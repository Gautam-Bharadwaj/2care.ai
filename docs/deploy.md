# Deploy runbook — Fly.io

Single-region (Mumbai / `bom`) deploy of all three apps, with Neon for
Postgres + pgvector, Upstash for Redis, LiveKit Cloud for media, and
Twilio for SIP. End-to-end run cost on free tiers + Fly's `shared-cpu-1x`
machines is roughly **$0–$5 / month** at demo traffic; everything
linearly scales out from there.

## Architecture

```
                       Twilio elastic SIP trunk
                                │ (SIP)
                                ▼
                   ┌─────────────────────────┐
                   │   LiveKit Cloud (bom)   │   ← agent worker registers
                   └──────────┬──────────────┘     here over WSS, outbound
                              │ media + jobs
                              ▼
   ┌──────────────────────────┴──────────────┐
   │  twocare-agent  (Fly, count=2, no port) │
   │  ──────────────────────────────────────  │
   │  Deepgram nova-3 → Groq llama-3.3-70b   │
   │  → Cartesia sonic-2.  Hooks LiveKit's   │
   │  metrics_collected for per-turn traces. │
   └──────────────────┬─────────────┬────────┘
                      │             │
        ┌─── HTTP ────┘             └──── HTTP ───┐
        ▼                                         ▼
  ┌──────────────────────┐               ┌──────────────────────┐
  │  twocare-backend     │               │  twocare-worker      │
  │  (Fly, FastAPI)      │←──── Redis ──→│  (Fly, Celery)       │
  │  /traces /healthz    │   (Upstash)   │  reminder + followup │
  │  /metrics/latency    │               │  beat: daily 09:00 IST│
  │  /campaigns/* …      │               │  outbound dial via   │
  │       │              │               │  LiveKit SIP         │
  └───────┴───── SQL ────┴───── Neon ────┘                      │
              pgvector (1536d embeddings)                       │
                                                                ▼
                                                       LiveKit Cloud
                                                       (outbound SIP)
```

## One-time setup

### 1. Provision Postgres (Neon)

[neon.tech](https://neon.tech) → New project → region **AWS ap-south-1
(Mumbai)** to colocate with Fly `bom`. Pick Postgres 16 (matches the
docker-compose dev DB and `pgvector/pgvector:pg16`). Neon enables
pgvector out of the box; verify:

```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
-- if missing:
CREATE EXTENSION vector;
```

Copy the **pooled** `DATABASE_URL` (port 6543) — Fly machines have
short-lived connections, the pooler is non-negotiable. Convert the
scheme to `postgresql+asyncpg://`:

```
postgresql+asyncpg://USER:PASS@ep-xxx-pooler.ap-south-1.aws.neon.tech/twocare?sslmode=require
```

### 2. Provision Redis (Upstash)

[upstash.com](https://upstash.com) → Create Database → **AWS Mumbai**
region. Pick TLS-enabled. Copy the `rediss://` URL (note the double `s`
— Upstash requires TLS).

### 3. LiveKit Cloud

[cloud.livekit.io](https://cloud.livekit.io) → new project → region
**Mumbai**. From the project dashboard:

- API key + secret → `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`
- `wss://` URL → `LIVEKIT_URL`
- Configure the outbound Twilio SIP trunk per
  [`docs/twilio-livekit-setup.md`](twilio-livekit-setup.md) → get a
  trunk ID like `ST_xxxxxx` → `LIVEKIT_SIP_TRUNK_ID`

### 4. Twilio

Buy an Indian or US number with Voice capability. Elastic SIP trunk
configured per `docs/twilio-livekit-setup.md` (LiveKit handles SIP auth;
Twilio just terminates PSTN).

- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`

### 5. Create the three Fly apps

```bash
flyctl auth login
flyctl apps create twocare-backend
flyctl apps create twocare-agent
flyctl apps create twocare-worker
```

## Set Fly secrets

All three apps share the same provider keys; Fly secrets are per-app so
set them on each. The backend additionally needs `OPENAI_API_KEY` (for
memory embeddings) and `LIVEKIT_*` (so it can mint room tokens if needed).

```bash
# Shared (every app)
for APP in twocare-backend twocare-agent twocare-worker; do
  flyctl secrets set --app "$APP" \
    DATABASE_URL='postgresql+asyncpg://…@…neon.tech/twocare?sslmode=require' \
    REDIS_URL='rediss://default:…@…upstash.io:6379' \
    LIVEKIT_URL='wss://twocare-xxxxxxxx.livekit.cloud' \
    LIVEKIT_API_KEY='APIxxxx' \
    LIVEKIT_API_SECRET='secretxxxx'
done

# Agent + worker — voice provider keys
for APP in twocare-agent twocare-worker; do
  flyctl secrets set --app "$APP" \
    DEEPGRAM_API_KEY='…' \
    CARTESIA_API_KEY='…' \
    GROQ_API_KEY='…' \
    LIVEKIT_SIP_TRUNK_ID='ST_xxxxxx' \
    TWILIO_ACCOUNT_SID='ACxxx' \
    TWILIO_AUTH_TOKEN='…' \
    TWILIO_PHONE_NUMBER='+15555550100'
done

# Backend — embedding key + same SIP trunk
flyctl secrets set --app twocare-backend \
    OPENAI_API_KEY='sk-…' \
    LIVEKIT_SIP_TRUNK_ID='ST_xxxxxx'
```

`flyctl secrets set` triggers a deploy automatically; pass `--stage` to
batch multiple sets before redeploying.

## First deploy

The three apps deploy independently. The backend's
`release_command = "alembic upgrade head"` runs migrations against the
production DB *before* the new machine takes traffic, so it's safe to
deploy backend first then agent/worker.

```bash
fly deploy --config infra/fly.backend.toml --dockerfile infra/Dockerfile.backend --remote-only
fly deploy --config infra/fly.agent.toml   --dockerfile infra/Dockerfile.agent   --remote-only
fly deploy --config infra/fly.worker.toml  --dockerfile infra/Dockerfile.worker  --remote-only
```

Or `gh workflow run deploy.yml` to use the CI pipeline.

## Scale & HA

```bash
# Backend — 1 is enough for demo traffic; bump if /traces gets hot.
fly scale count 1 --app twocare-backend

# Agent — keep TWO for HA. If one dies mid-call, that call drops, but
# the *next* call routes to the survivor while Fly respawns the dead one.
fly scale count 2 --app twocare-agent

# Worker — depends on outbound campaign volume. Twilio elastic SIP
# typically allows ~10 concurrent calls per trunk on a starter account;
# 1 worker machine at CELERY_WORKER_CONCURRENCY=8 saturates that.
fly scale count 1 --process-group worker --app twocare-worker
# Beat MUST stay at 1. Two beats = duplicate sweeps = duplicate calls.
fly scale count 1 --process-group beat   --app twocare-worker
```

## Seed the prod DB

```bash
# Run the seed inside a backend machine so DATABASE_URL is resolved
# from the secret store, not your laptop env.
fly ssh console --app twocare-backend -C 'twocare-seed'
```

`twocare-seed` (see `backend/db/seed.py`) creates the demo doctor set
(Mehra, Rao, Iyer, Pillai, Bose, Joshi, Chandran) with bilingual
language tags and ~14 slots per doctor over the next 7 days.

## Verify the deployed system

```bash
# 1. Health check — DB + Redis + provider freshness in one JSON blob.
curl -s https://twocare-backend.fly.dev/healthz | jq

# 2. Confirm the agent is registered with LiveKit. The agent app has no
#    public port, so we check its logs instead:
fly logs --app twocare-agent | grep "registered_worker\|connected"

# 3. Make a test call. Either:
#    a. Dial the Twilio number → LiveKit inbound trunk → agent picks up
#    b. Open the LiveKit Agents Playground with your project URL,
#       create a room named "playground-test", the agent dispatch rule
#       routes you to a registered worker.
#    https://agents-playground.livekit.io  (set LiveKit URL + token)

# 4. Pull latency for the call you just made:
curl -s https://twocare-backend.fly.dev/metrics/latency | jq '.speech_end_to_first_audio_ms'
```

## Troubleshooting

| Symptom | Where to look | Fix |
| ------- | ------------- | --- |
| `/healthz` returns `degraded` with `db.ok=false` | Neon connection limit hit | Switch URL to the **pooler** (port 6543) — Fly machines open many short connections. |
| `/healthz` returns `degraded` with `redis.ok=false` | Upstash URL missing `rediss://` (no TLS) | Re-set the secret with `rediss://`. |
| Agent registers but never picks up calls | Wrong LiveKit URL in agent secrets | `fly logs --app twocare-agent` will show `livekit_url=…` on boot. |
| Outbound campaign call rings but agent never greets | `LIVEKIT_SIP_TRUNK_ID` empty in the worker app | `fly secrets list --app twocare-worker` and re-set. |
| Beat duplicates the daily sweep | Beat process scaled above 1 | `fly scale count 1 --process-group beat --app twocare-worker`. |
| First call after deploy is slow (>700ms p50) | Prewarm hadn't completed yet | The agent's `prewarm_fnc` fires a 1-token Groq call and loads Silero. Wait 5s after `fly deploy` finishes before calling. |

## Rollback

```bash
fly releases --app twocare-backend
fly deploy --app twocare-backend --image registry.fly.io/twocare-backend@sha256:<previous-digest>
```

Or `fly releases rollback --app twocare-backend` to one-shot the last
known-good release. The agent and worker rollback the same way; they
have no DB schema dependencies, so any order is fine.

## Secrets reference

The full env set across all three apps:

| Secret | Backend | Agent | Worker | Notes |
| --- | :---: | :---: | :---: | --- |
| `DATABASE_URL`        | ✓ | ✓ | ✓ | Neon pooled URL with `?sslmode=require` |
| `REDIS_URL`           | ✓ | ✓ | ✓ | Upstash `rediss://` |
| `LIVEKIT_URL`         | ✓ | ✓ | ✓ | `wss://*.livekit.cloud` |
| `LIVEKIT_API_KEY`     | ✓ | ✓ | ✓ | |
| `LIVEKIT_API_SECRET`  | ✓ | ✓ | ✓ | |
| `LIVEKIT_SIP_TRUNK_ID`| ✓ |   | ✓ | Outbound trunk. Agent doesn't dial; worker does. |
| `DEEPGRAM_API_KEY`    |   | ✓ |   | STT — agent only |
| `CARTESIA_API_KEY`    |   | ✓ |   | TTS — agent only |
| `GROQ_API_KEY`        |   | ✓ |   | LLM — agent only (worker uses it indirectly via the agent) |
| `OPENAI_API_KEY`      | ✓ |   |   | Memory embeddings (text-embedding-3-small) — backend only |
| `TWILIO_ACCOUNT_SID`  | ✓ |   | ✓ | |
| `TWILIO_AUTH_TOKEN`   | ✓ |   | ✓ | |
| `TWILIO_PHONE_NUMBER` | ✓ |   | ✓ | E.164 number that fronts the SIP trunk |

The Fly secret for the GitHub Actions deploy job:

| Secret | Where | Notes |
| --- | --- | --- |
| `FLY_API_TOKEN` | GitHub repo settings → Secrets and variables → Actions | `fly tokens create deploy -x 999999h` |
