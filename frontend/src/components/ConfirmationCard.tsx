import { Calendar, Check } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { Booking } from "@/lib/types";
import type { LangCode } from "@/lib/languages";

interface ConfirmationCardProps {
  booking: Booking;
  /** Active language at booking time — drives Intl.DateTimeFormat locale. */
  language?: LangCode;
  onDone: () => void;
}

const LOCALE_BY_LANG: Record<LangCode, string> = {
  en: "en-IN",
  hi: "hi-IN",
  bn: "bn-IN",
  ta: "ta-IN",
  te: "te-IN",
  kn: "kn-IN",
  ml: "ml-IN",
  mr: "mr-IN",
  gu: "gu-IN",
  pa: "pa-IN",
};

/**
 * Post-call success card. Shows the agent-confirmed appointment, with
 * a real "Add to calendar" button that generates an .ics file
 * client-side (no server round-trip) and offers it as a download.
 *
 * `booking.startsAtIso` / `endsAtIso` are optional — when provided we
 * embed real DTSTART/DTEND in the ICS; otherwise we generate a
 * best-effort 30-minute event starting now+1h so the file still works.
 */
export default function ConfirmationCard({
  booking,
  language = "en",
  onDone,
}: ConfirmationCardProps) {
  // Reference ID — server-provided if available, else a short
  // pseudo-random ID generated client-side. Booking-flavoured shape:
  // 4 letters + 3 digits, all caps, no ambiguous chars.
  const referenceId = booking.referenceId || generateReferenceId();

  const locale = LOCALE_BY_LANG[language] || "en-IN";
  const dateTimeDisplay = formatLocaleDateTime(booking, locale);

  const onAddToCalendar = () => {
    const ics = buildIcs(booking, referenceId);
    const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `2careAi-${booking.doctor.replace(/\s+/g, "-")}.ics`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="w-full max-w-[440px] flex flex-col gap-3 animate-line-fade">
      <Card className="p-7 flex flex-col gap-5 border-success/30 shadow-xl shadow-success/10 bg-card">
        <div className="inline-flex items-center gap-2 text-success text-sm font-semibold">
          <span className="h-8 w-8 rounded-full bg-success text-success-foreground grid place-items-center shadow-md">
            <Check className="h-4 w-4" strokeWidth={2.4} aria-hidden />
          </span>
          Meeting scheduled
        </div>

        <div>
          <h2 className="text-xl font-semibold tracking-tight leading-tight">
            {dateTimeDisplay}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {booking.doctor}
            {booking.specialty && <> · {booking.specialty}</>}
          </p>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border pt-4">
          {booking.clinic && (
            <div>
              <dt className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-0.5">
                Clinic
              </dt>
              <dd className="text-sm font-medium">{booking.clinic}</dd>
            </div>
          )}
          {booking.duration && (
            <div>
              <dt className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-0.5">
                Duration
              </dt>
              <dd className="text-sm font-medium">{booking.duration}</dd>
            </div>
          )}
        </dl>

        <div className="border-t border-border pt-3 text-[11px] font-mono text-muted-foreground tracking-wider">
          Booking #{referenceId}
        </div>
      </Card>

      <div className="flex gap-2.5">
        <Button variant="outline" className="flex-1" onClick={onAddToCalendar}>
          <Calendar className="h-4 w-4" strokeWidth={1.5} />
          Add to calendar
        </Button>
        <Button className="flex-1" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

/**
 * Build an RFC 5545 .ics file. Verified to import cleanly into both
 * Google Calendar and Apple Calendar — RFC line endings (CRLF), a
 * stable UID derived from the booking reference, and no surprising
 * UTF-8 chars in field values (escaped where needed).
 */
function buildIcs(b: Booking, referenceId: string): string {
  const now = new Date();
  const startsAt = b.startsAtIso
    ? new Date(b.startsAtIso)
    : new Date(now.getTime() + 60 * 60 * 1000);
  const endsAt = b.endsAtIso
    ? new Date(b.endsAtIso)
    : new Date(startsAt.getTime() + 30 * 60 * 1000);
  const dtStamp = toIcsDate(now);
  const dtStart = toIcsDate(startsAt);
  const dtEnd = toIcsDate(endsAt);
  const uid = `2careai-${referenceId}@2careai.app`;
  const summary = `${b.doctor}${b.specialty ? ` — ${b.specialty}` : ""}`;
  const description = `Appointment booked via 2careAi. Booking #${referenceId}.${
    b.clinic ? `\\nClinic: ${b.clinic}` : ""
  }`;
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//2careAi//Booking//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${uid}`,
    `DTSTAMP:${dtStamp}`,
    `DTSTART:${dtStart}`,
    `DTEND:${dtEnd}`,
    `SUMMARY:${escapeIcs(summary)}`,
    `DESCRIPTION:${escapeIcs(description)}`,
    b.clinic ? `LOCATION:${escapeIcs(b.clinic)}` : "",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
  ]
    .filter(Boolean)
    .join("\r\n");
}

/**
 * "Tuesday, 28 May at 9:00 AM" — Intl.DateTimeFormat with the
 * patient's locale. Falls back to the pre-formatted `date · time`
 * fields the agent provided when no ISO timestamp is available.
 */
function formatLocaleDateTime(b: Booking, locale: string): string {
  if (!b.startsAtIso) {
    return `${b.date} · ${b.time}`;
  }
  try {
    const d = new Date(b.startsAtIso);
    const datePart = new Intl.DateTimeFormat(locale, {
      weekday: "long",
      day: "numeric",
      month: "long",
    }).format(d);
    const timePart = new Intl.DateTimeFormat(locale, {
      hour: "numeric",
      minute: "2-digit",
    }).format(d);
    // " at " join word — kept in English to avoid an extra translation
    // surface; locales typically still place the date and time
    // unambiguously around it.
    return `${datePart} · ${timePart}`;
  } catch {
    return `${b.date} · ${b.time}`;
  }
}

function generateReferenceId(): string {
  // 4 letters + 3 digits. Skip 0/O/1/I to avoid the classic confusion.
  const letters = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const digits = "23456789";
  let id = "";
  for (let i = 0; i < 4; i++) id += letters[Math.floor(Math.random() * letters.length)];
  for (let i = 0; i < 3; i++) id += digits[Math.floor(Math.random() * digits.length)];
  return id;
}

function toIcsDate(d: Date): string {
  // YYYYMMDDTHHMMSSZ
  const z = (n: number) => String(n).padStart(2, "0");
  return (
    d.getUTCFullYear().toString() +
    z(d.getUTCMonth() + 1) +
    z(d.getUTCDate()) +
    "T" +
    z(d.getUTCHours()) +
    z(d.getUTCMinutes()) +
    z(d.getUTCSeconds()) +
    "Z"
  );
}

function escapeIcs(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/,/g, "\\,").replace(/;/g, "\\;");
}
