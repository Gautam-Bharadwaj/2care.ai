# Twilio ↔ LiveKit SIP Setup (Outbound Campaigns)

This document walks through wiring a Twilio SIP trunk into LiveKit so
the agent can place outbound calls (reminders, follow-ups). Inbound
calls reuse the same trunk on the inbound dispatch rule.

Reference: <https://docs.livekit.io/sip>

The Twilio account SID / auth token in `.env` are only used for SMS
fallback and account-level metadata; **SIP credentials live on LiveKit**.

## 1. Buy a Twilio phone number

1. <https://console.twilio.com/us1/develop/phone-numbers/manage/incoming>
2. Buy a number with **Voice** capability in the country you'll call from
   (for India coverage, a US Twilio number plus international dialing is
   the cheapest path; for Indian numbers, regulatory KYC is required).
3. Note: `TWILIO_PHONE_NUMBER` in `.env` should match this E.164 number.

## 2. Create a Twilio Elastic SIP Trunk

1. Go to *Elastic SIP Trunking → Trunks → Create new trunk*.
2. Friendly name: `2careai-outbound`.
3. Under **Termination**:
   - Termination URI: pick a subdomain like `2careai.pstn.twilio.com`.
     This is the SIP host LiveKit will hit when *placing* outbound calls.
   - Authentication: create a **Credential List** with a single
     username/password. Save these — LiveKit needs them.
   - Set an **IP Access Control List** containing LiveKit's egress IPs
     (see <https://docs.livekit.io/sip/trunk-twilio/#configure-twilio>
     for the current list).
4. Under **Origination** (for inbound from Twilio → LiveKit):
   - Origination URI: `sip:<your-livekit-sip-host>:5060;transport=tls`
     (your LiveKit project page shows the SIP URI).
5. Under **Numbers**, assign your purchased number to this trunk.

## 3. Create the LiveKit outbound trunk

LiveKit needs to know how to dial out through Twilio. Use the CLI:

```bash
lk sip outbound create --address 2careai.pstn.twilio.com \
    --number +15551234567 \
    --auth-username '<twilio-trunk-username>' \
    --auth-password '<twilio-trunk-password>' \
    --name 2careai-outbound
```

This prints a trunk ID like `ST_xxxxxxxxxxxxxx`. **Copy it into
`.env`** as `LIVEKIT_SIP_TRUNK_ID`.

If you prefer the API: `POST /twirp/livekit.SIP/CreateSIPOutboundTrunk`
with the `SIPOutboundTrunkInfo` payload (see
[livekit_sip.proto](https://github.com/livekit/protocol/blob/main/protobufs/livekit_sip.proto)).

## 4. Create the LiveKit inbound trunk + dispatch rule

So inbound Twilio calls (e.g., a patient calling back) route into a
LiveKit room and trigger the agent:

```bash
lk sip inbound create \
    --numbers +15551234567 \
    --name 2careai-inbound

lk sip dispatch create \
    --trunk <inbound-trunk-id> \
    --room-prefix call- \
    --agent-name twocare-agent
```

The `--agent-name` must match the `WorkerOptions(agent_name=...)`
your `agent/main.py` registers — that's how LiveKit knows which agent
worker to dispatch.

## 5. Environment variables

All of the following should be set in `.env`:

```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_SIP_TRUNK_ID=ST_xxxxxxxxxxxxxx        # outbound trunk
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+15551234567
```

`LIVEKIT_SIP_TRUNK_ID` is the **outbound** trunk; the inbound trunk
needs no env var because LiveKit routes those automatically through
the dispatch rule.

## 6. Verifying the path

Pick a test number you own, then:

```bash
uv run python - <<'PY'
import asyncio, uuid
from workers.outbound import outbound_call
print(outbound_call("+15555550100", f"verify-{uuid.uuid4().hex[:8]}"))
PY
```

You should see the phone ring within ~2s. If you hear "all circuits
busy" or get a 4xx from LiveKit:

- 403 / `auth failed` → Twilio Credential List username/password
  mismatch in the LiveKit outbound trunk.
- 503 from Twilio → IP ACL on the trunk is missing LiveKit's egress
  IPs.
- LiveKit `no_trunks_available` → `LIVEKIT_SIP_TRUNK_ID` not set or
  points at a non-existent trunk.

## 7. Campaign metadata flow

When the Celery worker places a campaign call it passes
`participant_metadata` on `CreateSIPParticipantRequest`:

```json
{
  "campaign_type": "reminder" | "followup",
  "appointment_id": "<uuid>",
  "campaign_job_id": "<uuid>"
}
```

The agent reads this from `participant.metadata` on join (see
`agent/main.py::_extract_campaign_metadata`) and uses a
campaign-specific opening line + records the outcome to
`CampaignOutcome` on call end.
