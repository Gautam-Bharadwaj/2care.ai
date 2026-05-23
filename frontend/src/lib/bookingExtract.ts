import type { Booking } from "./types";

export type SchedulingStatus = "idle" | "in_progress" | "awaiting_confirm" | "confirmed";

export interface SchedulingState {
  status: SchedulingStatus;
  booking: Partial<Booking>;
  /** 0=find slots, 1=details, 2=confirm, 3=done */
  step: number;
}

export const EMPTY_SCHEDULING: SchedulingState = {
  status: "idle",
  booking: {},
  step: 0,
};

const DEFAULT_DOCTOR = "Dr. Anita Mehra";
const DEFAULT_SPECIALTY = "Cardiology";
const DEFAULT_CLINIC = "MedFirst · Banjara Hills";
const DEFAULT_DURATION = "20 min";

/** Merge agent lines into a growing appointment draft. */
export function updateSchedulingFromAgent(
  prev: SchedulingState,
  agentText: string
): SchedulingState {
  const text = agentText.trim();
  if (!text) return prev;

  const booking = { ...prev.booking };
  let status = prev.status;
  let step = prev.step;

  const doctor = extractDoctor(text);
  if (doctor) booking.doctor = doctor;

  const specialty = extractSpecialty(text);
  if (specialty) booking.specialty = specialty;

  const time = extractTime(text);
  if (time) booking.time = time;

  const date = extractDate(text);
  if (date) booking.date = date;

  const clinic = extractClinic(text);
  if (clinic) booking.clinic = clinic;

  if (mentionsSlots(text)) {
    status = "in_progress";
    step = Math.max(step, 1);
    if (!booking.doctor) booking.doctor = DEFAULT_DOCTOR;
  }

  if (booking.doctor || booking.time || booking.date) {
    status = "in_progress";
    step = Math.max(step, 2);
    if (!booking.doctor) booking.doctor = DEFAULT_DOCTOR;
    if (!booking.specialty) booking.specialty = DEFAULT_SPECIALTY;
    if (!booking.clinic) booking.clinic = DEFAULT_CLINIC;
    if (!booking.duration) booking.duration = DEFAULT_DURATION;
  }

  if (asksConfirmation(text)) {
    status = "awaiting_confirm";
    step = 3;
  }

  if (isConfirmed(text)) {
    status = "confirmed";
    step = 3;
  }

  if (status === "idle" && (booking.doctor || booking.time || booking.date)) {
    status = "in_progress";
    step = 2;
  }

  return { status, booking, step };
}

export function finalizeBooking(
  draft: Partial<Booking>,
  confirmed?: Partial<Booking>
): Booking {
  const base = confirmed ?? {};
  return {
    doctor: base.doctor || draft.doctor || DEFAULT_DOCTOR,
    specialty: base.specialty || draft.specialty || DEFAULT_SPECIALTY,
    date: base.date || draft.date || formatDefaultDate(),
    time: base.time || draft.time || "9:00 AM",
    clinic: base.clinic || draft.clinic || DEFAULT_CLINIC,
    duration: base.duration || draft.duration || DEFAULT_DURATION,
    startsAtIso: base.startsAtIso || draft.startsAtIso,
    endsAtIso: base.endsAtIso || draft.endsAtIso,
    referenceId: base.referenceId || draft.referenceId,
  };
}

function formatDefaultDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toLocaleDateString("en-IN", { weekday: "short", month: "short", day: "numeric" });
}

function mentionsSlots(t: string): boolean {
  return /\b(slot|slots|available|availability|check|देख|समय|மணி|గంట|ಇದೆ|ઉપલબ્ધ)\b/i.test(t);
}

function asksConfirmation(t: string): boolean {
  return /\b(confirm|shall i book|book it for you|does that work|works for you|क्या यह समय|बुक कर दू|பதிவு செய்யவா|నిర్ధారించ)\b/i.test(
    t
  );
}

function isConfirmed(t: string): boolean {
  if (!t) return false;
  const text = t.toLowerCase();
  const lat =
    /\b(booked|confirmed|appointment is set|booking complete)\b/.test(text) &&
    /\b(sms|sent|confirmation)\b/.test(text);
  const indic =
    /(बुक|कन्फर्म|पुष्टि|பதிவ|எஸ்எம்எஸ்|बुकिंग)/.test(text) &&
    /(sms|एसएमएस|भेज|sent|அனுப்ப)/i.test(text);
  return lat || indic;
}

function extractDoctor(t: string): string | undefined {
  const m =
    t.match(/\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/) ||
    t.match(/डॉ\.?\s*([^\s,।]+(?:\s+[^\s,।]+)?)/) ||
    t.match(/டாக்டர்\s+([^\s,]+(?:\s+[^\s,]+)?)/);
  if (m) return `Dr. ${m[1].replace(/^Dr\.?\s*/i, "").trim()}`;
  if (/mehra|मेहरा|மேஹ்ரா/i.test(t)) return DEFAULT_DOCTOR;
  return undefined;
}

function extractSpecialty(t: string): string | undefined {
  const map: [RegExp, string][] = [
    [/cardio|cardiology|हृदय|இருதய|ಹೃದಯ/i, "Cardiology"],
    [/derma|skin|त्वचा/i, "Dermatology"],
    [/general|physician|सामान्य/i, "General Medicine"],
  ];
  for (const [re, name] of map) {
    if (re.test(t)) return name;
  }
  return undefined;
}

function extractTime(t: string): string | undefined {
  const m = t.match(/\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM|am|pm)\b/);
  if (m) {
    const h = m[1];
    const min = m[2] ? `:${m[2]}` : ":00";
    return `${h}${min} ${m[3].toUpperCase()}`;
  }
  if (/नौ बजे|9 बजे/i.test(t)) return "9:00 AM";
  if (/ஒன்பது மணி|9 மணி/i.test(t)) return "9:00 AM";
  if (/సాయంత్రం|evening/i.test(t) && !m) return "5:00 PM";
  if (/सुबह|morning|காலை/i.test(t) && !/\d/.test(t)) return "9:00 AM";
  return undefined;
}

function extractDate(t: string): string | undefined {
  if (/\btomorrow\b|कल|नाळे|நாளை|నాళె|ನಾಳೆ/i.test(t)) return formatDefaultDate();
  const m = t.match(
    /\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b/i
  );
  if (m) return m[0];
  return undefined;
}

function extractClinic(t: string): string | undefined {
  if (/medfirst|banjara/i.test(t)) return DEFAULT_CLINIC;
  return undefined;
}

export function schedulingVisible(s: SchedulingState): boolean {
  return s.status !== "idle";
}
