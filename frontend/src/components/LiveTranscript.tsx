import { MessageSquare } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Turn } from "@/lib/types";

interface LiveTranscriptProps {
  turns: Turn[];
}

/** Last 4 turns with chat-style bubbles inside a panel. */
export default function LiveTranscript({ turns }: LiveTranscriptProps) {
  const tail = turns.slice(-4);

  return (
    <div className="w-full glass-card p-4 sm:p-5">
      <div className="flex items-center gap-2 mb-3 pb-2 border-b border-slate-100">
        <MessageSquare className="h-4 w-4 text-primary/80" strokeWidth={1.8} aria-hidden />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Live transcript
        </span>
      </div>

      {tail.length === 0 ? (
        <p className="text-center text-sm text-muted-foreground py-6">
          Conversation will appear here as you speak…
        </p>
      ) : (
        <div className="flex flex-col gap-2.5 min-h-[88px]" aria-live="polite">
          {tail.map((t, i) => {
            const isUser = t.role === "user";
            const age = tail.length - 1 - i;
            return (
              <div
                key={`${t.role}-${t.ts}-${i}`}
                className={cn(
                  "flex animate-line-fade",
                  isUser ? "justify-end" : "justify-start"
                )}
              >
                <div
                  className={cn(
                    "max-w-[90%] rounded-2xl px-3.5 py-2.5 text-[15px] leading-relaxed",
                    isUser
                      ? "bg-primary text-primary-foreground rounded-br-md shadow-sm shadow-primary/15"
                      : "bg-slate-50 border border-slate-100 text-foreground rounded-bl-md",
                    age === 1 && "opacity-75",
                    age >= 2 && "opacity-50"
                  )}
                >
                  {!isUser && (
                    <span className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1 font-semibold">
                      Assistant
                    </span>
                  )}
                  {t.text}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
