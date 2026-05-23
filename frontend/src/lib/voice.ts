/**
 * Browser voice engine — mic capture + server STT/TTS + /voice/chat LLM.
 *
 * Uses Deepgram (STT) and Cartesia (TTS) via the FastAPI backend so Indic
 * languages work reliably. For LiveKit + agent tools, set
 * `VITE_USE_LIVEKIT=true` and run `uv run twocare-agent dev`.
 */

import {
  EMPTY_SCHEDULING,
  finalizeBooking,
  updateSchedulingFromAgent,
} from "./bookingExtract";
import {
  pickRecorderMimeType,
  requestMicrophone,
  resumeAudioContext,
} from "./mic";
import { AGENT_GREETINGS, FILLER_DIDNT_CATCH, type LangCode } from "./languages";
import type { AgentEvent } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "";

/** Min recorded audio before we bother calling STT (bytes). */
const MIN_AUDIO_BYTES = 200;

/** RMS thresholds (0–1). Lower = easier to detect quiet mics / laptop mics. */
const UI_SPEECH_THRESHOLD = 0.022;
const RECORD_SPEECH_THRESHOLD = 0.016;

/** Silence after speech before we stop recording and send to STT. */
const END_SILENCE_MS = 1100;

/** Pause after agent TTS so speaker bleed does not trip VAD / STT. */
const POST_AGENT_AUDIO_MS = 550;

export interface VoiceCallOptions {
  language: LangCode;
  onEvent: (event: AgentEvent) => void;
  onAmplitude?: (amp: number) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (message: string) => void;
}

export interface ActiveVoiceCall {
  disconnect: () => Promise<void>;
}

export async function startCall(opts: VoiceCallOptions): Promise<ActiveVoiceCall> {
  const engine = new VoiceEngine(opts);
  const run = engine.start().catch((err) => {
    if (!engine.isStopped()) {
      opts.onError?.(String((err as Error)?.message || err));
    }
  });
  return {
    disconnect: async () => {
      await engine.stop();
      await run;
    },
  };
}

class VoiceEngine {
  private opts: VoiceCallOptions;
  private mediaStream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private ampFrame: number | null = null;
  private cancelled = false;
  private history: { role: "user" | "assistant"; content: string }[] = [];
  private cancelHooks: Array<() => void> = [];
  private isListenPhase = false;
  private userSpeaking = false;
  private silenceStartedAt = 0;
  private currentAudio: HTMLAudioElement | null = null;
  private abort = new AbortController();
  private recordPollTimer: ReturnType<typeof setTimeout> | null = null;
  private emptyListenCount = 0;

  constructor(opts: VoiceCallOptions) {
    this.opts = opts;
  }

  isStopped(): boolean {
    return this.cancelled;
  }

  private emit(ev: AgentEvent) {
    if (this.cancelled) return;
    this.opts.onEvent(ev);
  }

  private registerCancel(fn: () => void) {
    this.cancelHooks.push(fn);
  }

  private fireCancelHooks() {
    const hooks = this.cancelHooks;
    this.cancelHooks = [];
    for (const h of hooks) {
      try {
        h();
      } catch {
        /* ignore */
      }
    }
  }

  async start() {
    this.cancelled = false;
    this.abort = new AbortController();
    const { language, onConnected, onError } = this.opts;

    const backendOk = await this.checkBackend();
    if (!backendOk) {
      onError?.(
        "Voice backend is not running. In another terminal run: uv run twocare-backend"
      );
      throw new Error("BACKEND_DOWN");
    }

    try {
      this.mediaStream = await requestMicrophone();
    } catch (e) {
      const code = (e as Error).message;
      if (code === "MIC_INSECURE") {
        onError?.(
          "Microphone needs HTTPS or localhost. Open the app at http://localhost:5174 (not a file:// URL)."
        );
      } else if (code === "MIC_UNSUPPORTED") {
        onError?.("This browser does not support microphone capture. Try Chrome or Edge.");
      } else {
        onError?.(
          "Microphone access was blocked. Allow it in your browser settings and try again."
        );
      }
      throw new Error("MIC_DENIED");
    }

    const track = this.mediaStream.getAudioTracks()[0];
    if (track) {
      track.enabled = true;
    }

    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioCtx = new Ctx();
      await resumeAudioContext(this.audioCtx);
      const src = this.audioCtx.createMediaStreamSource(this.mediaStream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.smoothingTimeConstant = 0.4;
      src.connect(this.analyser);
      this.runAmpLoop();
    } catch (e) {
      console.warn("[voice] analyser init failed", e);
    }

    onConnected?.();

    const greeting = AGENT_GREETINGS[language] || AGENT_GREETINGS.en;
    this.history = [{ role: "assistant", content: greeting }];
    this.emit({ type: "state", value: "agent_speaking" });
    this.emit({ type: "transcript", role: "agent", text: greeting, lang: language });
    await this.speak(greeting, language);
    if (this.cancelled) return;

    while (!this.cancelled) {
      this.userSpeaking = false;
      this.silenceStartedAt = 0;
      this.isListenPhase = true;
      this.emit({ type: "state", value: "listening" });

      await resumeAudioContext(this.audioCtx);
      await sleep(POST_AGENT_AUDIO_MS);

      const userText = await this.recordAndTranscribe(language);
      this.isListenPhase = false;
      this.userSpeaking = false;

      if (this.cancelled) return;
      if (!userText.trim()) {
        this.emptyListenCount += 1;
        if (this.emptyListenCount >= 3) {
          onError?.(
            "Still not hearing your voice. Check the mic input in system settings, speak closer to the mic, and allow microphone access for this site."
          );
          this.emptyListenCount = 0;
        }
        const filler = FILLER_DIDNT_CATCH[language] || FILLER_DIDNT_CATCH.en;
        this.emit({ type: "state", value: "agent_speaking" });
        this.emit({ type: "transcript", role: "agent", text: filler, lang: language });
        await this.speak(filler, language);
        if (this.cancelled) return;
        continue;
      }

      this.emptyListenCount = 0;
      this.emit({ type: "transcript", role: "user", text: userText, lang: language });
      this.emit({ type: "state", value: "thinking" });

      const reply = await this.askLLM(userText, language);
      if (this.cancelled) return;

      this.history.push({ role: "user", content: userText });
      this.history.push({ role: "assistant", content: reply });

      this.emit({ type: "state", value: "agent_speaking" });
      this.emit({ type: "transcript", role: "agent", text: reply, lang: language });
      await this.speak(reply, language);
      if (this.cancelled) return;

      if (looksLikeConfirmation(reply)) {
        let scheduling = EMPTY_SCHEDULING;
        for (const m of this.history) {
          if (m.role === "assistant") {
            scheduling = updateSchedulingFromAgent(scheduling, m.content);
          }
        }
        if (this.cancelled) return;
        await sleep(350);
        if (this.cancelled) return;
        this.emit({
          type: "booking_confirmed",
          appointment: finalizeBooking(scheduling.booking),
        });
        return;
      }
    }
  }

  async stop() {
    if (this.cancelled) return;
    this.cancelled = true;
    this.isListenPhase = false;
    this.userSpeaking = false;

    this.abort.abort();
    if (this.recordPollTimer != null) {
      clearTimeout(this.recordPollTimer);
      this.recordPollTimer = null;
    }

    if (this.currentAudio) {
      try {
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
        this.currentAudio.src = "";
      } catch {
        /* ignore */
      }
      this.currentAudio = null;
    }

    this.fireCancelHooks();

    if (this.ampFrame != null) cancelAnimationFrame(this.ampFrame);
    this.ampFrame = null;

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((t) => t.stop());
      this.mediaStream = null;
    }
    if (this.audioCtx) {
      try {
        await this.audioCtx.close();
      } catch {
        /* ignore */
      }
      this.audioCtx = null;
    }
  }

  private async checkBackend(): Promise<boolean> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    try {
      const r = await fetch(`${API_BASE}/healthz`, { signal: ctrl.signal });
      return r.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  private measurePeak(): number {
    if (!this.analyser) return 0;
    const buf = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = Math.abs(buf[i] - 128) / 128;
      if (v > peak) peak = v;
    }
    return peak;
  }

  private runAmpLoop() {
    if (!this.analyser) return;
    let prev = 0;

    const tick = () => {
      if (this.cancelled || !this.analyser) return;
      const peak = this.measurePeak();
      prev = prev * 0.55 + peak * 0.45;
      this.opts.onAmplitude?.(prev);

      if (this.isListenPhase) {
        const loud = prev > UI_SPEECH_THRESHOLD;
        if (loud && !this.userSpeaking) {
          this.userSpeaking = true;
          this.silenceStartedAt = 0;
          this.emit({ type: "state", value: "user_speaking" });
        } else if (this.userSpeaking) {
          if (loud) {
            this.silenceStartedAt = 0;
          } else if (this.silenceStartedAt === 0) {
            this.silenceStartedAt = performance.now();
          } else if (performance.now() - this.silenceStartedAt > 500) {
            this.userSpeaking = false;
            this.silenceStartedAt = 0;
            this.emit({ type: "state", value: "listening" });
          }
        }
      }

      this.ampFrame = requestAnimationFrame(tick);
    };
    this.ampFrame = requestAnimationFrame(tick);
  }

  private async recordAndTranscribe(language: LangCode): Promise<string> {
    if (!this.mediaStream || this.cancelled) return "";

    const mime = pickRecorderMimeType();
    if (!mime && typeof MediaRecorder === "undefined") {
      console.warn("[voice] MediaRecorder not available");
      return "";
    }

    return new Promise((resolve) => {
      let done = false;
      const finish = (text: string) => {
        if (done) return;
        done = true;
        resolve(text);
      };
      this.registerCancel(() => finish(""));

      const chunks: Blob[] = [];
      let recorder: MediaRecorder;
      try {
        recorder = mime
          ? new MediaRecorder(this.mediaStream!, { mimeType: mime, audioBitsPerSecond: 128000 })
          : new MediaRecorder(this.mediaStream!);
      } catch (e) {
        console.warn("[voice] MediaRecorder failed", e);
        finish("");
        return;
      }

      let heardSpeech = false;
      let silenceMs = 0;
      const MAX_MS = 22000;
      const NO_SPEECH_GIVE_UP_MS = 14000;
      const FIXED_RECORD_MS = 7000;
      const started = performance.now();
      const hasVad = Boolean(this.analyser);

      const poll = () => {
        if (done || this.cancelled) {
          try {
            if (recorder.state === "recording") recorder.stop();
          } catch {
            /* ignore */
          }
          return;
        }

        const peak = this.measurePeak();

        if (hasVad) {
          if (peak > RECORD_SPEECH_THRESHOLD) {
            heardSpeech = true;
            silenceMs = 0;
          } else if (heardSpeech) {
            silenceMs += 80;
          }
        }

        const elapsed = performance.now() - started;
        const shouldStop = hasVad
          ? (heardSpeech && silenceMs >= END_SILENCE_MS) ||
            elapsed >= MAX_MS ||
            (!heardSpeech && elapsed >= NO_SPEECH_GIVE_UP_MS)
          : elapsed >= FIXED_RECORD_MS || elapsed >= MAX_MS;

        if (shouldStop && recorder.state === "recording") {
          recorder.stop();
          return;
        }
        this.recordPollTimer = setTimeout(poll, 80);
      };

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };

      recorder.onstop = async () => {
        if (this.cancelled) {
          finish("");
          return;
        }
        const blob = new Blob(chunks, {
          type: recorder.mimeType || mime || "audio/webm",
        });
        if (blob.size < MIN_AUDIO_BYTES) {
          console.warn("[voice] recording too short", blob.size, "heardSpeech", heardSpeech);
          finish("");
          return;
        }
        const text = await this.transcribeBlob(blob, language);
        if (this.cancelled) {
          finish("");
          return;
        }
        finish(text);
      };

      recorder.onerror = () => {
        console.warn("[voice] recorder error");
        finish("");
      };

      try {
        recorder.start(100);
        poll();
      } catch (e) {
        console.warn("[voice] recorder.start failed", e);
        finish("");
      }
    });
  }

  private async transcribeBlob(blob: Blob, language: LangCode): Promise<string> {
    try {
      const ext = blob.type.includes("mp4") ? "utterance.m4a" : "utterance.webm";
      const form = new FormData();
      form.append("audio", blob, ext);
      form.append("language", language);
      const r = await fetch(`${API_BASE}/voice/stt`, {
        method: "POST",
        body: form,
        signal: this.abort.signal,
      });
      if (this.cancelled) return "";
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        console.warn("[voice] STT failed", r.status, detail);
        if (r.status === 503) {
          this.opts.onError?.(
            "Speech recognition is not configured. Add DEEPGRAM_API_KEY to .env and restart the backend."
          );
        } else {
          this.opts.onError?.(
            "Could not transcribe your voice. Make sure the backend is running: uv run twocare-backend"
          );
        }
        return "";
      }
      const data = await r.json();
      return (data.text as string) || "";
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        console.warn("[voice] STT error", e);
      }
      return "";
    }
  }

  private async speak(text: string, language: LangCode): Promise<void> {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        if (this.currentAudio) {
          this.currentAudio = null;
        }
        void resumeAudioContext(this.audioCtx);
        resolve();
      };
      this.registerCancel(finish);

      if (this.cancelled) {
        finish();
        return;
      }

      (async () => {
        try {
          const r = await fetch(`${API_BASE}/voice/tts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, language }),
            signal: this.abort.signal,
          });
          if (!r.ok || this.cancelled) {
            finish();
            return;
          }
          const blob = await r.blob();
          if (this.cancelled) {
            finish();
            return;
          }
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.setAttribute("playsinline", "true");
          this.currentAudio = audio;
          audio.onended = () => {
            URL.revokeObjectURL(url);
            finish();
          };
          audio.onerror = () => {
            URL.revokeObjectURL(url);
            finish();
          };
          await resumeAudioContext(this.audioCtx);
          try {
            await audio.play();
          } catch (playErr) {
            console.warn("[voice] audio.play failed", playErr);
            URL.revokeObjectURL(url);
            finish();
            return;
          }
          if (this.cancelled) {
            audio.pause();
            URL.revokeObjectURL(url);
            finish();
          }
        } catch (err) {
          console.warn("[voice] speak failed", err);
          if (!this.cancelled) finish();
        }
      })();
    });
  }

  private async askLLM(userText: string, language: LangCode): Promise<string> {
    const messages = [...this.history, { role: "user" as const, content: userText }];
    try {
      const r = await fetch(`${API_BASE}/voice/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, messages }),
        signal: this.abort.signal,
      });
      if (this.cancelled) return "";
      if (!r.ok) {
        return (
          FILLER_DIDNT_CATCH[language] ||
          "Sorry, the booking service is unavailable. Please try again."
        );
      }
      const data = await r.json();
      return data.response || FILLER_DIDNT_CATCH[language] || FILLER_DIDNT_CATCH.en;
    } catch {
      return (
        FILLER_DIDNT_CATCH[language] ||
        "Sorry, cannot reach the server. Start the backend with: uv run twocare-backend"
      );
    }
  }
}

function looksLikeConfirmation(text: string): boolean {
  if (!text) return false;
  const t = text.toLowerCase();
  const lat =
    /\b(booked|confirmed|appointment is set|booking complete)\b/.test(t) &&
    /\b(sms|sent|confirmation)\b/.test(t);
  const indic =
    /(बुक|कन्फर्म|पुष्टि|बुकिंग|பதிவ|பதிவு|உறுதி|ಬುಕ್|ಖಚಿತ|ಬುಕ್|ನಿರ್ಧಾರಣ|বুক|নিশ্চিত|બુક|કન્ફર્મ|ബുക്ക്|ഉറപ്പ|ਬੁੱਕ|ਕਨਫਰਮ)/.test(text) &&
    /(sms|एसएमएस|भेज|sent|அனுப்ப|ಕಳುಹಿಸ|ఎస్ఎంఎస్|పంప|পাঠানো|મોકલી|അയച്ച|അയച്ചിട്ടുണ്ട|ਭੇਜ)/i.test(t);
  return lat || indic;
}

function sleep(ms: number) {
  return new Promise((res) => setTimeout(res, ms));
}
