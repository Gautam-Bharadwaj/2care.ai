/**
 * LiveKit room manager for the booking page.
 */
import {
  Room,
  RoomEvent,
  Track,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrackPublication,
} from "livekit-client";

import type { AgentEvent, TokenResponse } from "./types";
import type { LangCode } from "./languages";

const API_BASE = import.meta.env.VITE_API_BASE || "";

export interface ConnectArgs {
  language: LangCode;
  patientPhone?: string;
  onEvent: (ev: AgentEvent) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (message: string) => void;
}

export interface ActiveCall {
  room: Room;
  disconnect: () => Promise<void>;
}

export async function startCall(args: ConnectArgs): Promise<ActiveCall> {
  const tokenRes = await fetch(`${API_BASE}/voice/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      language: args.language,
      patient_phone: args.patientPhone ?? null,
    }),
  });
  if (!tokenRes.ok) {
    throw new Error(`TOKEN_${tokenRes.status}`);
  }
  const { token, livekit_url }: TokenResponse = await tokenRes.json();

  const room = new Room({
    adaptiveStream: true,
    dynacast: true,
    publishDefaults: { dtx: true, red: true },
  });

  let ended = false;
  const agentAudioEls: HTMLAudioElement[] = [];

  const stopAgentAudio = () => {
    for (const el of agentAudioEls) {
      try {
        el.pause();
        el.srcObject = null;
        el.remove();
      } catch {
        /* ignore */
      }
    }
    agentAudioEls.length = 0;
  };

  wireRoomEvents(room, args, {
    isEnded: () => ended,
    onAgentAudio: (el) => agentAudioEls.push(el),
    stopAgentAudio,
  });

  await room.connect(livekit_url, token);

  try {
    await room.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
  } catch {
    await room.disconnect().catch(() => undefined);
    throw new Error("MIC_DENIED");
  }

  args.onConnected?.();

  return {
    room,
    disconnect: async () => {
      if (ended) return;
      ended = true;

      stopAgentAudio();

      try {
        await room.localParticipant.setMicrophoneEnabled(false);
      } catch {
        /* ignore */
      }

      try {
        await room.disconnect();
      } catch {
        /* ignore */
      }

      args.onDisconnected?.();
    },
  };
}

function wireRoomEvents(
  room: Room,
  args: ConnectArgs,
  ctx: {
    isEnded: () => boolean;
    onAgentAudio: (el: HTMLAudioElement) => void;
    stopAgentAudio: () => void;
  }
) {
  room.on(
    RoomEvent.TrackSubscribed,
    (track, _pub: RemoteTrackPublication, _participant: RemoteParticipant) => {
      if (ctx.isEnded() || track.kind !== Track.Kind.Audio) return;
      const el = (track as RemoteAudioTrack).attach();
      el.style.display = "none";
      document.body.appendChild(el);
      ctx.onAgentAudio(el);
    }
  );

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      (track as RemoteAudioTrack).detach().forEach((el) => {
        try {
          el.pause();
          el.remove();
        } catch {
          /* ignore */
        }
      });
    }
  });

  room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
    if (ctx.isEnded()) return;
    if (topic && topic !== "agent") return;
    try {
      const text = new TextDecoder().decode(payload);
      const ev = JSON.parse(text) as AgentEvent;
      args.onEvent(ev);
    } catch (e) {
      console.warn("[livekit] invalid data event", e);
    }
  });

  room.on(RoomEvent.Disconnected, () => {
    if (ctx.isEnded()) return;
    ctx.stopAgentAudio();
    args.onDisconnected?.();
  });

  room.on(RoomEvent.MediaDevicesError, (err: Error) => {
    if (ctx.isEnded()) return;
    args.onError?.(err.message || "Microphone error");
  });
}
