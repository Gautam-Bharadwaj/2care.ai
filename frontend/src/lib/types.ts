import type { LangCode } from "./languages";

/** Top-level page state — drives which sub-component renders on `/`. */
export type BookingState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "active"; speakerState: SpeakerState }
  | { kind: "ended_success"; booking: Booking }
  | { kind: "ended_no_booking" }
  | { kind: "error"; message: string };

/** What the orb shows during an active call. */
export type SpeakerState =
  | "listening"        // waiting for the patient to speak
  | "user_speaking"    // mic capturing user audio (coral)
  | "thinking"         // LLM / tools running
  | "agent_speaking";  // TTS playing

/** Transcript turn — one line in the LiveTranscript pane. */
export interface Turn {
  role: "user" | "agent";
  text: string;
  /** Local timestamp, used for the fade-up ordering. */
  ts: number;
}

/** Confirmation card payload — emitted by the agent on book_appointment. */
export interface Booking {
  doctor: string;
  specialty?: string;
  date: string;   // formatted, e.g. "Tue, May 28"
  time: string;   // formatted, e.g. "9:00 AM"
  clinic?: string;
  duration?: string;
  /** Optional ISO timestamp — used to generate the .ics file
   *  and the locale-formatted "Tuesday, 28 May at 9:00 AM" header. */
  startsAtIso?: string;
  endsAtIso?: string;
  /** Short reference ID shown at the bottom of the confirmation card.
   *  Generated client-side if the server didn't provide one. */
  referenceId?: string;
}

/**
 * Data-channel events from the agent worker. The agent publishes one of
 * these shapes whenever the conversation moves through a meaningful
 * state. Keep this synced with `agent/main.py::publish_event`.
 */
export type AgentEvent =
  | { type: "state"; value: SpeakerState }
  | { type: "transcript"; role: "user" | "agent"; text: string; lang?: string }
  | { type: "booking_confirmed"; appointment: Booking }
  | { type: "language_locked"; lang: LangCode }
  | { type: "error"; message: string };

/** Backend `/voice/token` response. */
export interface TokenResponse {
  token: string;
  room: string;
  livekit_url: string;
}
