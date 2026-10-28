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

export const formatTime = (date) =>
  date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });

export const nowLocalInput = () => formatTime(new Date());

export const futureLocalInput = (minutesAhead) => {
  const date = new Date();
  date.setMinutes(date.getMinutes() + minutesAhead);
  return formatTime(date);
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
