import { LANGUAGES, type LangCode } from "@/lib/languages";
import { cn } from "@/lib/utils";

interface LanguagePickerProps {
  value: LangCode;
  onChange: (next: LangCode) => void;
  disabled?: boolean;
  /** 2 = sidebar card; 5 = wide layouts */
  columns?: 2 | 5;
}

export default function LanguagePicker({
  value,
  onChange,
  disabled,
  columns = 5,
}: LanguagePickerProps) {
  const cols = columns === 2 ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-5";

  return (
    <div className="w-full">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
        Choose your language
      </p>
      <p className="text-xs text-slate-500 mb-2.5">
        Pick one, then speak naturally. The assistant will listen and reply in
        the same language.
      </p>
      <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-2">
        <div className={cn("grid gap-1.5", cols)}>
          {LANGUAGES.map((l) => {
            const selected = l.code === value;
            const showSubtitle = l.native !== l.english;
            return (
              <button
                key={l.code}
                type="button"
                disabled={disabled}
                onClick={() => onChange(l.code)}
                aria-pressed={selected}
                className={cn(
                  "min-h-[48px] rounded-lg border px-2 py-2 text-left",
                  "transition-colors duration-150",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-1",
                  selected
                    ? "bg-primary text-primary-foreground border-primary ring-2 ring-primary/20"
                    : "bg-white text-slate-800 border-slate-200/90 hover:border-primary/25 hover:bg-white",
                  disabled && "opacity-50 cursor-not-allowed"
                )}
              >
                <span className="block text-sm font-medium leading-tight truncate">
                  {l.native}
                </span>
                {showSubtitle && (
                  <span
                    className={cn(
                      "mt-0.5 block text-[10px] font-normal truncate",
                      selected ? "text-primary-foreground/70" : "text-slate-500"
                    )}
                  >
                    {l.english}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
