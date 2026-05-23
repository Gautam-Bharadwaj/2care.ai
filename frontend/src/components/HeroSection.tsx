import { Mic2 } from "lucide-react";

import { cn } from "@/lib/utils";

interface HeroSectionProps {
  className?: string;
}

export default function HeroSection({ className }: HeroSectionProps) {
  return (
    <section
      className={cn(
        "text-left animate-line-fade flex flex-col justify-center lg:pr-6 lg:pt-1",
        className
      )}
    >
      <h1 className="text-[1.65rem] sm:text-[2.25rem] xl:text-[2.65rem] font-bold tracking-tight leading-[1.15] text-slate-900">
        AI Infrastructure for{" "}
        <span className="brand-gradient-text">Modern Healthcare</span>
      </h1>

      <p className="mt-4 text-[15px] sm:text-base text-slate-600 leading-relaxed max-w-md">
        Follow up with patients, check when doctors are free, and book clinic
        appointments through a simple voice call. Pick your language and talk
        naturally in English, Hindi, Bengali, Tamil, Telugu, Kannada, Malayalam,
        Marathi, Gujarati, or Punjabi.
      </p>

      <ul className="mt-5 flex flex-col sm:flex-row sm:flex-wrap gap-2 text-xs text-slate-600">
        {[
          { icon: Mic2, label: "Speak in 10 languages" },
          { label: "Real-time scheduling" },
          { label: "HIPAA-ready pipeline" },
        ].map((item) => (
          <li
            key={item.label}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200/80 bg-white/80 px-3 py-2"
          >
            {"icon" in item && item.icon && (
              <item.icon className="h-3.5 w-3.5 text-primary shrink-0" strokeWidth={2} aria-hidden />
            )}
            {item.label}
          </li>
        ))}
      </ul>
    </section>
  );
}
