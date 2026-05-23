/** Shared microphone helpers for voice.ts and livekit.ts */

export const MIC_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

export async function requestMicrophone(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("MIC_UNSUPPORTED");
  }
  if (!window.isSecureContext) {
    throw new Error("MIC_INSECURE");
  }
  return navigator.mediaDevices.getUserMedia({ audio: MIC_AUDIO_CONSTRAINTS });
}

/** Best MediaRecorder mime for this browser (Safari needs mp4). */
export function pickRecorderMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/aac",
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "";
}

export async function resumeAudioContext(ctx: AudioContext | null): Promise<void> {
  if (!ctx || ctx.state === "closed") return;
  if (ctx.state === "suspended") {
    await ctx.resume();
  }
}
