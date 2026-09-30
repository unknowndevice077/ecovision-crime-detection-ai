// Timestamps the database writes itself (CURRENT_TIMESTAMP defaults, NOW()
// -- created_at, last_login, audit entries, decisions, report requests) are
// UTC but carry no zone: "2026-09-30 18:02:20". new Date() reads a zone-less
// string as LOCAL time, so every one of them showed 8 hours early in Manila.
// This reads them as UTC. Strings that already name a zone pass through.
// (Incident occurred_date/occurred_time are written as local time by Python
// and are displayed as-is, not through this.)
const NAIVE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function serverDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  return new Date(NAIVE.test(value) ? value.replace(" ", "T") + "Z" : value);
}

export function serverDateTime(value: string | null | undefined, fallback = ""): string {
  const d = serverDate(value);
  return d ? d.toLocaleString() : fallback;
}

export function serverDay(value: string | null | undefined, fallback = ""): string {
  const d = serverDate(value);
  return d ? d.toLocaleDateString() : fallback;
}
