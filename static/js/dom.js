// Single place for element lookups plus small DOM helpers.
// Module scripts are deferred, so the document is fully parsed on import.

const $ = (id) => document.getElementById(id);

export const els = {
  status: $("status"),
  schedule: $("schedule"),
  noShow: $("noShowPanel"),
  printTable: $("printTable"),
  scheduleSelect: $("scheduleSelect"),
  teamSearch: $("teamSearch"),

  judgePairs: $("judgePairs"),
  slotMinutes: $("slotMinutes"),
  blockMinutes: $("blockMinutes"),
  startTime: $("startTime"),
  endTime: $("endTime"),

  stats: {
    rounds_min: $("roundsMin"),
    rounds_max: $("roundsMax"),
    spacing_min: $("spacingMin"),
    spacing_max: $("spacingMax"),
    spacing_avg: $("spacingAvg"),
    repeat_min: $("repeatMin"),
    repeat_max: $("repeatMax"),
    repeat_avg: $("repeatAvg"),
    distinct_min: $("distinctMin"),
    distinct_max: $("distinctMax"),
    distinct_avg: $("distinctAvg"),
  },

  matchSchedule: $("matchSchedule"),
  dropzone: $("dropzone"),
  fileInput: $("fileInput"),
  finalDropzone: $("finalDropzone"),
  finalFileInput: $("finalFileInput"),
  finalMatchSchedule: $("finalMatchSchedule"),

  generateBtn: $("generateBtn"),
  printBtn: $("printBtn"),
  generateNoShowBtn: $("generateNoShowBtn"),
  resetBtn: $("resetBtn"),
};

export const setStatus = (message) => {
  if (els.status) {
    els.status.textContent = message;
  }
};

const cache = new WeakMap();

/**
 * Replace el.innerHTML only when the markup actually changed, preserving
 * scroll position (element + window) and keyboard focus across the swap.
 * Callers must pass already-escaped markup.
 */
export const patchHtml = (el, html) => {
  if (!el || cache.get(el) === html) {
    return false;
  }

  const scrollTop = el.scrollTop;
  const scrollLeft = el.scrollLeft;
  const winX = window.scrollX;
  const winY = window.scrollY;

  let focusSelector = null;
  const active = document.activeElement;
  if (active && el.contains(active) && active.dataset?.action && active.dataset?.team) {
    focusSelector =
      `[data-action="${CSS.escape(active.dataset.action)}"]` +
      `[data-team="${CSS.escape(active.dataset.team)}"]`;
  }

  el.innerHTML = html;
  cache.set(el, html);

  el.scrollTop = scrollTop;
  el.scrollLeft = scrollLeft;
  if (window.scrollX !== winX || window.scrollY !== winY) {
    window.scrollTo(winX, winY);
  }

  if (focusSelector) {
    const target = el.querySelector(focusSelector);
    if (target && !target.disabled) {
      target.focus({ preventScroll: true });
    }
  }
  return true;
};
