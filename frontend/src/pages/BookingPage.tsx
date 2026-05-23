import { useEffect, useReducer, useRef, useState } from "react";
import { CalendarClock, Mic, PhoneOff, RotateCcw, Waves } from "lucide-react";

import CallOrb from "@/components/CallOrb";
import HeroSection from "@/components/HeroSection";
import LanguagePicker from "@/components/LanguagePicker";
import LiveTranscript from "@/components/LiveTranscript";
import ConfirmationCard from "@/components/ConfirmationCard";
import SchedulingCard from "@/components/SchedulingCard";
import StateIndicator from "@/components/StateIndicator";
import {
  EMPTY_SCHEDULING,
  finalizeBooking,
  schedulingVisible,
  updateSchedulingFromAgent,
  type SchedulingState,
} from "@/lib/bookingExtract";
import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";
import { STATE_LABELS, type LangCode, LANGUAGES } from "@/lib/languages";
import type { AgentEvent, BookingState, SpeakerState, Turn } from "@/lib/types";
import { startCall as startLiveKitCall, type ActiveCall } from "@/lib/livekit";
import { startCall as startServerVoiceCall, type ActiveVoiceCall } from "@/lib/voice";

const API_BASE = import.meta.env.VITE_API_BASE || "";
/** Set `VITE_USE_LIVEKIT=true` only when LiveKit + agent worker are running. */
const PREFER_LIVEKIT = import.meta.env.VITE_USE_LIVEKIT === "true";

type AnyCall = ActiveCall | ActiveVoiceCall;

// ────────────────────────────────────────────────────────────────
// State machine
// ────────────────────────────────────────────────────────────────

interface State {
  page: BookingState;
  language: LangCode;
  activeLanguage: LangCode;
  languageLocked: boolean;
  turns: Turn[];
  scheduling: SchedulingState;
}

type Action =
  | { type: "PICK_LANGUAGE"; lang: LangCode }
  | { type: "START_CONNECTING" }
  | { type: "CONNECTED" }
  | { type: "AGENT_EVENT"; event: AgentEvent }
  | { type: "DISCONNECTED" }
  | { type: "ERROR"; message: string }
  | { type: "RESET" };

const INITIAL_STATE: State = {
  page: { kind: "idle" },
  language: "en",
  activeLanguage: "en",
  languageLocked: false,
  turns: [],
  scheduling: EMPTY_SCHEDULING,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "PICK_LANGUAGE":
      // Picking a language while the call is live shouldn't reset
      // anything — it's only meaningful from the hero screen, where
      // we use it to mint the LiveKit token.
      if (state.page.kind !== "idle") return state;
      return { ...state, language: action.lang, activeLanguage: action.lang };

    case "START_CONNECTING":
      return {
        ...state,
        page: { kind: "connecting" },
        turns: [],
        scheduling: EMPTY_SCHEDULING,
      };

    case "CONNECTED":
      // Default the orb to "agent_speaking" — the agent always greets
      // first. The real state updates a moment later via AGENT_EVENT.
      return { ...state, page: { kind: "active", speakerState: "agent_speaking" } };

    case "AGENT_EVENT":
      return applyAgentEvent(state, action.event);

    case "DISCONNECTED":
      // From `connecting` (the user cancelled before the call really
      // started) → bounce straight back to idle. No "Call ended" card
      // since no call actually happened.
      if (state.page.kind === "connecting") {
        return { ...state, page: { kind: "idle" } };
      }
      // Already settled — don't overwrite a confirmed booking or an
      // explicit error message when the engine's late disconnect lands.
      if (
        state.page.kind === "ended_success" ||
        state.page.kind === "ended_no_booking" ||
        state.page.kind === "error" ||
        state.page.kind === "idle"
      ) {
        return state;
      }
      return { ...state, page: { kind: "ended_no_booking" } };

    case "ERROR":
      return { ...state, page: { kind: "error", message: action.message } };

    case "RESET":
      return {
        ...INITIAL_STATE,
        language: state.language,
        activeLanguage: state.language,
        languageLocked: false,
        scheduling: EMPTY_SCHEDULING,
      };
  }
}

function applyAgentEvent(state: State, ev: AgentEvent): State {
  // Ignore stray events after End call — the engine may still drain
  // in-flight STT/LLM/TTS or LiveKit may send one last data packet.
  if (state.page.kind !== "active") {
    return state;
  }

  switch (ev.type) {
    case "state": {
      if (state.page.kind !== "active") return state;
      return { ...state, page: { kind: "active", speakerState: ev.value } };
    }
    case "transcript": {
      const turn: Turn = { role: ev.role, text: ev.text, ts: Date.now() };
      const scheduling =
        ev.role === "agent"
          ? updateSchedulingFromAgent(state.scheduling, ev.text)
          : state.scheduling;
      return {
        ...state,
        turns: [...state.turns, turn].slice(-12),
        scheduling,
      };
    }
    case "language_locked": {
      // Only flip if the locked language is one we recognize on the
      // frontend. Unknown codes leave the active language unchanged.
      const known = LANGUAGES.find((l) => l.code === ev.lang);
      if (!known) return state;
      return { ...state, activeLanguage: ev.lang, languageLocked: true };
    }
    case "booking_confirmed": {
      const booking = finalizeBooking(state.scheduling.booking, ev.appointment);
      return {
        ...state,
        page: { kind: "ended_success", booking },
        scheduling: { status: "confirmed", booking, step: 3 },
      };
    }
    case "error":
      return { ...state, page: { kind: "error", message: ev.message } };
  }
}

// ────────────────────────────────────────────────────────────────
// Page
// ────────────────────────────────────────────────────────────────

export default function BookingPage() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  // 0..1 audio amplitude from the mic, used to scale the orb during
  // user_speaking. Kept outside the reducer so a 60fps update stream
  // doesn't force the whole page state machine through every frame.
  const [amp, setAmp] = useState(0);
  const callRef = useRef<AnyCall | null>(null);

  // Tear down the room when the page unmounts (route change, refresh).
  useEffect(() => {
    return () => {
      callRef.current?.disconnect().catch(() => {
        /* ignore — page is unmounting */
      });
      callRef.current = null;
    };
  }, []);

  const onStart = async () => {
    dispatch({ type: "START_CONNECTING" });
    const handlers = {
      language: state.language,
      onConnected: () => dispatch({ type: "CONNECTED" }),
      onDisconnected: () => dispatch({ type: "DISCONNECTED" }),
      onEvent: (ev: AgentEvent) => dispatch({ type: "AGENT_EVENT", event: ev }),
      onAmplitude: (a: number) => setAmp(a),
      onError: (msg: string) => dispatch({ type: "ERROR", message: msg }),
    };

    try {
      let call: AnyCall | null = null;

      if (PREFER_LIVEKIT) {
        const tokenOk = await canMintLiveKitToken(state.language);
        if (tokenOk) {
          try {
            call = await startLiveKitCall({
              language: handlers.language,
              onConnected: handlers.onConnected,
              onDisconnected: handlers.onDisconnected,
              onEvent: handlers.onEvent,
              onError: handlers.onError,
            });
          } catch (lkErr) {
            console.warn("[booking] LiveKit connect failed, using server voice", lkErr);
          }
        }
      }

      if (!call) {
        call = await startServerVoiceCall(handlers);
      }

      callRef.current = call;
    } catch (err) {
      const msg = String((err as Error)?.message || "");
      if (msg === "MIC_DENIED" || msg === "MIC_UNSUPPORTED" || msg === "MIC_INSECURE") {
        dispatch({
          type: "ERROR",
          message:
            msg === "MIC_INSECURE"
              ? "Microphone needs HTTPS or localhost — use http://localhost:5174"
              : msg === "MIC_UNSUPPORTED"
                ? "This browser cannot access the microphone. Try Chrome or Edge."
                : "Microphone access was blocked. Allow it in your browser settings and try again.",
        });
      } else {
        dispatch({ type: "ERROR", message: msg || "Something went wrong." });
      }
    }
  };

  // End / Cancel button. Idempotent across every page state — safe to
  // press even if there's no call in flight or if we're already
  // partway through a disconnect. We also dispatch DISCONNECTED *here*
  // (not just from the engine's onDisconnected hook) so the UI flips
  // immediately even if the engine's cleanup is still draining.
  const onEnd = async () => {
    const inFlight = callRef.current;
    callRef.current = null;
    setAmp(0);
    // Stop mic + TTS + in-flight API calls first, then flip UI state.
    if (inFlight) {
      await inFlight.disconnect().catch(() => undefined);
    }
    dispatch({ type: "DISCONNECTED" });
  };

  const onReset = async () => {
    const inFlight = callRef.current;
    callRef.current = null;
    setAmp(0);
    dispatch({ type: "RESET" });
    if (inFlight) {
      inFlight.disconnect().catch(() => undefined);
    }
  };

  const showScheduling =
    state.page.kind === "active" && schedulingVisible(state.scheduling);

  return (
    <main className="flex-1 w-full max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8 lg:py-10">
        <div
          className={cn(
            "w-full flex flex-col gap-6 sm:gap-8",
            state.page.kind === "active" && "max-w-[960px] mx-auto",
            state.page.kind === "idle" && "max-w-none",
            (state.page.kind === "connecting" ||
              state.page.kind === "ended_success" ||
              state.page.kind === "ended_no_booking" ||
              state.page.kind === "error") &&
              "max-w-[480px] mx-auto"
          )}
        >
          {state.page.kind === "idle" && (
            <IdleBlock
              language={state.language}
              onLang={(l) => dispatch({ type: "PICK_LANGUAGE", lang: l })}
              onStart={onStart}
            />
          )}

          {state.page.kind === "connecting" && <ConnectingBlock onCancel={onEnd} />}

          {state.page.kind === "active" && (
            <ActiveBlock
              speakerState={state.page.speakerState}
              activeLanguage={state.activeLanguage}
              languageLocked={state.languageLocked}
              turns={state.turns}
              scheduling={state.scheduling}
              showScheduling={showScheduling}
              amp={amp}
              onEnd={onEnd}
            />
          )}

          {state.page.kind === "ended_success" && (
            <ConfirmationCard
              booking={state.page.booking}
              language={state.activeLanguage}
              onDone={onReset}
            />
          )}

          {state.page.kind === "ended_no_booking" && (
            <EndedBlock onRetry={onReset} />
          )}

          {state.page.kind === "error" && (
            <ErrorBlock message={state.page.message} onRetry={onReset} />
          )}
        </div>
    </main>
  );
}

/** Probe whether LiveKit is configured before attempting WebRTC. */
async function canMintLiveKitToken(language: LangCode): Promise<boolean> {
  try {
    const r = await fetch(`${API_BASE}/voice/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language, patient_phone: null }),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// ────────────────────────────────────────────────────────────────
// Sub-blocks
// ────────────────────────────────────────────────────────────────

function IdleBlock({
  language,
  onLang,
  onStart,
}: {
  language: LangCode;
  onLang: (l: LangCode) => void;
  onStart: () => void;
}) {
  return (
    <div className="w-full grid gap-8 lg:gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(280px,380px)] lg:items-start">
      <div className="flex flex-col gap-6 sm:gap-7 min-w-0">
        <HeroSection />
        <LanguagePicker value={language} onChange={onLang} columns={5} />
      </div>

      <div className="w-full glass-card flex flex-col overflow-hidden lg:sticky lg:top-6">
        <div className="px-5 sm:px-6 pt-6 pb-2 text-center">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-3">
            <Waves className="h-5 w-5" strokeWidth={1.8} aria-hidden />
          </span>
          <h2 className="text-lg font-semibold tracking-tight text-slate-900">
            Start a live booking
          </h2>
          <p className="mt-1.5 text-sm text-slate-600 leading-snug">
            Tap the orb and speak in the language you chose on the left.
          </p>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-6 sm:py-8 px-4">
          <p className="text-[11px] font-semibold text-primary/70 uppercase tracking-wider">
            Tap to begin
          </p>
          <CallOrb state="idle" onClick={onStart} />
        </div>

        <ul className="px-5 sm:px-6 py-4 border-t border-slate-100/90 grid gap-1.5 text-[11px] text-slate-500">
          <li className="inline-flex items-center gap-2">
            <Mic className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
            Microphone required
          </li>
          <li className="inline-flex items-center gap-2">
            <CalendarClock className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
            Slots update live during the call
          </li>
        </ul>
      </div>
    </div>
  );
}

function ConnectingBlock({ onCancel }: { onCancel: () => void }) {
  return (
    <div className="w-full glass-card p-8 sm:p-10 flex flex-col items-center gap-6 animate-line-fade">
      <div className="text-center space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Connecting…
        </h1>
        <p className="text-sm text-slate-600">
          Opening a secure voice line to your assistant.
        </p>
      </div>
      <CallOrb state="thinking" disabled />
      <button
        type="button"
        onClick={onCancel}
        className="text-sm font-medium text-slate-600 hover:text-destructive transition-colors px-5 py-2.5 rounded-full border border-slate-200 bg-white hover:border-destructive/30"
      >
        Cancel
      </button>
    </div>
  );
}

function ActiveBlock({
  speakerState,
  activeLanguage,
  languageLocked,
  turns,
  scheduling,
  showScheduling,
  amp,
  onEnd,
}: {
  speakerState: SpeakerState;
  activeLanguage: LangCode;
  languageLocked: boolean;
  turns: Turn[];
  scheduling: SchedulingState;
  showScheduling: boolean;
  amp: number;
  onEnd: () => void;
}) {
  const lang = LANGUAGES.find((l) => l.code === activeLanguage) || LANGUAGES[0];
  return (
    <div className="w-full space-y-4 animate-line-fade">
      <div className="glass-card grid grid-cols-1 sm:grid-cols-[1fr_auto_auto] sm:items-center gap-3 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="live-pulse-dot shrink-0" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-900 truncate">Live call</p>
            <p className="text-xs text-slate-500 truncate">
              {lang.native}
              {languageLocked ? " · locked" : ""}
            </p>
          </div>
        </div>
        <div className="sm:justify-self-center">
          <StateIndicator state={speakerState} language={activeLanguage} />
        </div>
        <button
          type="button"
          onClick={onEnd}
          className="inline-flex items-center justify-center gap-1.5 text-sm font-medium text-destructive hover:bg-destructive/5 transition-colors px-3.5 py-2 rounded-lg border border-destructive/20 bg-white sm:justify-self-end w-full sm:w-auto"
        >
          <PhoneOff className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
          End call
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,340px)] lg:items-start">
        <div className="glass-card p-5 sm:p-6 flex flex-col items-center gap-5">
          <CallOrb state={speakerState} amp={amp} onClick={onEnd} />
          <p className="text-xs text-center text-slate-500 max-w-xs">
            Tap the orb or use End call when you are finished.
          </p>

          <div className="w-full lg:hidden">
            {showScheduling && (
              <SchedulingCard scheduling={scheduling} language={activeLanguage} />
            )}
          </div>

          <LiveTranscript turns={turns} />
        </div>

        <aside className="hidden lg:block sticky top-6">
          {showScheduling ? (
            <SchedulingCard scheduling={scheduling} language={activeLanguage} />
          ) : (
            <div className="glass-card border-dashed p-6 text-center text-sm text-slate-500">
              <CalendarClock
                className="h-8 w-8 mx-auto mb-3 text-primary/40"
                strokeWidth={1.5}
                aria-hidden
              />
              <p className="font-semibold text-slate-800 mb-1">Appointment panel</p>
              <p className="leading-relaxed">
                Doctor, time, and clinic details appear here as the assistant finds a slot.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function EndedBlock({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="w-full glass-card p-8 sm:p-10 flex flex-col items-center gap-6 text-center animate-line-fade">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Call ended</h1>
        <p className="text-sm text-slate-600 max-w-sm">
          No appointment was booked this time. Start a new call whenever you are ready.
        </p>
      </div>
      <Button size="lg" onClick={onRetry} className="gap-2 rounded-full px-8 shadow-md shadow-primary/15">
        <RotateCcw className="h-4 w-4" strokeWidth={1.8} aria-hidden />
        Start a new call
      </Button>
    </div>
  );
}

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="w-full glass-card p-8 sm:p-10 flex flex-col items-center gap-6 text-center animate-line-fade">
      <div className="space-y-1.5 max-w-md">
        <h1 className="text-2xl font-semibold tracking-tight text-destructive">
          Couldn&apos;t start the call
        </h1>
        <p className="text-sm text-slate-600">{message}</p>
      </div>
      <Button size="lg" onClick={onRetry} className="rounded-full px-8">
        Try again
      </Button>
    </div>
  );
}

// Silence unused-import warning when STATE_LABELS isn't referenced at the
// top level — actually used inside StateIndicator. Re-export so future
// consumers can pull from this barrel if they want.
export { STATE_LABELS };
