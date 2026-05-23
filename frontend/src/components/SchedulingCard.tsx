import { Calendar, Check, Clock, MapPin, Stethoscope, User } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { SchedulingState, SchedulingStatus } from "@/lib/bookingExtract";
import type { LangCode } from "@/lib/languages";
import type { Booking } from "@/lib/types";

const LABELS: Record<
  LangCode,
  {
    title: string;
    steps: [string, string, string, string];
    fields: { doctor: string; when: string; clinic: string; duration: string };
    awaiting: string;
    confirmed: string;
    pending: string;
  }
> = {
  en: {
    title: "Your appointment",
    steps: ["Finding slots", "Choosing time", "Confirming", "Scheduled"],
    fields: { doctor: "Doctor", when: "Date & time", clinic: "Clinic", duration: "Duration" },
    awaiting: "Say yes to confirm this slot",
    confirmed: "Meeting scheduled",
    pending: "—",
  },
  hi: {
    title: "आपकी अपॉइंटमेंट",
    steps: ["स्लॉट देख रहे हैं", "समय चुनना", "पुष्टि", "बुक हो गई"],
    fields: { doctor: "डॉक्टर", when: "दिन और समय", clinic: "क्लिनिक", duration: "अवधि" },
    awaiting: "हाँ कहकर पुष्टि करें",
    confirmed: "मीटिंग शेड्यूल हो गई",
    pending: "—",
  },
  bn: {
    title: "আপনার অ্যাপয়েন্টমেন্ট",
    steps: ["স্লট খুঁজছি", "সময় বাছাই", "নিশ্চিতকরণ", "বুক হয়েছে"],
    fields: { doctor: "ডাক্তার", when: "তারিখ ও সময়", clinic: "ক্লিনিক", duration: "সময়কাল" },
    awaiting: "হ্যাঁ বলে নিশ্চিত করুন",
    confirmed: "মিটিং শিডিউল হয়েছে",
    pending: "—",
  },
  ta: {
    title: "உங்கள் சந்திப்பு",
    steps: ["இடங்கள் தேடுகிறோம்", "நேரம் தேர்வு", "உறுதிப்படுத்தல்", "பதிவானது"],
    fields: { doctor: "மருத்துவர்", when: "தேதி & நேரம்", clinic: "மருத்துவமனை", duration: "காலம்" },
    awaiting: "ஆமென்று சொல்லி உறுதி செய்யுங்கள்",
    confirmed: "சந்திப்பு திட்டமிடப்பட்டது",
    pending: "—",
  },
  te: {
    title: "మీ అపాయింట్‌మెంట్",
    steps: ["స్లాట్లు వెతుకుతున్నాం", "సమయం ఎంపిక", "నిర్ధారణ", "బుక్ అయింది"],
    fields: { doctor: "డాక్టర్", when: "తేదీ & సమయం", clinic: "క్లినిక్", duration: "వ్యవధి" },
    awaiting: "అవును అని చెప్పి నిర్ధారించండి",
    confirmed: "మీటింగ్ షెడ్యూల్ అయింది",
    pending: "—",
  },
  kn: {
    title: "ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್",
    steps: ["ಸ್ಲಾಟ್ ಹುಡುಕುತ್ತಿದ್ದೇವೆ", "ಸಮಯ ಆಯ್ಕೆ", "ದೃಢೀಕರಣ", "ಬುಕ್ ಆಯಿತು"],
    fields: { doctor: "ವೈದ್ಯರು", when: "ದಿನಾಂಕ & ಸಮಯ", clinic: "ಕ್ಲಿನಿಕ್", duration: "ಅವಧಿ" },
    awaiting: "ಹೌದು ಎಂದು ಹೇಳಿ ದೃಢೀಕರಿಸಿ",
    confirmed: "ಮೀಟಿಂಗ್ ನಿಗದಿಯಾಯಿತು",
    pending: "—",
  },
  ml: {
    title: "നിങ്ങളുടെ അപ്പോയിന്റ്മെന്റ്",
    steps: ["സ്ലോട്ട് തിരയുന്നു", "സമയം തിരഞ്ഞെടുക്കൽ", "സ്ഥിരീകരണം", "ബുക്ക് ചെയ്തു"],
    fields: { doctor: "ഡോക്ടർ", when: "തീയതി & സമയം", clinic: "ക്ലിനിക്", duration: "കാലാവധി" },
    awaiting: "അതെ എന്ന് പറഞ്ഞ് സ്ഥിരീകരിക്കുക",
    confirmed: "മീറ്റിംഗ് ഷെഡ്യൂൾ ചെയ്തു",
    pending: "—",
  },
  mr: {
    title: "तुमची अपॉइंटमेंट",
    steps: ["स्लॉट शोधत आहोत", "वेळ निवड", "पुष्टी", "बुक झाली"],
    fields: { doctor: "डॉक्टर", when: "दिनांक & वेळ", clinic: "क्लिनिक", duration: "कालावधी" },
    awaiting: "होय म्हणून पुष्टी करा",
    confirmed: "मीटिंग शेड्यूल झाली",
    pending: "—",
  },
  gu: {
    title: "તમારી એપોઇન્ટમેન્ટ",
    steps: ["સ્લોટ શોધીએ છીએ", "સમય પસંદ", "પુષ્ટિ", "બુક થઈ ગઈ"],
    fields: { doctor: "ડૉક્ટર", when: "તારીખ & સમય", clinic: "ક્લિનિક", duration: "સમયગાળો" },
    awaiting: "હા કહીને પુષ્ટિ કરો",
    confirmed: "મીટિંગ શેડ્યૂલ થઈ",
    pending: "—",
  },
  pa: {
    title: "ਤੁਹਾਡੀ ਅਪਾਇੰਟਮੈਂਟ",
    steps: ["ਸਲਾਟ ਲੱਭ ਰਹੇ ਹਾਂ", "ਸਮਾਂ ਚੁਣਨਾ", "ਪੁਸ਼ਟੀ", "ਬੁਕ ਹੋ ਗਈ"],
    fields: { doctor: "ਡਾਕਟਰ", when: "ਤਾਰੀਖ & ਸਮਾਂ", clinic: "ਕਲੀਨਿਕ", duration: "ਮਿਆਦ" },
    awaiting: "ਹਾਂ ਕਹਿ ਕੇ ਪੁਸ਼ਟੀ ਕਰੋ",
    confirmed: "ਮੀਟਿੰਗ ਸ਼ੈਡਿਊਲ ਹੋ ਗਈ",
    pending: "—",
  },
};

interface SchedulingCardProps {
  scheduling: SchedulingState;
  language: LangCode;
  className?: string;
}

export default function SchedulingCard({
  scheduling,
  language,
  className,
}: SchedulingCardProps) {
  const L = LABELS[language] || LABELS.en;
  const { status, booking, step } = scheduling;
  const b = booking as Partial<Booking>;

  if (status === "idle") return null;

  const when =
    b.date && b.time ? `${b.date} · ${b.time}` : b.date || b.time || null;

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border-primary/20 shadow-lg shadow-primary/5",
        status === "confirmed" && "border-success/40 bg-success-soft/30",
        status === "awaiting_confirm" && "ring-2 ring-primary/25",
        className
      )}
    >
      <div className="px-4 py-3 border-b border-border bg-primary-soft/40 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Calendar className="h-4 w-4 text-primary shrink-0" strokeWidth={1.8} />
          <span className="text-sm font-semibold truncate">{L.title}</span>
        </div>
        <StatusPill status={status} label={L} />
      </div>

      <StepBar steps={L.steps} activeStep={status === "confirmed" ? 3 : step} />

      <div className="p-4 space-y-3">
        <FieldRow
          icon={Stethoscope}
          label={L.fields.doctor}
          value={b.doctor}
          highlight={step >= 1}
        />
        <FieldRow
          icon={Clock}
          label={L.fields.when}
          value={when}
          highlight={step >= 2}
        />
        <FieldRow
          icon={MapPin}
          label={L.fields.clinic}
          value={b.clinic}
          highlight={step >= 2}
        />
        <FieldRow
          icon={User}
          label={L.fields.duration}
          value={b.duration}
          highlight={step >= 2}
        />
      </div>

      {status === "awaiting_confirm" && (
        <p className="px-4 pb-4 text-xs text-primary font-medium animate-pulse">
          {L.awaiting}
        </p>
      )}
    </Card>
  );
}

function StatusPill({
  status,
  label,
}: {
  status: SchedulingStatus;
  label: (typeof LABELS)["en"];
}) {
  if (status === "confirmed") {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-success bg-success-soft px-2 py-0.5 rounded-full shrink-0">
        <Check className="h-3 w-3" />
        {label.confirmed}
      </span>
    );
  }
  if (status === "awaiting_confirm") {
    return (
      <span className="text-[11px] font-medium text-primary bg-primary/10 px-2 py-0.5 rounded-full shrink-0">
        {label.steps[2]}
      </span>
    );
  }
  return (
    <span className="text-[11px] text-muted-foreground bg-secondary px-2 py-0.5 rounded-full shrink-0 animate-pulse">
      {label.steps[Math.min(2, 1)]}
    </span>
  );
}

function StepBar({ steps, activeStep }: { steps: string[]; activeStep: number }) {
  return (
    <ol className="flex px-2 pt-3 pb-1 gap-0.5" aria-label="Booking progress">
      {steps.map((label, i) => {
        const done = i < activeStep;
        const current = i === activeStep;
        return (
          <li key={label} className="flex-1 flex flex-col items-center gap-1 min-w-0">
            <span
              className={cn(
                "h-1.5 w-full rounded-full transition-colors duration-500",
                done && "bg-success",
                current && !done && "bg-primary animate-pulse",
                !done && !current && "bg-border"
              )}
            />
            <span
              className={cn(
                "text-[9px] leading-tight text-center px-0.5 truncate w-full",
                current ? "text-primary font-medium" : "text-muted-foreground"
              )}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function FieldRow({
  icon: Icon,
  label,
  value,
  highlight,
}: {
  icon: typeof Stethoscope;
  label: string;
  value?: string | null;
  highlight?: boolean;
}) {
  const filled = Boolean(value);
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg px-2 py-1.5 -mx-2 transition-colors",
        highlight && !filled && "bg-primary-soft/50",
        filled && "bg-success-soft/20"
      )}
    >
      <Icon
        className={cn(
          "h-4 w-4 mt-0.5 shrink-0",
          filled ? "text-success" : "text-muted-foreground"
        )}
        strokeWidth={1.6}
      />
      <div className="min-w-0 flex-1">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
          {label}
        </p>
        <p
          className={cn(
            "text-sm font-medium truncate",
            filled ? "text-foreground" : "text-muted-foreground/60 italic"
          )}
        >
          {value || "…"}
        </p>
      </div>
      {filled && (
        <Check className="h-3.5 w-3.5 text-success shrink-0 mt-1" aria-hidden />
      )}
    </div>
  );
}
