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

const plural = (count, one, many) => `${count} ${Number(count) === 1 ? one : many}`;

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
    return { text: [name, preview.error].filter(Boolean).join(" · "), error: true };
  }
  const parts = [name];
  if (preview.match_count != null) parts.push(plural(preview.match_count, "match", "matches"));
  if (preview.team_count != null) parts.push(plural(preview.team_count, "team", "teams"));
  const first = clockFromIso(preview.first_match);
  if (first) parts.push(`first qual ${first}`);
  return { text: parts.filter(Boolean).join(" · "), error: false };
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
