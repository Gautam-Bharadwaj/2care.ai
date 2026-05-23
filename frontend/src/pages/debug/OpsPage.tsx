import { useEffect, useState } from "react";

import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

/**
 * /debug/ops — minimal operations dashboard.
 *
 * Reads from the existing backend metrics + health endpoints. NOT
 * linked from `/` — this is the operator surface, reached by typing
 * the URL.
 */

interface LatencyStats {
  n: number;
  p50: number | null;
  p90: number | null;
  p99: number | null;
}
interface LatencySummary {
  sample_size: number;
  speech_end_to_first_audio_ms: LatencyStats;
  stt_finalization_ms: LatencyStats;
  llm_ttft_ms: LatencyStats;
  tts_ttfb_ms: LatencyStats;
}
interface Healthz {
  status: "ok" | "degraded";
  checked_at: string;
  db: { ok: boolean; latency_ms?: number; error?: string };
  redis: { ok: boolean; latency_ms?: number; error?: string };
  providers: {
    llm: { last_call_at: string | null; age_seconds: number | null };
    tts: { last_call_at: string | null; age_seconds: number | null };
  };
}

const API_BASE = import.meta.env.VITE_API_BASE || "";

export default function OpsPage() {
  const [health, setHealth] = useState<Healthz | null>(null);
  const [latency, setLatency] = useState<LatencySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const refresh = async () => {
      try {
        const [hRes, lRes] = await Promise.all([
          fetch(`${API_BASE}/healthz`),
          fetch(`${API_BASE}/metrics/latency`),
        ]);
        if (hRes.ok) setHealth(await hRes.json());
        if (lRes.ok) setLatency(await lRes.json());
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            debug · ops
          </p>
          <h1 className="text-lg font-semibold tracking-tight">Operations</h1>
        </div>
        {health && (
          <Badge variant={health.status === "ok" ? "success" : "muted"}>
            backend {health.status}
          </Badge>
        )}
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {error && (
          <Card className="border-destructive">
            <CardContent className="py-6 text-destructive text-sm">
              {error}
            </CardContent>
          </Card>
        )}

        {/* Health */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">System health</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Tile
              label="DB"
              ok={health?.db.ok}
              detail={health?.db.latency_ms ? `${health.db.latency_ms.toFixed(0)} ms` : "—"}
            />
            <Tile
              label="Redis"
              ok={health?.redis.ok}
              detail={
                health?.redis.latency_ms
                  ? `${health.redis.latency_ms.toFixed(0)} ms`
                  : "—"
              }
            />
            <Tile
              label="LLM"
              ok={health?.providers.llm.last_call_at != null}
              detail={fmtAge(health?.providers.llm.age_seconds)}
            />
            <Tile
              label="TTS"
              ok={health?.providers.tts.last_call_at != null}
              detail={fmtAge(health?.providers.tts.age_seconds)}
            />
          </CardContent>
        </Card>

        {/* Latency */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              End-to-end latency · last {latency?.sample_size ?? 0} turns
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat
              label="speech → first audio · p50"
              value={latency?.speech_end_to_first_audio_ms.p50}
              budget={450}
            />
            <Stat
              label="p90"
              value={latency?.speech_end_to_first_audio_ms.p90}
              budget={700}
            />
            <Stat
              label="p99"
              value={latency?.speech_end_to_first_audio_ms.p99}
              budget={1200}
            />
            <Stat
              label="STT p50"
              value={latency?.stt_finalization_ms.p50}
            />
            <Stat label="LLM TTFT p50" value={latency?.llm_ttft_ms.p50} />
            <Stat label="TTS TTFB p50" value={latency?.tts_ttfb_ms.p50} />
          </CardContent>
        </Card>

        <p className="text-xs text-muted-foreground">
          For the full reasoning trace of a single call, visit{" "}
          <span className="font-mono">/debug/trace/&lt;session-id&gt;</span>.
        </p>
      </main>
    </div>
  );
}

function Tile({ label, ok, detail }: { label: string; ok?: boolean; detail: string }) {
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground font-mono">
        <span
          className={
            "inline-block h-2 w-2 rounded-full " +
            (ok ? "bg-success" : ok === false ? "bg-destructive" : "bg-muted-foreground")
          }
        />
        {label}
      </div>
      <div className="mt-1 text-sm font-mono">{detail}</div>
    </div>
  );
}

function Stat({
  label,
  value,
  budget,
}: {
  label: string;
  value: number | null | undefined;
  budget?: number;
}) {
  const breach = budget != null && value != null && value > budget;
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-mono">
        {label}
      </div>
      <div className="mt-1 font-mono text-lg tabular-nums">
        {value != null ? (
          <span className={breach ? "text-destructive" : ""}>{Math.round(value)} ms</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>
    </div>
  );
}

function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}
