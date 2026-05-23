<p align="center">
  <img src="assets/logo.svg" alt="2Care.ai Logo" width="250" />
</p>

# Real-Time Multilingual Voice AI Agent: Clinical Appointment Booking

A voice AI agent that handles inbound appointment booking and outbound reminders across **ten Indian languages** (English, Hindi, Bengali, Tamil, Telugu, Kannada, Malayalam, Marathi, Gujarati, and Punjabi) — the workload that drives 2care.ai's *"80% reduction in manual calls"* and revenue-leakage prevention for hospitals. Built as an end-to-end real-time pipeline targeting **&lt; 450 ms** speech-to-speech latency (p50), with atomic slot booking, three-tier persistent memory, and per-turn reasoning traces.

![2Care.ai Web Console & Dashboard](assets/dashboard.png)

> **Before submission:** capture the booking page at `http://localhost:5174` and save as `assets/dashboard.png`. Optionally export the Mermaid diagram below to `assets/architecture.png`.

## Demo

**Loom walkthrough:** [Watch Video Walkthrough](https://drive.google.com/file/d/1ZHurA4DOicn5wiNWGKi2Hpi1AIGP8pCa/view?usp=sharing)

A 3-minute walkthrough of the live demo and architecture overview.

***

## 1. Architecture

The system is a modular async pipeline in **Python 3.11** (FastAPI, LiveKit Agents, Celery) and **TypeScript** (Vite + React). Real-time calls run on **LiveKit**; the browser can also use a **server-mediated fallback** (mic → Deepgram STT → Groq LLM → Cartesia TTS) when LiveKit is off.


```mermaid
flowchart TD
    subgraph Client["Patient client"]
        FE["Vite + React booking page<br/>Language picker · call orb · live transcript"]
    end

    subgraph Media["LiveKit Cloud"]
        LK["WebRTC room · SIP trunk · agent dispatch"]
    end

    subgraph Agent["twocare-agent (LiveKit Agents worker)"]
        VAD["Silero VAD"]
        STT["Deepgram nova-3<br/>multi + per-lang lock"]
        TD["Multilingual turn detector"]
        LLM["Groq llama-3.3-70b<br/>function tools"]
        TTS["Cartesia sonic-3<br/>per-language voice ID"]
        VAD --> STT --> TD --> LLM --> TTS
    end

    subgraph Backend["twocare-backend (FastAPI)"]
        API["REST: patients · slots · appointments<br/>/voice · /traces · /memory · /campaigns"]
        VoiceFB["/voice/stt · /voice/chat · /voice/tts<br/>browser fallback path"]
    end

    subgraph Data["Data & jobs"]
        Redis[("Redis<br/>session memory · latency ring · Celery broker")]
        PG[("PostgreSQL + pgvector<br/>patients · appointments · memories · traces")]
        Worker["Celery worker + beat<br/>outbound reminders"]
    end

    Twilio["Twilio PSTN"] --> LK
    FE <-->|WebRTC preferred| LK
    FE -->|HTTP fallback| VoiceFB
    LK <--> Agent
    Agent -->|HTTP tools| API
    API --> Redis
    API --> PG
    Worker --> Redis
    Worker --> API
    Worker --> LK

    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px;
    classDef media fill:#f0fdf4,stroke:#16a34a,stroke-width:2px;
    classDef agent fill:#faf5ff,stroke:#7e22ce,stroke-width:2px;
    classDef backend fill:#fff7ed,stroke:#ea580c,stroke-width:2px;
    classDef data fill:#fcf7f2,stroke:#ca8a04,stroke-width:2px;

    class FE client;
    class LK media;
    class VAD,STT,TD,LLM,TTS agent;
    class API,VoiceFB backend;
    class Redis,PG,Worker data;

    style Client fill:none,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
    style Media fill:none,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
    style Agent fill:none,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
    style Backend fill:none,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
    style Data fill:none,stroke:#888,stroke-width:1px,stroke-dasharray: 5 5
```

### Core modules

| Module | Role |
| --- | --- |
| **`agent/main.py`** | LiveKit job entrypoint: connect room, resolve patient by phone, pre-warm context, run `AgentSession` (VAD → STT → LLM → TTS), greet, persist transcript + memory on shutdown. |
| **`agent/llm_agent.py`** | System prompt, Groq LLM, `function_tool` wiring for booking and memory. |
| **`agent/tools.py`** | HTTP wrappers to backend: list slots, book / cancel / reschedule, recall memory, campaign outcomes. |
| **`agent/language.py`** | Per-language Deepgram codes, Cartesia voice IDs, greetings; language-lock threshold (2 turns). |
| **`agent/memory.py`** | Tier-1 Redis session hash (`session:{patient_id}:{session_id}`, 30 min TTL). |
| **`agent/metrics.py`** | Per-turn latency tracker (EOU, STT, LLM TTFT, TTS TTFB) → Redis ring + `logs/latency.jsonl`. |
| **`backend/routers/voice.py`** | LiveKit token minting; browser STT/TTS/chat fallback; script validation for Indic replies. |
| **`backend/services/memory_store.py`** | Tier-2 pgvector semantic memory + end-of-call summarization. |
| **`workers/`** | Celery tasks: outbound reminder dialing via LiveKit SIP. |
| **`frontend/`** | Patient booking UI (`/`), operator debug traces (`/debug/trace/:id`), ops dashboard (`/debug/ops`). |

Full deploy topology (Fly.io, Neon, Upstash, Twilio): [`docs/deploy.md`](docs/deploy.md).

***

## 2. Memory design (three tiers)

Context is split by durability and retrieval style. The same fact should not live in two tiers.

| Tier | Store | Lifetime | What lives here |
| --- | --- | --- | --- |
| **1. Session** | Redis `session:{patient_id}:{session_id}` | **30 min** sliding TTL | `pending_slot_id`, `language_locked`, `current_intent`, in-call flags |
| **2. Semantic** | Postgres `memories` + **pgvector(1536)** | Indefinite | Free-text preferences and notes; `recall_memory(query)` |
| **3. Structured** | Postgres `patients`, `appointments`, `doctor_slots`, … | Indefinite | Phone lookup, `preferred_language`, exact slot UUIDs |

**Session start:** look up patient → last appointments → top semantic recalls → inject into system prompt → open `SessionMemory`.

**Session end:** transcript → `/memory/summarize` → up to 5 embedded facts (Tier 2). Tier 1 key is cleared.

**Patient isolation:** Tier-1 keys are namespaced by `patient_id`; Tier-2 SQL always filters `WHERE patient_id = :id` before vector sort. Covered in `tests/test_memory.py`.

***

## 3. Latency breakdown

Headline metric: **`speech_end_to_first_audio_ms`** — from end-of-utterance to first TTS audio. Target: **p50 &lt; 450 ms**.

| Stage | Target p50 | Target p95 |
| --- | --- | --- |
| STT finalization (Deepgram) | 120 ms | 250 ms |
| LLM first token (Groq 70b) | 200 ms | 400 ms |
| TTS first audio (Cartesia sonic-3) | 130 ms | 280 ms |
| **Total (headline)** | **450 ms** | **930 ms** |

Live calls: `agent/metrics.py` subscribes to LiveKit metrics → Redis `latency:last100` → `GET /metrics/latency`.

### Per-turn sequence (LiveKit path)

```mermaid
sequenceDiagram
    autonumber
    participant U as Patient
    participant FE as React frontend
    participant LK as LiveKit
    participant AG as Agent worker
    participant DG as Deepgram
    participant GQ as Groq
    participant CT as Cartesia
    participant BE as FastAPI backend

    U->>FE: speaks (mic)
    FE->>LK: WebRTC audio
    LK->>AG: media stream
    Note over AG: Silero VAD + MultilingualModel EOU
    AG->>DG: streaming STT (nova-3)
    DG-->>AG: transcript + language hint
    AG->>BE: tools if needed (slots, book, memory)
    BE-->>AG: JSON results
    AG->>GQ: LLM + function tools
    GQ-->>AG: reply text
    AG->>CT: sonic-3 TTS
    CT-->>AG: audio stream
    AG->>LK: agent audio
    LK-->>FE: playback
    FE-->>U: hears response
```

### Measured numbers

Reproduce with the synthetic provider benchmark (no Twilio minutes):

```bash
uv run python scripts/bench_latency.py --turns 30 --lang en
uv run python scripts/bench_latency.py --turns 30 --lang hi
```

After real calls, inspect live aggregates:

```bash
curl -s http://localhost:8000/metrics/latency | jq
```

Methodology, optimizations (`endpointing_ms=150`, prompt trim, Silero/Groq prewarm), and India region caveats: [`docs/latency-report.md`](docs/latency-report.md).

***

## 4. Multilingual handling

* **10 languages** configured in `agent/language.py`: `en`, `hi`, `bn`, `ta`, `te`, `kn`, `ml`, `mr`, `gu`, `pa`.
* **STT:** Deepgram **nova-3** with `language="multi"` on the LiveKit path; browser fallback pins the patient's selected language for accuracy.
* **TTS:** **Cartesia sonic-3** with a dedicated `cartesia_voice_id` per language (swap voices at [play.cartesia.ai](https://play.cartesia.ai/)).
* **Lock strategy:** If `patients.preferred_language` is set, start there. Otherwise multilingual greeting → after **two consecutive** turns in another supported language, lock TTS + PATCH `preferred_language` (reduces false switches on code-mixing).
* **Script guard:** `backend/routers/voice.py` validates Indic script in LLM replies and retries on drift (e.g. Devanagari leaking into Gujarati).

Automated language check:

```bash
uv run twocare-backend &   # wait until ready
uv run python scripts/verify_languages.py
# writes docs/language-verification-report.md
```

| Code | Language | Sample greeting |
| --- | --- | --- |
| `en` | English | *Hi! I can help you book an appointment…* |
| `hi` | हिन्दी | *नमस्ते! मैं अपॉइंटमेंट बुक करने में मदद कर रही हूँ…* |
| `ta` | தமிழ் | *வணக்கம்! நான் சந்திப்பு பதிவு செய்ய உதவுகிறேன்…* |
| `te` | తెలుగు | *నమస్కారం! అపాయింట్‌మెంట్ బుక్ చేయడంలో నేను సహాయం చేస్తాను…* |
| … | _(see `agent/language.py` for all ten)_ | |

***

## 5. Failure modes

| Failure | Behaviour | Recovery |
| --- | --- | --- |
| Deepgram / provider timeout | Turn fails; agent may apologize | User retries utterance; STT reconnects on next LiveKit session |
| Groq rate limit (browser path) | Falls back to `llama-3.1-8b-instant` | Logged in `backend/routers/voice.py` |
| LiveKit worker dies mid-call | Call drops | Scale agent count ≥ 2; LiveKit redispatches new rooms |
| Redis unavailable | Session memory / latency ring degraded | Postgres + tools still work; redeploy Redis |
| Slot conflict on book | Tool returns alternatives inline | LLM reads alternatives in one turn |
| Missing API keys | `503` on `/voice/*` or agent fails to start | Fill `.env` (see Setup) |

***

## 6. Production readiness & compliance

Internship-scale prototype; for a hospital production deploy:

* **HIPAA / DPDP:** persist `REASONING_TRACE` events (already captured per turn), TLS everywhere, audio retention policy, consent capture, PII-redacted logs.
* **EHR:** extend `book_appointment` / `cancel_appointment` to emit FHIR `Appointment` webhooks.
* **Telephony:** Twilio elastic SIP → LiveKit (see [`docs/twilio-livekit-setup.md`](docs/twilio-livekit-setup.md)).
* **Auth:** lock down `/voice/token` and admin routes; rate limits for clinic integrations.

Deploy runbook: [`docs/deploy.md`](docs/deploy.md).

***

## 7. Tradeoffs & known limitations

### Shipped tradeoffs

* **LiveKit vs. custom WebSocket gateway:** LiveKit handles media, SIP, and dispatch; faster to ship telephony than a bespoke PCM gateway.
* **Groq 70b for all turns:** Simpler than routing greetings to 8b; avoids mis-routed tool calls on short "yes" confirmations.
* **Two-turn language lock:** Tolerates Hinglish code-switching better than locking on the first Hindi word in an English sentence.
* **Browser fallback:** Chunked mic upload + REST STT/TTS when `VITE_USE_LIVEKIT` is not set — higher latency than WebRTC but zero LiveKit setup for local demos.

### Known limitations

* **Kannada / Gujarati voices:** Some Cartesia voice IDs should be ear-checked on [play.cartesia.ai](https://play.cartesia.ai/) before a demo.
* **Groq region:** US-only today adds ~100–140 ms RTT from India (see latency report).
* **Outbound at scale:** Throughput gated by Twilio concurrent SIP limits (~10 default).

***

## 8. Setup

### A. Environment configuration

```bash
uv sync
cp .env.example .env
```

Fill in at minimum:

```env
DATABASE_URL=postgresql+asyncpg://twocare:twocare@localhost:5432/twocare
REDIS_URL=redis://localhost:6379/0

DEEPGRAM_API_KEY=
CARTESIA_API_KEY=
GROQ_API_KEY=
OPENAI_API_KEY=          # memory embeddings

LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

Optional: Twilio + `LIVEKIT_SIP_TRUNK_ID` for PSTN inbound/outbound.

### B. Database & backend services

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis
uv run alembic upgrade head
uv run twocare-seed                    # optional sample doctors/slots

uv run twocare-backend                 # terminal 1 — :8000
uv run twocare-agent dev               # terminal 2 — LiveKit worker
uv run twocare-worker                  # terminal 3 — Celery (reminders)
```

### C. Frontend

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5174
```

| Route | Purpose |
| --- | --- |
| `/` | Patient booking — language picker + call orb + live transcript |
| `/debug/trace/:sessionId` | Per-turn reasoning trace |
| `/debug/ops` | `/healthz` + `/metrics/latency` |

**LiveKit in the browser:** set `VITE_USE_LIVEKIT=true` in `frontend/.env` when the agent worker is running. Otherwise the page uses the HTTP fallback (`/voice/stt`, `/voice/chat`, `/voice/tts`).

### D. Try it

* **Browser:** open `http://localhost:5174` → pick language → tap orb → allow microphone.
* **LiveKit Playground:** mint a token via backend `/voice/token`; paste URL + token at [agents-playground.livekit.io](https://agents-playground.livekit.io).
* **Phone (deployed):** Twilio → LiveKit SIP → agent (see deploy doc).

### E. Latency & language benchmarks

```bash
uv run python scripts/bench_latency.py --turns 30 --lang en
uv run python scripts/bench_latency.py --turns 30 --lang hi --json /tmp/bench.json
curl -s http://localhost:8000/metrics/latency | jq
uv run python scripts/verify_languages.py
```

***

## Stack

Python 3.11 · FastAPI · LiveKit Agents · Deepgram STT · Groq LLM · Cartesia TTS · Twilio SIP · Postgres (pgvector) · Redis · Celery · Vite · React 18 · TypeScript · Tailwind · shadcn/ui

## License

_Add your license here._
