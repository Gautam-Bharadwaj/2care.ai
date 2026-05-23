import { Outlet } from "react-router-dom";

import AppHeader from "@/components/AppHeader";

/** Shared shell: grid background + top navigation on every page. */
export default function SiteLayout() {
  return (
    <div className="min-h-dvh flex flex-col site-grid-bg">
      <div className="w-full max-w-6xl mx-auto px-4 sm:px-6 pt-5 sm:pt-6 flex-shrink-0">
        <AppHeader />
      </div>
      <div className="flex-1 flex flex-col min-h-0">
        <Outlet />
      </div>
      <footer className="w-full max-w-6xl mx-auto px-4 sm:px-6 py-5 text-center text-[11px] text-slate-400/90">
        2care.ai · Voice scheduling demo
      </footer>
    </div>
  );
}
