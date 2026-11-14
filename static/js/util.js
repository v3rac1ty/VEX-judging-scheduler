// Pure helpers (no DOM access) so they can be unit-checked under plain Node.

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Escape any value for safe interpolation into HTML text or quoted attributes. */
export const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);

/**
 * "2026-10-28T11:00:00-05:00" -> "11:00 AM", read straight from the string so
 * every device shows the event's wall-clock time regardless of its own zone.
 */
export const clockFromIso = (iso) => {
  const match = /T(\d{2}):(\d{2})/.exec(String(iso ?? ""));
  if (!match) {
    return "";
  }
  const hours = Number(match[1]);
  return `${hours % 12 || 12}:${match[2]} ${hours >= 12 ? "PM" : "AM"}`;
};



/**
 * One-line summary of a server-computed upload preview, e.g.
 * "initial.log · 48 matches · 24 teams · first qual 11:00 AM".
 * Returns {text, error}; empty text means "show nothing".
 */
export const describePreview = (name, preview) => {
  if (!preview) {
    return { text: name || "", error: false };
  }
  if (preview.error) {
    return { text: [name, preview.error].filter(Boolean).join(", "), error: true };
  }
  const parts = [name];
  if (preview.match_count != null) parts.push(plural(preview.match_count, "match", "matches"));
  if (preview.team_count != null) parts.push(plural(preview.team_count, "team", "teams"));
  const first = clockFromIso(preview.first_match);
  if (first) parts.push(`first qual ${first}`);
  const lunchStart = clockFromIso(preview.lunch_start);
  const lunchEnd = clockFromIso(preview.lunch_end);
  if (lunchStart && lunchEnd) parts.push(`lunch ${lunchStart} to ${lunchEnd}`);
  const elims = clockFromIso(preview.elims_start);
  if (elims) parts.push(`eliminations ${elims}`);
  return { text: parts.filter(Boolean).join(", "), error: false };
};

/** ISO timestamp -> local "09:05 AM" style text; empty string if unparseable. */
export const toLocalTime = (isoString) => {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

/** "start - end" range text for a slot-like object. */
export const timeRange = (start, end) => `${toLocalTime(start)} - ${toLocalTime(end)}`;

/** Numerical team order: 99A < 1234A < 1234B < 5678X (names without a number go last). */
export const compareTeams = (a, b) => {
  const ka = String(a ?? "").trim();
  const kb = String(b ?? "").trim();
  const na = /^\d/.test(ka);
  const nb = /^\d/.test(kb);
  if (na !== nb) return na ? -1 : 1;
  return ka.localeCompare(kb, undefined, { numeric: true, sensitivity: "base" });
};

/** "9:05 AM" / "9 am" / "13:30" -> minutes after midnight; null when unreadable. */
export const parseClock = (text) => {
  const value = String(text ?? "").trim();
  let match = /^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$/i.exec(value);
  if (match) {
    const hour = Number(match[1]);
    const minute = Number(match[2] || 0);
    if (hour < 1 || hour > 12 || minute > 59) return null;
    const pm = match[3].toLowerCase() === "p";
    return ((hour % 12) + (pm ? 12 : 0)) * 60 + minute;
  }
  match = /^(\d{1,2}):(\d{2})$/.exec(value);
  if (match) {
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour <= 23 && minute <= 59) return hour * 60 + minute;
  }
  return null;
};

/** Minutes after midnight -> "9:05 AM". */
export const formatClock = (minutes) => {
  const total = ((Math.round(minutes) % 1440) + 1440) % 1440;
  const hours = Math.floor(total / 60);
  const mins = String(total % 60).padStart(2, "0");
  return `${hours % 12 || 12}:${mins} ${hours >= 12 ? "PM" : "AM"}`;
};

/**
 * How many interview slots one judge pair gets between open and close (minutes),
 * skipping a lunch break [lunchStart, lunchEnd). Mirrors the server's build_slots.
 */
export const slotsInWindow = (open, close, slotMinutes, lunch) => {
  if (open == null || close == null || !(slotMinutes > 0)) return 0;
  let cursor = Math.ceil(open / 5) * 5;
  let count = 0;
  for (let guard = 0; guard < 2000; guard += 1) {
    if (lunch && cursor < lunch[1] && cursor + slotMinutes > lunch[0]) cursor = lunch[1];
    if (cursor + slotMinutes > close) break;
    count += 1;
    cursor += slotMinutes;
  }
  return count;
};

export const plural = (count, one, many) => `${count} ${Number(count) === 1 ? one : many}`;
