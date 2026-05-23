import { STATE_LABELS, type LangCode } from "@/lib/languages";
import type { SpeakerState } from "@/lib/types";
import { cn } from "@/lib/utils";

interface StateIndicatorProps {
  state: SpeakerState;
  language: LangCode;
}

export default function StateIndicator({ state, language }: StateIndicatorProps) {
  const labels = STATE_LABELS[language] ?? STATE_LABELS.en;
  const text =
    state === "listening" || state === "user_speaking"
      ? labels.listening
      : state === "thinking"
        ? labels.thinking
        : labels.speaking;

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-medium",
        "border transition-colors duration-200",
        state === "thinking" &&
          "italic text-muted-foreground bg-slate-50 border-slate-200",
        state === "agent_speaking" &&
          "text-primary bg-primary/10 border-primary/20",
        state === "user_speaking" &&
          "text-accent bg-accent-soft border-accent/25",
        state === "listening" &&
          "text-slate-600 bg-white border-slate-200 shadow-sm"
      )}
      aria-live="polite"
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full shrink-0",
          state === "agent_speaking" && "bg-primary animate-pulse",
          state === "user_speaking" && "bg-accent animate-pulse",
          state === "listening" && "bg-slate-400",
          state === "thinking" && "bg-slate-300"
        )}
      />
      <span>{text}</span>
      {(state === "listening" || state === "user_speaking") && (
        <span className="inline-flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1 w-1 rounded-full bg-current opacity-50 animate-bounce"
              style={{ animationDelay: `${i * 0.12}s`, animationDuration: "1s" }}
            />
          ))}
        </span>
      )}
    </div>
  );
}
