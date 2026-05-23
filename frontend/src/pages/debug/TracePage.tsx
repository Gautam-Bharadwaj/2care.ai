import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";

import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * GET /traces/:session_id  → renders the reasoning trace.
 *
 * This is the operator-facing debug surface. There are NO links from
 * `/` to here — reach by typing the URL. The minimal UI mirrors the
 * `docs/trace-viewer/` Vite app but with Tailwind + shadcn primitives
 * for visual continuity with the new patient page.
 */

interface ToolCallRecord {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  latency_ms?: number;
  status?: "ok" | "error";
}
interface MemoryRecallRecord {
  query: string;
  returned_count: number;
  latency_ms?: number;
  results?: string[];
}
interface TurnLatencyBreakdown {
  stt_finalization_ms?: number;
  llm_ttft_ms?: number;
  tts_ttfb_ms?: number;
  speech_end_to_first_audio_ms?: number;
}
interface TurnTrace {
  turn_idx: number;
  user_input?: string | null;
  detected_language?: string | null;
  active_language?: string | null;
  llm_messages?: { role: string; content: string }[] | null;
  llm_response_text?: string | null;
  final_response_to_user?: string | null;
  tool_calls?: ToolCallRecord[];
  memory_recalls?: MemoryRecallRecord[];
  latency?: TurnLatencyBreakdown;
}
interface TraceRow {
  id: string;
  session_id: string;
  turn_idx: number;
  trace: TurnTrace;
  created_at: string;
}
interface SessionTraces {
  session_id: string;
  turns: TraceRow[];
}

const API_BASE = import.meta.env.VITE_API_BASE || "";

export default function TracePage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [data, setData] = useState<SessionTraces | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState(sessionId || "");

  useEffect(() => {
    if (!sessionId) return;
    setError(null);
    setData(null);
    fetch(`${API_BASE}/traces/${encodeURIComponent(sessionId)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as SessionTraces;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [sessionId]);

  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            debug · reasoning trace
          </p>
          <h1 className="text-lg font-semibold tracking-tight">
            {sessionId || "Pick a session"}
          </h1>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (input.trim()) window.location.href = `/debug/trace/${input.trim()}`;
          }}
          className="flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="session id"
            className="text-sm px-3 py-1.5 rounded-md border border-input bg-background font-mono min-w-[260px]"
          />
          <button
            type="submit"
            className="text-sm px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
          >
            Load
          </button>
        </form>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        {!sessionId && (
          <Card>
            <CardContent className="py-12 text-center text-muted-foreground">
              Enter a session id above, or visit{" "}
              <span className="font-mono text-foreground">
                /debug/trace/&lt;session-id&gt;
              </span>{" "}
              directly.
            </CardContent>
          </Card>
        )}

        {error && (
          <Card className="border-destructive">
            <CardContent className="py-8 text-center text-destructive">
              Failed to load: {error}
            </CardContent>
          </Card>
        )}

        {data && (
          <div className="space-y-4">
            {data.turns.map((row) => (
              <TurnCard key={row.id} row={row} />
            ))}
            {data.turns.length === 0 && (
              <p className="text-center text-muted-foreground py-12">
                No turns in this session.
              </p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function TurnCard({ row }: { row: TraceRow }) {
  const t = row.trace;
  const [open, setOpen] = useState(false);
  const lat = t.latency || {};
  const total = lat.speech_end_to_first_audio_ms;
  const totalCls = !total
    ? ""
    : total <= 450
    ? "bg-success-soft text-success"
    : total <= 700
    ? "bg-accent-soft text-accent"
    : "bg-destructive/15 text-destructive";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 py-4">
        <div className="flex items-center gap-3">
          <Badge variant="muted" className="font-mono">
            turn {t.turn_idx}
          </Badge>
          {t.detected_language && (
            <Badge variant="outline" className="font-mono text-xs">
              {t.detected_language}
            </Badge>
          )}
        </div>
        {total != null && (
          <span className={cn("text-xs font-mono px-2 py-0.5 rounded-full", totalCls)}>
            {Math.round(total)} ms
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {t.user_input && (
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1 font-mono">
              user
            </p>
            <p className="text-sm bg-secondary px-3 py-2 rounded-md">{t.user_input}</p>
          </div>
        )}

        {((t.tool_calls?.length ?? 0) > 0 || (t.memory_recalls?.length ?? 0) > 0) && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5"
          >
            <ChevronRight
              className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-90")}
            />
            agent reasoning ·{" "}
            {(t.memory_recalls?.length ?? 0) > 0 && (
              <>{t.memory_recalls!.length} memory · </>
            )}
            {(t.tool_calls?.length ?? 0) > 0 && (
              <>
                {t.tool_calls!.length} tool
                {t.tool_calls!.length === 1 ? "" : "s"}
              </>
            )}
          </button>
        )}

        {open && (
          <div className="space-y-2 pl-5 border-l border-border">
            {(t.memory_recalls || []).map((m, i) => (
              <div
                key={`m-${i}`}
                className="text-xs font-mono bg-secondary px-3 py-2 rounded"
              >
                <div className="text-muted-foreground">
                  recall_memory("{m.query}")
                </div>
                {m.results?.map((r, j) => (
                  <div key={j} className="mt-1 text-foreground">
                    · {r}
                  </div>
                ))}
              </div>
            ))}
            {(t.tool_calls || []).map((tc, i) => (
              <details
                key={`t-${i}`}
                className="text-xs font-mono bg-secondary rounded px-3 py-2"
              >
                <summary
                  className={cn(
                    "cursor-pointer flex items-center gap-2",
                    tc.status === "error" ? "text-destructive" : "text-foreground"
                  )}
                >
                  <span>{tc.name}(…)</span>
                  <span className="text-muted-foreground ml-auto">
                    {tc.latency_ms ?? "?"} ms
                  </span>
                </summary>
                <pre className="mt-2 text-[11px] whitespace-pre-wrap text-muted-foreground">
                  {JSON.stringify(tc.args || {}, null, 2)}
                </pre>
                {tc.result && (
                  <pre className="mt-2 text-[11px] whitespace-pre-wrap text-foreground">
                    {tc.result}
                  </pre>
                )}
              </details>
            ))}
          </div>
        )}

        {(t.final_response_to_user || t.llm_response_text) && (
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1 font-mono">
              agent
            </p>
            <p className="text-sm bg-primary-soft/40 px-3 py-2 rounded-md text-foreground">
              {t.final_response_to_user || t.llm_response_text}
            </p>
          </div>
        )}

        {/* Latency bar */}
        {(lat.stt_finalization_ms || lat.llm_ttft_ms || lat.tts_ttfb_ms) && (
          <LatencyBar lat={lat} />
        )}
      </CardContent>
    </Card>
  );
}

function LatencyBar({ lat }: { lat: TurnLatencyBreakdown }) {
  const stt = lat.stt_finalization_ms || 0;
  const llm = lat.llm_ttft_ms || 0;
  const tts = lat.tts_ttfb_ms || 0;
  const total = stt + llm + tts || 1;
  return (
    <div>
      <div className="flex h-1.5 rounded-full overflow-hidden bg-secondary">
        <div className="bg-[hsl(210_70%_55%)]" style={{ width: `${(stt / total) * 100}%` }} />
        <div className="bg-primary" style={{ width: `${(llm / total) * 100}%` }} />
        <div className="bg-accent" style={{ width: `${(tts / total) * 100}%` }} />
      </div>
      <div className="flex gap-3 mt-1 text-[11px] font-mono text-muted-foreground">
        <span>stt {Math.round(stt)}ms</span>
        <span>llm {Math.round(llm)}ms</span>
        <span>tts {Math.round(tts)}ms</span>
      </div>
    </div>
  );
}
