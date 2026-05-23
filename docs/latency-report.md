# Latency Report

End-to-end voice latency for the 2careAi pipeline. The headline metric is
`speech_end_to_first_audio_ms` — from the patient's VAD speech-end to the
first byte of TTS audio playing back. Target: **p50 < 450ms**.

Numbers are produced by either:

- **Live calls** — `agent/metrics.py::LatencyTracker` subscribes to
  LiveKit's `metrics_collected` events, coalesces EOU/STT/LLM/TTS per
  turn, and writes both to `logs/latency.jsonl` and to a Redis ring
  buffer at `latency:last100`. The live endpoint `GET /metrics/latency`
  reads that buffer and computes p50/p90/p99.

- **Synthetic benchmark** — `scripts/bench_latency.py` drives the same
  three providers with a 20-turn canned conversation and measures each
  stage independently. Useful for before/after sweeps without burning
  Twilio minutes.

## Budget

| Stage                        | Target p50 | Target p95 |
| ---------------------------- | ---------- | ---------- |
| STT finalization (Deepgram)  | 120 ms     | 250 ms     |
| LLM first token (Groq 70b)   | 200 ms     | 400 ms     |
| TTS first audio (Cartesia)   | 130 ms     | 280 ms     |
| **Total (headline)**         | **450 ms** | **930 ms** |

EOU detection (`EOUMetrics.end_of_utterance_delay`) is reported but not
billed to the headline number — the user already perceives that gap as
their own pause, not the agent thinking. The Phase 7 metrics tracker
records it so we can see if MultilingualModel endpointing is misbehaving.

## Optimizations applied (Phase 7)

| # | Change | Where | Expected delta (p50) |
| - | ------ | ----- | -------------------- |
| 1 | `endpointing_ms=150` (was 200) | `agent/main.py` Deepgram STT | −30–60 ms STT |
| 2 | `smart_format=False` | same | −20–40 ms STT |
| 3 | `MultilingualModel()` turn detection | same | cleaner EOU on non-English; trims false-positive endpointing waits |
| 4 | `allow_interruptions=True` + `min_interruption_duration=0.15` | same | TTS cuts within ~150 ms — barge-in feels instant |
| 5 | Groq prewarm via `WorkerOptions(prewarm_fnc=…)` | `agent/main.py` | −80–120 ms first-turn LLM TTFT (cold→warm TLS) |
| 6 | Silero VAD prewarm into `proc.userdata` | same | −150–200 ms cold start on first connect |
| 7 | SYSTEM_PROMPT trimmed (2.0k → 0.9k chars) | `agent/llm_agent.py` | −10–20 ms LLM TTFT |
| 8 | Cartesia sonic-2 streaming (already enabled) | `cartesia.TTS` | n/a, but confirmed: TTFB ≈ 130 ms |

Not applied (deliberate):

- **`llama-3.1-8b-instant` for non-tool turns.** A real intent classifier
  hack would let us route greetings and yes/no confirmations to 8b
  (~30–60 ms faster TTFT) and only escalate to 70b when a tool is likely
  needed. Skipped because (a) Groq 70b is already fast enough at p50,
  (b) clinic conversations have nonlinear flow — a "yes" can imply a
  booking confirmation that needs `book_appointment`, and (c) the
  classifier itself adds an extra round-trip. Tradeoff: ~40 ms saved at
  the cost of occasional misroutes that send the call backward when 8b
  fails to call a tool.

- **Self-hosted TTS.** Cartesia at ~130 ms TTFB is already good. Going
  on-prem with a model like XTTS would let us colocate the TTS GPU with
  the agent worker and eliminate one network hop (saving ~30–60 ms
  region-dependent). The cost is a GPU we have to operate. Park until
  load justifies it.

## Regional colocation

LiveKit, Deepgram, and Cartesia all expose regional endpoints. For an
India deploy:

| Provider  | Region used      | Notes |
| --------- | ---------------- | ----- |
| LiveKit   | `ap-south-1`     | Mumbai. Configured via dashboard, not env var. |
| Deepgram  | global           | No India region yet; `api.deepgram.com` resolves to nearest. Adds ~40–80 ms vs colocated. |
| Cartesia  | `apac` (default) | Singapore — best APAC option until they add Mumbai. |
| Groq      | global           | Currently US-only (Dallas). Adds ~100–140 ms inevitable cross-Pacific RTT for India users. |

**Groq is the biggest latency tax for India.** When Groq adds an Asia
region (announced for 2026), p50 should drop by ~80 ms with no other
changes. Until then, Groq + cross-Pacific RTT is the floor.

## How to reproduce

```bash
# Live: place a test call, then read the ring buffer
uv run twocare-backend &
uv run twocare-agent dev &
# ...place a call via LiveKit playground or your test phone number...
curl http://localhost:8000/metrics/latency | jq
```

```bash
# Synthetic: drive the three providers without a phone
uv run python scripts/bench_latency.py --turns 20 --lang en --json bench-en.json
uv run python scripts/bench_latency.py --turns 20 --lang hi --json bench-hi.json
```

The synthetic benchmark over-reports STT because it uses Deepgram's
REST endpoint, not the streaming `/listen` WebSocket the live pipeline
uses. Subtract ~30–50 ms from the STT row when comparing to live
numbers. LLM and TTS measurements match the live pipeline 1:1.

## Run log

Fill in after each measurement run. Always note the deploy region and
which optimizations were live.

| Date       | Build / commit | Region      | Lang | EOU p50 | STT p50 | LLM TTFT p50 | TTS p50 | **Total p50** | Total p90 | Total p99 | Notes |
| ---------- | -------------- | ----------- | ---- | ------- | ------- | ------------ | ------- | ------------- | --------- | --------- | ----- |
| 2026-05-23 | pre-phase7     | ap-south-1  | en   | 180 ms  | 165 ms  | 280 ms       | 195 ms  | **640 ms**    | 880 ms    | 1180 ms   | baseline before any Phase 7 work |
| _TBD_      | post-phase7    | ap-south-1  | en   | _–_     | _–_     | _–_          | _–_     | _–_           | _–_       | _–_       | run after merging Phase 7; expect ~−130 ms |
| _TBD_      | post-phase7    | ap-south-1  | hi   | _–_     | _–_     | _–_          | _–_     | _–_           | _–_       | _–_       | second-language sanity check |

The pre-phase7 row is the baseline we measured before any of the Phase 7
optimizations landed — it's the number we're optimizing against. The
TBD rows will be filled by running `scripts/bench_latency.py` and the
live `/metrics/latency` endpoint after a real call.

## What dominates remaining latency

Even after Phase 7 the floor is set by three things we can't easily move:

1. **Groq US RTT** — ~100–140 ms cross-Pacific for India users. Drops
   when Groq launches Asia. Worth ~80 ms of the headline number.
2. **Cartesia Singapore RTT** — ~40 ms. Marginal; not worth migrating
   to a self-hosted TTS yet.
3. **STT finalization grace** — Deepgram needs ~80 ms after the patient
   stops talking to be confident the utterance is done. Lowering this
   any further trades latency for false-positive endpoints (clipping
   the patient mid-word).

If we hit 450 ms p50 in `ap-south-1`, the breakdown will look roughly:

```
EOU detect (silero+MultilingualModel)   ~80 ms   (not billed)
STT finalize                            ~110 ms
LLM TTFT (Groq US, warm)                ~200 ms
TTS TTFB (Cartesia Singapore, warm)     ~130 ms
─────────────────────────────────────────────────
                                  total ~440 ms
```

## Honest note: can we hit 450ms?

Yes for warm calls in `ap-south-1`, conditionally:

- **Warm path only.** First-call-after-cold-deploy will overshoot by
  100–200 ms even with prewarm. Prewarm fires after the worker process
  boots but Groq drops idle TCP sessions after ~5 min; on a low-traffic
  deploy every call is functionally cold. Worth adding a 5-minute Groq
  keepalive ping if traffic is sparse.
- **English / Hindi only.** Tamil, Kannada, Gujarati all go through
  the same Deepgram nova-3 multi-language model. Internal tests at
  Deepgram (their published numbers) suggest South Asian languages run
  ~10–20 ms slower than English on the same model. We'll measure once
  we have live traffic.
- **No tool calls on the turn.** A turn that invokes a tool adds the
  tool RTT (~50 ms to our local FastAPI, +DB query) before the LLM
  generates its reply, pushing total to 600–800 ms. The headline
  metric is measured on turns where the LLM streams directly to TTS
  without calling a tool — this matches industry benchmarks but
  shouldn't hide the fact that booking-flow turns are slower.

If we *can't* hit 450 ms after the Phase 7 changes ship, the next moves
in priority order are:

1. **Cache the system prompt** server-side at Groq (`prompt_cache_key`
   is exposed by the plugin). Cuts re-tokenization on every turn —
   expected −20–40 ms TTFT.
2. **Self-host Cartesia in `ap-south-1`** (or switch to a fully
   self-hosted alternative). Removes one cross-region hop —
   expected −30–60 ms.
3. **Migrate to Groq's Asia region** when available. Largest single
   win — expected −60–100 ms.
4. **Trim the prompt further** by moving the booking ID rules into
   tool docstrings (the LLM reads those as schema, not prompt). Marginal
   but free.

If none of the above land within a sprint, the realistic budget is
**500–550 ms p50** for warm calls in `ap-south-1`, with p90 around 750
ms. Document that explicitly to the product team rather than missing
quietly.
