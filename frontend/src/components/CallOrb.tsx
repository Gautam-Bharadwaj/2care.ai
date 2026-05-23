import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SpeakerState } from "@/lib/types";

interface CallOrbProps {
  /**
   * 'idle' before the call starts. During the call the speaker state
   * drives the visual treatment (color, ring count, dot orbit).
   */
  state: "idle" | SpeakerState;
  /** Tap handler. Behaves as start-call in idle, end-call when active. */
  onClick?: () => void;
  /** Optional 0..1 audio amplitude — pulses the orb during user_speaking. */
  amp?: number;
  disabled?: boolean;
  /** Smaller orb for the sidebar booking card */
  size?: "default" | "compact";
}

const SIZES = {
  default: { wrap: 200, orb: 120, ring: 120 },
  compact: { wrap: 168, orb: 100, ring: 100 },
} as const;

/**
 * 120 px circular orb. Visual states match the design:
 *   idle              — teal circle, subtle 2s breath (scale 1.0 → 1.03)
 *   listening         — single thin expanding ring (waiting for user)
 *   user_speaking     — coral orb + 3 staggered rings + amplitude scale
 *   thinking          — 3 dots orbiting the orb center (1.4s rotation)
 *   agent_speaking    — teal orb + 3 staggered rings, like user but teal
 *
 * Only CSS animations + one inline transform for the amplitude pulse.
 */
export default function CallOrb({
  state,
  onClick,
  amp = 0,
  disabled,
  size = "default",
}: CallOrbProps) {
  const dim = SIZES[size];
  const iconClass = size === "compact" ? "h-6 w-6" : "h-7 w-7";
  const isActive = state !== "idle";
  const isUser = state === "user_speaking";
  const colorFamily = isUser ? "accent" : "primary";

  // Amplitude pulse — clamp so the orb never jitters wildly. Only
  // active when the user is currently speaking (we're actually reading
  // their mic). All other states use the CSS-driven animation.
  const scale = isUser ? 1 + Math.min(0.18, amp * 0.85) : 1;
  const transformStyle = isUser
    ? { transform: `scale(${scale.toFixed(3)})` }
    : undefined;

  // a11y label per state. The orb is the primary CTA on the hero,
  // a status indicator during the call, and a control surface
  // throughout — screen readers should hear the right thing in each
  // phase. `aria-live="polite"` on the wrapper announces state
  // transitions without interrupting the user.
  const ariaLabel =
    state === "idle"
      ? "Start call"
      : state === "user_speaking"
      ? "You are speaking"
      : state === "agent_speaking"
      ? "Assistant is speaking"
      : state === "thinking"
      ? "Assistant is thinking"
      : "Listening";

  const ringStyle = { width: dim.ring, height: dim.ring };

  return (
    <div
      className="relative grid place-items-center"
      style={{ width: dim.wrap, height: dim.wrap }}
      role="status"
      aria-live="polite"
      aria-label={ariaLabel}
    >
      {/* Expanding rings — count + color depend on state */}
      {state === "listening" && (
        <span
          className={cn(
            "absolute rounded-full border-[1.5px] pointer-events-none",
            "animate-ring-out-1",
            "border-primary"
          )}
          style={ringStyle}
        />
      )}
      {(state === "user_speaking" || state === "agent_speaking") && (
        <>
          <span
            className={cn(
              "absolute rounded-full border-[1.5px] pointer-events-none animate-ring-out-1",
              isUser ? "border-accent" : "border-primary"
            )}
            style={ringStyle}
          />
          <span
            className={cn(
              "absolute rounded-full border-[1.5px] pointer-events-none animate-ring-out-2",
              isUser ? "border-accent" : "border-primary"
            )}
            style={ringStyle}
          />
          <span
            className={cn(
              "absolute rounded-full border-[1.5px] pointer-events-none animate-ring-out-3",
              isUser ? "border-accent" : "border-primary"
            )}
            style={ringStyle}
          />
        </>
      )}

      {/* Three rotating dots for "thinking". They orbit the orb on a
          fixed-radius circle by rotating the wrapper around the center. */}
      {state === "thinking" && (
        <div className="absolute inset-0 animate-dot-orbit pointer-events-none">
          <span className="absolute left-1/2 top-2 h-2 w-2 -translate-x-1/2 rounded-full bg-primary/70" />
          <span
            className="absolute h-2 w-2 rounded-full bg-primary/40"
            style={{ right: "16%", bottom: "20%" }}
          />
          <span
            className="absolute h-2 w-2 rounded-full bg-primary/40"
            style={{ left: "16%", bottom: "20%" }}
          />
        </div>
      )}

      {/* The orb itself */}
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        style={{
          ...transformStyle,
          width: dim.orb,
          height: dim.orb,
        }}
        className={cn(
          "relative z-10 grid place-items-center rounded-full",
          "text-primary-foreground",
          "transition-[transform,background-color,box-shadow] duration-300",
          "ring-8",
          // Color family
          colorFamily === "primary"
            ? "bg-primary ring-primary/10 shadow-[0_24px_60px_-12px_hsl(var(--primary)/0.32)]"
            : "bg-accent ring-accent/10 shadow-[0_24px_60px_-12px_hsl(var(--accent)/0.32)]",
          // Idle breath
          state === "idle" && "animate-orb-idle",
          // Hover lift (only when idle — during a call the orb should not jump)
          state === "idle" &&
            "hover:-translate-y-[1px] active:translate-y-[1px]",
          disabled && "opacity-60 cursor-not-allowed",
          // The thinking state dims slightly to feel pensive
          state === "thinking" && "opacity-90"
        )}
        aria-label={isActive ? "End call" : "Start call"}
      >
        {state === "idle" && <Mic className={iconClass} strokeWidth={1.6} />}
        {state === "listening" && <Mic className={iconClass} strokeWidth={1.6} />}
        {state === "user_speaking" && <Mic className={iconClass} strokeWidth={1.6} />}
        {state === "agent_speaking" && <Phone className={iconClass} strokeWidth={1.6} />}
        {state === "thinking" && (
          <Mic
            className={size === "compact" ? "h-5 w-5 opacity-60" : "h-6 w-6 opacity-60"}
            strokeWidth={1.6}
          />
        )}
        {/* `disabled` icon overrides — used while connecting */}
        {disabled && (
          <MicOff className="h-6 w-6 absolute opacity-0" aria-hidden />
        )}
        {isActive && (
          <span className="sr-only">
            <PhoneOff />
          </span>
        )}
      </button>
    </div>
  );
}
