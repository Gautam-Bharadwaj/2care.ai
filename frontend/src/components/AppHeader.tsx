import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

const API_BASE = import.meta.env.VITE_API_BASE || "";

/** Only routes that exist and work — see `main.tsx`. */
const NAV = [
  { id: "bookings", to: "/", label: "Live Bookings", end: true },
  { id: "trace", to: "/debug/trace", label: "Session Trace", end: false },
  { id: "ops", to: "/debug/ops", label: "Ops", end: false },
] as const;

export default function AppHeader() {
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const r = await fetch(`${API_BASE}/healthz`);
        if (!r.ok) {
          if (!cancelled) setApiOnline(false);
          return;
        }
        const data = await r.json();
        if (!cancelled) setApiOnline(data.status === "ok");
      } catch {
        if (!cancelled) setApiOnline(false);
      }
    };

    check();
    const id = window.setInterval(check, 12_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <header className="w-full">
      <div className="glass-card !rounded-full grid grid-cols-1 md:grid-cols-[auto_1fr_auto] items-center gap-3 md:gap-4 px-6 sm:px-8 py-3">
        <NavLink
          to="/"
          className="flex items-center shrink-0 justify-self-start opacity-95 hover:opacity-100 transition-opacity"
          aria-label="2care.ai home"
        >
          <img
            src="/logo.svg"
            alt="2care.ai"
            className="h-7 sm:h-8 w-auto"
            width={152}
            height={31}
            decoding="async"
          />
        </NavLink>

        <nav
          className="flex flex-wrap items-center justify-center gap-0.5 md:justify-self-center"
          aria-label="Main"
        >
          {NAV.map(({ id, to, label, end }) => (
            <NavLink
              key={id}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "rounded-lg px-3 py-2 text-sm font-medium transition-all",
                  isActive
                    ? "text-primary bg-primary/10 shadow-sm"
                    : "text-slate-600 hover:text-primary hover:bg-slate-50"
                )
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div
          className={cn(
            "inline-flex items-center gap-2 justify-self-center lg:justify-self-end shrink-0",
            "rounded-full border px-3 py-1.5 text-xs font-semibold tracking-wide",
            apiOnline === true &&
              "border-emerald-200/90 bg-emerald-50 text-emerald-800",
            apiOnline === false &&
              "border-amber-200/90 bg-amber-50 text-amber-900",
            apiOnline === null && "border-slate-200 bg-slate-50 text-slate-500"
          )}
        >
          <span
            className={cn(
              "h-2 w-2 rounded-full shrink-0",
              apiOnline === true && "live-pulse-dot",
              apiOnline === false && "bg-amber-500",
              apiOnline === null && "bg-slate-300 animate-pulse"
            )}
          />
          {apiOnline === true && "FastAPI Online"}
          {apiOnline === false && "FastAPI Offline"}
          {apiOnline === null && "Checking API…"}
        </div>
      </div>
    </header>
  );
}
