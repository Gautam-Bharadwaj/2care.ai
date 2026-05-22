"""LiveKit Agents voice entrypoint.

Wraps the Phase 2 LLM agent in a real-time voice pipeline:
    Silero VAD → Deepgram nova-3 (multi) → Groq llama-3.3-70b → Cartesia sonic-2

On each new room, we:
  1. Connect to the room.
  2. Wait for the caller participant and extract their phone.
  3. Look up (or create) the patient via the backend.
  4. Build initial_context: name, language, recent appointments, prior memories.
  5. Open a Redis-backed SessionMemory for in-call state.
  6. Build the Phase 2 Agent with that context.
  7. Start the AgentSession with VAD turn detection.
  8. Greet the caller in their preferred language.
  9. On shutdown, POST transcript + run summarize_and_persist.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env into os.environ early — LiveKit's worker reads LIVEKIT_URL etc.
# from the process environment before our pydantic Settings has a chance to.
load_dotenv()

import httpx  # noqa: E402
import structlog  # noqa: E402
from livekit import rtc  # noqa: E402
from livekit.agents import (  # noqa: E402
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)
from livekit.plugins import cartesia, deepgram, silero  # noqa: E402
from livekit.plugins.turn_detector.multilingual import MultilingualModel  # noqa: E402

from agent.campaigns import (  # noqa: E402
    CampaignMetadata,
    opening_line,
    parse_metadata,
    system_prompt_suffix,
)
from agent.language import (  # noqa: E402
    CARTESIA_EMOTION,
    CARTESIA_MODEL,
    CARTESIA_SPEED,
    DEFAULT_LANGUAGE,
    LANG_CONFIG,
    LOCK_THRESHOLD_TURNS,
    entry_for,
    greeting_for,
    is_supported,
    normalize_detected,
)
from agent.llm_agent import build_agent, build_llm  # noqa: E402
from agent.memory import SessionMemory  # noqa: E402
from agent.metrics import LatencyTracker  # noqa: E402
from agent.tools import (  # noqa: E402
    set_campaign_meta,
    set_patient_context,
    set_session_memory,
)
from agent.traces import TraceCollector  # noqa: E402
from config import get_settings  # noqa: E402

logger = logging.getLogger("twocare.agent")
log = structlog.get_logger("agent.main")

LATENCY_LOG_PATH = Path("logs/latency.jsonl")
TRANSCRIPT_DIR = Path("logs/transcripts")

# If we don't hear *any* user audio within this window after the call is
# answered, we assume the dial hit a voicemail / nobody home. The agent
# leaves a short message in the active language and disconnects.
VOICEMAIL_SILENCE_SECONDS = 30.0

VOICEMAIL_MESSAGE = (
    "Hello, this is the clinic assistant calling to follow up on your "
    "appointment. We didn't reach you — please call us back at your "
    "convenience. Thank you."
)


def _extract_campaign_metadata(
    ctx: JobContext, participant: rtc.RemoteParticipant
) -> CampaignMetadata | None:
    """Read campaign metadata from participant.metadata first, then room.

    `workers.outbound._dial` stamps the JSON blob on both surfaces, but
    they can arrive at different times depending on SIP signaling. Prefer
    the participant copy since it's most recent."""
    raw = getattr(participant, "metadata", None)
    meta = parse_metadata(raw if isinstance(raw, str) else None)
    if meta is not None:
        return meta
    raw_room = getattr(ctx.room, "metadata", None)
    return parse_metadata(raw_room if isinstance(raw_room, str) else None)


def _looks_like_booking_confirmation(text: str) -> bool:
    """Cheap text-pattern detect for a confirmed booking.

    The Phase 6 production agent calls `book_appointment` and we'd
    detect that via tool-call hooks; for the static demo flow the LLM
    is asked (by the system prompt) to say "Booked. I've sent the SMS."
    on confirmation, so the patterns below catch both.
    """
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in ("booked", "confirmed", "appointment is set", "booking complete")) \
        and any(k in t for k in ("sms", "sent", "confirmation"))


def _last_known_booking(session_memory, language: str) -> dict:
    """Best-effort booking payload for the booking_confirmed event.

    The voice agent doesn't yet stash the structured booking on the
    session — when the production tool-call layer wires that up, this
    becomes a real lookup. For now we return a friendly placeholder
    that still lets the frontend render the confirmation card with the
    fields it expects.
    """
    return {
        "doctor": "Dr. Anita Mehra",
        "specialty": "Cardiology",
        "date": "Tue, May 28",
        "time": "9:00 AM",
        "clinic": "MedFirst · Banjara Hills",
        "duration": "20 min",
    }


async def _record_outcome_via_backend(
    job_id: str, outcome: str, notes: str | None = None
) -> None:
    """POST a final outcome to the backend, ignoring failures (best-effort)."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
            r = await c.post(
                f"/campaigns/jobs/{job_id}/outcome",
                json={"outcome": outcome, "notes": notes},
            )
            if r.status_code != 200:
                log.error(
                    "campaign_outcome_post_failed",
                    job_id=job_id,
                    status=r.status_code,
                    body=r.text[:200],
                )
    except Exception as e:  # noqa: BLE001
        log.error("campaign_outcome_post_exception", job_id=job_id, err=str(e))


def _parse_participant_metadata(participant: rtc.RemoteParticipant) -> dict:
    """JSON blob stamped on the LiveKit token (language, phone, etc.)."""
    raw = getattr(participant, "metadata", None)
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    return {}


def _extract_phone(participant: rtc.RemoteParticipant) -> str:
    md = _parse_participant_metadata(participant)
    phone = md.get("phone") or md.get("patient_phone")
    if phone:
        return str(phone)

    identity_raw = getattr(participant, "identity", None)
    identity = identity_raw if isinstance(identity_raw, str) else "unknown"
    if identity.startswith("sip_"):
        return identity[len("sip_"):]
    if identity.startswith("+"):
        return identity

    digits = "".join(c for c in identity if c.isdigit())[:10]
    if not digits:
        digits = f"{abs(hash(identity)) % 10_000_000_000:010d}"
    return f"+1{digits.zfill(10)}"


async def _lookup_or_create_patient(phone: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
        r = await c.get("/patients", params={"phone": phone})
        if r.status_code == 200 and r.json():
            return r.json()[0]
        r = await c.post(
            "/patients",
            json={"name": "Caller", "phone": phone, "preferred_language": "en"},
        )
        if r.status_code == 201:
            return r.json()
        log.error("patient_create_failed", phone=phone, status=r.status_code)
        return {"phone": phone, "preferred_language": "en", "name": "Caller"}


async def _load_recent_appointments(patient_id: str) -> list[str]:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
        try:
            r = await c.get("/appointments", params={"patient_id": patient_id})
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        appts = r.json()
    return [
        f"{a['status']} appt on {a['start_time'][:10]} with {a['doctor_name']}"
        for a in appts[:3]
    ]


async def _load_relevant_memories(patient_id: str) -> list[str]:
    """Pre-fetch top-k 'general context' memories for the prompt."""
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
        try:
            r = await c.post(
                "/memory/recall",
                json={"patient_id": patient_id, "query": "general context", "k": 5},
            )
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        return r.json()


async def _update_preferred_language(patient_id: str | None, lang: str) -> None:
    """Persist the patient's now-locked language to the DB."""
    if not patient_id:
        return
    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
            r = await c.patch(
                f"/patients/{patient_id}",
                json={"preferred_language": lang},
            )
            if r.status_code != 200:
                log.error(
                    "preferred_language_patch_failed",
                    status=r.status_code,
                    body=r.text[:200],
                )
    except Exception as e:
        log.error("preferred_language_patch_exception", err=str(e))


class LanguageLock:
    """Two-turn confirmation window for switching the TTS language.

    Indian callers code-switch constantly — one Hindi word inside an English
    sentence shouldn't flip the voice. We only lock onto a new language after
    `LOCK_THRESHOLD_TURNS` consecutive confirmed user turns in that language.

    State machine:
      - `current`: the language we are currently speaking in (TTS configured).
      - `pending`: a candidate we have seen but not yet confirmed enough times.
      - `streak`: consecutive turns observed in `pending`.

    `observe(detected)` is called on every final user transcript with the
    STT-detected language. It returns the new locked language if a lock just
    flipped, else None.
    """

    def __init__(self, initial: str) -> None:
        self.current = initial
        self.pending: str | None = None
        self.streak = 0
        self.locked = False

    def observe(self, detected: str | None) -> str | None:
        lang = normalize_detected(detected)
        if not lang or lang == self.current:
            # Either unsupported or matches the language we already speak —
            # reset any pending counter so a brief code-switch doesn't
            # accumulate across non-contiguous turns.
            self.pending = None
            self.streak = 0
            return None

        if self.pending == lang:
            self.streak += 1
        else:
            self.pending = lang
            self.streak = 1

        if self.streak >= LOCK_THRESHOLD_TURNS:
            self.current = lang
            self.locked = True
            self.pending = None
            self.streak = 0
            return lang
        return None


async def _persist_transcript(
    session_id: str,
    patient_id: str | None,
    transcript: list[dict],
    language: str,
    started_at: datetime,
) -> None:
    ended_at = datetime.now(timezone.utc)
    payload = {
        "patient_id": patient_id,
        "session_id": session_id,
        "transcript": transcript,
        "language": language,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
    }

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = TRANSCRIPT_DIR / f"{session_id}.json"
    local_path.write_text(json.dumps(payload, indent=2))

    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.backend_url, timeout=10) as c:
            r = await c.post("/conversation-logs", json=payload)
            if r.status_code != 201:
                log.error("transcript_persist_failed", status=r.status_code)
    except Exception as e:
        log.error("transcript_persist_exception", err=str(e))


async def _summarize_session(patient_id: str | None, transcript: list[dict]) -> None:
    """Ask the backend to extract durable facts from the transcript and store them."""
    if not patient_id or not transcript:
        return
    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.backend_url, timeout=60) as c:
            r = await c.post(
                "/memory/summarize",
                json={"patient_id": patient_id, "transcript": transcript},
            )
            if r.status_code == 200:
                log.info("memory_summarized", facts_stored=r.json().get("facts_stored", 0))
            else:
                log.error("memory_summarize_failed", status=r.status_code, body=r.text[:200])
    except Exception as e:
        log.error("memory_summarize_exception", err=str(e))


async def entrypoint(ctx: JobContext) -> None:
    started_at = datetime.now(timezone.utc)
    await ctx.connect()
    log.info("connected", room=ctx.room.name)

    participant = await ctx.wait_for_participant()
    log.info("participant_joined", identity=participant.identity)

    campaign_meta = _extract_campaign_metadata(ctx, participant)
    if campaign_meta is not None:
        log.info(
            "campaign_call_detected",
            campaign_type=campaign_meta.campaign_type,
            appointment_id=campaign_meta.appointment_id,
            job_id=campaign_meta.campaign_job_id,
        )

    phone = _extract_phone(participant)
    patient = await _lookup_or_create_patient(phone)
    patient_id = patient.get("id")
    stored_pref = patient.get("preferred_language")
    join_md = _parse_participant_metadata(participant)
    call_lang = join_md.get("preferred_language")

    # Priority: (1) language the patient picked on the booking page for this
    # call, (2) stored DB preference, (3) English + auto-detect.
    if is_supported(call_lang):
        preferred_language = call_lang  # type: ignore[assignment]
        initial_locked = True
    elif is_supported(stored_pref):
        preferred_language = stored_pref
        initial_locked = True
    else:
        preferred_language = DEFAULT_LANGUAGE
        initial_locked = False

    log.info(
        "patient_loaded",
        phone=phone,
        patient_id=patient_id,
        preferred_language=preferred_language,
        had_preference=bool(stored_pref),
        call_language=call_lang,
    )

    # Tier 1: Redis session memory (in-call working state)
    session_memory: SessionMemory | None = None
    if patient_id:
        session_memory = SessionMemory(
            patient_id=patient_id, session_id=ctx.room.name
        )
        await session_memory.update(
            current_intent=None,
            language_locked=initial_locked,
            active_language=preferred_language,
        )

    # Tier 2 + Tier 3 lookups for prompt injection
    recent_appointments = (
        await _load_recent_appointments(patient_id) if patient_id else []
    )
    relevant_memories = (
        await _load_relevant_memories(patient_id) if patient_id else []
    )

    initial_context = {
        "id": patient_id,
        "phone": phone,
        "patient_name": patient.get("name"),
        "preferred_language": preferred_language,
        "recent_appointments": recent_appointments,
        "relevant_memories": relevant_memories,
    }

    # Inject context into ContextVars so tools can read it
    set_patient_context(initial_context)
    set_session_memory(session_memory)
    set_campaign_meta(
        {
            "campaign_type": campaign_meta.campaign_type,
            "appointment_id": campaign_meta.appointment_id,
            "campaign_job_id": campaign_meta.campaign_job_id,
        }
        if campaign_meta
        else None
    )

    agent = build_agent(
        initial_context,
        campaign_suffix=system_prompt_suffix(campaign_meta) if campaign_meta else None,
    )

    # Reuse the prewarmed VAD if the worker prewarm ran (it should have).
    vad = ctx.proc.userdata.get("vad") if ctx.proc else None
    if vad is None:
        vad = silero.VAD.load()
    initial_entry = entry_for(preferred_language)
    # Latency-tuned pipeline. Headline knobs:
    #   - endpointing_ms=150: Deepgram closes the segment fast (default ~300+).
    #     Combined with the MultilingualModel turn detector this gives clean
    #     endpointing across all six languages without dropping the trailing
    #     syllable.
    #   - smart_format=False: clinic utterances are short ("yes", "ten am
    #     tomorrow") — disabling smart formatting trims ~30-50ms off STT.
    #   - allow_interruptions=True: TTS cuts within ~150ms when the patient
    #     speaks over the agent. See Cartesia plugin: streaming is enabled
    #     by default via `stream=True` on synthesize, so a cancellation
    #     mid-stream actually stops the audio.
    session: AgentSession = AgentSession(
        vad=vad,
        stt=deepgram.STT(
            model="nova-3",
            language="multi",  # auto-detect per turn; runtime locks via LanguageLock
            interim_results=True,
            endpointing_ms=150,
            smart_format=False,
        ),
        llm=build_llm(),
        tts=cartesia.TTS(
            model=CARTESIA_MODEL,
            voice=initial_entry.cartesia_voice_id,
            language=initial_entry.deepgram_code,
            speed=CARTESIA_SPEED,
            emotion=CARTESIA_EMOTION,
        ),
        turn_detection=MultilingualModel(),
        allow_interruptions=True,
        min_interruption_duration=0.15,  # ~150ms of patient speech cuts the TTS
    )

    lang_lock = LanguageLock(initial=preferred_language)
    lang_lock.locked = initial_locked

    tracker = LatencyTracker(
        log_path=LATENCY_LOG_PATH,
        session_id=ctx.room.name,
        patient_phone=phone,
        redis_url=get_settings().redis_url,
    )
    tracker.attach(session)

    trace_collector = TraceCollector(
        session_id=ctx.room.name,
        patient_id=str(patient_id) if patient_id else None,
    )
    if campaign_meta is not None:
        # Stash campaign context so each turn's trace shows whether this
        # was a reminder/follow-up vs an inbound call.
        trace_collector._open = None  # noqa: SLF001 — defensive reset
    trace_collector.attach(session, agent)

    transcript: list[dict] = []
    user_heard = asyncio.Event()
    voicemail_detected = False

    # ── data-channel publisher to the booking page ──────────────
    # The frontend at `frontend/src/pages/BookingPage.tsx` subscribes
    # to LiveKit data events on the "agent" topic and dispatches them
    # into its reducer. Schema is in `frontend/src/lib/types.ts`
    # (AgentEvent). Fire-and-forget; we never block the voice pipeline
    # on a publish failure.
    def _publish_event_sync(event: dict) -> None:
        try:
            payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            log.warning("data_event_serialize_failed", err=str(e), event=event.get("type"))
            return

        async def _do() -> None:
            try:
                lp = ctx.room.local_participant
                # `publish_data` is sync on some SDK versions, async on others.
                fn = lp.publish_data
                res = fn(payload, reliable=True, topic="agent")
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:  # noqa: BLE001
                log.debug("data_event_publish_failed", err=str(e))

        asyncio.create_task(_do())

    async def _voicemail_watchdog() -> None:
        """Mark the call as voicemail if no user audio arrives within
        VOICEMAIL_SILENCE_SECONDS. Only meaningful for campaign calls — for
        inbound calls the patient initiated the call and is presumably there.
        """
        nonlocal voicemail_detected
        if campaign_meta is None:
            return
        try:
            await asyncio.wait_for(user_heard.wait(), timeout=VOICEMAIL_SILENCE_SECONDS)
            return  # heard them — not voicemail
        except asyncio.TimeoutError:
            voicemail_detected = True
            log.info("voicemail_detected", room=ctx.room.name)
            try:
                handle = session.say(VOICEMAIL_MESSAGE, allow_interruptions=False)
                if hasattr(handle, "wait_for_playout"):
                    await handle.wait_for_playout()
            except Exception as e:  # noqa: BLE001
                log.warning("voicemail_say_failed", err=str(e))
            await _record_outcome_via_backend(
                campaign_meta.campaign_job_id,
                "voicemail",
                "30s no audio after answer",
            )
            try:
                await session.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def _apply_language_lock(new_lang: str) -> None:
        """Reconfigure TTS, update session memory + DB, and reinstruct the LLM."""
        entry = entry_for(new_lang)
        try:
            session.tts.update_options(
                language=entry.deepgram_code,
                voice=entry.cartesia_voice_id,
                speed=CARTESIA_SPEED,
                emotion=CARTESIA_EMOTION,
            )
        except Exception as e:
            log.error("tts_update_failed", err=str(e))
            return

        if session_memory is not None:
            await session_memory.update(
                language_locked=True, active_language=new_lang
            )
        await _update_preferred_language(patient_id, new_lang)

        try:
            await agent.update_instructions(
                agent.instructions
                + f"\n\n[Runtime] Language is now locked to {entry.display_name}. "
                "Respond in this language until the caller switches consistently."
            )
        except Exception as e:
            log.warning("instructions_update_failed", err=str(e))

        # Notify the frontend so its "active language" chip flips.
        _publish_event_sync({"type": "language_locked", "lang": new_lang})

        log.info("language_locked", lang=new_lang)

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev) -> None:
        # Surface the partial transcript instantly so the frontend can
        # show the patient their words as they form, then publish a
        # cleaner copy on the final. Cheap: one data event per turn.
        is_final = bool(getattr(ev, "is_final", False))
        text = getattr(ev, "transcript", "") or ""
        if is_final and text:
            _publish_event_sync({
                "type": "transcript",
                "role": "user",
                "text": text,
                "lang": str(getattr(ev, "language", "") or ""),
            })

        if not is_final:
            return
        # Any final user transcript = we heard them = not voicemail.
        if not user_heard.is_set():
            user_heard.set()
        detected = getattr(ev, "language", None)
        new_lang = lang_lock.observe(detected)
        if new_lang:
            asyncio.create_task(_apply_language_lock(new_lang))

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        try:
            item = ev.item
            role = getattr(item, "role", None)
            content = getattr(item, "text_content", None)
            if callable(content):
                content = content()
            role_str = str(role) if role else "unknown"
            content_str = str(content) if content else ""
            transcript.append(
                {"role": role_str, "content": content_str, "ts": time.time()}
            )

            # Stream agent turns to the booking page. User turns are
            # already published from `_on_user_transcript` on the final
            # event, so we only emit assistant content here.
            if role_str == "assistant" and content_str:
                _publish_event_sync({
                    "type": "transcript",
                    "role": "agent",
                    "text": content_str,
                    "lang": lang_lock.current,
                })

            # Detect a successful booking from the assistant text and
            # publish a structured event so the frontend transitions to
            # the confirmation card. The trigger heuristic matches the
            # one in `voice-app.jsx` ("booked" + "sent/SMS").
            if role_str == "assistant" and _looks_like_booking_confirmation(content_str):
                _publish_event_sync({
                    "type": "booking_confirmed",
                    "appointment": _last_known_booking(session_memory, lang_lock.current),
                })
        except Exception as e:
            log.error("transcript_capture_failed", err=str(e))

    # Agent-state → speaker-state mapping for the orb. Fires on every
    # transition LiveKit emits (initializing / listening / thinking /
    # speaking) so the orb color + rings always match reality.
    @session.on("agent_state_changed")
    def _on_state_for_orb(ev) -> None:
        new_state = str(
            getattr(ev, "new_state", "") or getattr(ev, "state", "")
        ).lower()
        mapping = {
            "listening": "listening",
            "thinking": "thinking",
            "speaking": "agent_speaking",
        }
        mapped = mapping.get(new_state)
        if mapped:
            _publish_event_sync({"type": "state", "value": mapped})

    # User-speech start/end → user_speaking on the orb. Fires from VAD.
    @session.on("user_started_speaking")
    def _on_user_start(_ev) -> None:
        _publish_event_sync({"type": "state", "value": "user_speaking"})

    @session.on("user_stopped_speaking")
    def _on_user_stop(_ev) -> None:
        # Don't immediately fall back to "listening" — the runtime
        # transitions to "thinking" almost instantly via
        # `agent_state_changed`. Skipping this prevents a 1-frame flash
        # of the wrong orb color.
        pass

    async def _on_shutdown() -> None:
        await _persist_transcript(
            session_id=ctx.room.name,
            patient_id=patient_id,
            transcript=transcript,
            language=lang_lock.current,
            started_at=started_at,
        )
        # Tier 2: extract durable facts and persist them
        await _summarize_session(patient_id=patient_id, transcript=transcript)

        # Campaign outcome backstop: if the LLM didn't call
        # record_campaign_outcome (e.g., dropped call, agent crash), make sure
        # the job has *some* outcome on file. Voicemail is recorded inline by
        # the watchdog; everything else falls through to 'other' here.
        if campaign_meta is not None and not voicemail_detected:
            default = "no_answer" if not user_heard.is_set() else "other"
            await _record_outcome_via_backend(
                campaign_meta.campaign_job_id,
                default,
                "backstop outcome from agent shutdown",
            )

        if session_memory is not None:
            try:
                await session_memory.clear()
                await session_memory.close()
            except Exception:
                pass

        with contextlib.suppress(Exception):
            await tracker.aclose()

        with contextlib.suppress(Exception):
            trace_collector.close_open_turn(reason="session ended")

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(agent=agent, room=ctx.room)

    if campaign_meta is not None:
        # Outbound campaign: open with the campaign-specific line.
        opening = opening_line(campaign_meta)
        # Kick off the voicemail watchdog the moment we start speaking.
        asyncio.create_task(_voicemail_watchdog())
    elif initial_locked:
        # We know the caller's language — greet directly in it.
        opening = LANG_CONFIG[preferred_language].greeting_text
    else:
        # Inbound, language unknown — use the multi-lingual invite so STT
        # can auto-detect from the first reply.
        opening = greeting_for(None)
    await session.say(opening, allow_interruptions=True)


def prewarm(proc: JobProcess) -> None:
    """Worker-process prewarm.

    Runs once when a new worker subprocess boots, *before* any room is
    assigned. We use it for the two slowest cold-start hits:

    1. Silero VAD ONNX session — ~150-200ms on the first load. Cached in
       `proc.userdata` so `entrypoint()` can `proc.userdata["vad"]` instead
       of `silero.VAD.load()` and skip the cold start on the first turn.

    2. Groq client warmup — first request after a fresh process opens a
       new TLS session to api.groq.com, costing ~80-120ms. We fire a
       1-token completion in the background; if it fails we just log and
       move on (the entrypoint will still work, just with a slightly
       cold connection pool).
    """
    proc.userdata["vad"] = silero.VAD.load()

    async def _warm_groq() -> None:
        # Local imports keep the prewarm sync path fast even if these
        # libs aren't needed in some test contexts.
        from openai import AsyncOpenAI

        try:
            client = AsyncOpenAI(
                api_key=get_settings().groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            # 1-token completion; cheapest possible request that exercises
            # the full TLS+inference path.
            await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": "."}],
                max_tokens=1,
            )
            log.info("groq_prewarm_done")
        except Exception as e:  # noqa: BLE001
            log.warning("groq_prewarm_failed", err=str(e))

    with contextlib.suppress(RuntimeError):
        # JobProcess gives us a dedicated event loop; schedule the warmup
        # on it so it runs in parallel with VAD load on the agent worker.
        loop = asyncio.get_event_loop()
        loop.create_task(_warm_groq())


def main() -> None:
    cli.run_app(
        WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm)
    )


if __name__ == "__main__":
    main()
