// Setup form: event stats, time autofill from an uploaded schedule, lock state.
import { els, setStatus } from "./dom.js";
import { isBusy } from "./busy.js";
import { formatTime, nowLocalInput, futureLocalInput } from "./util.js";
import {
  parseMatchesFromText,
  extractMatchStartTime,
  parseQualsStartTime,
  extractTeamCount,
  extractTeamCountFromText,
} from "./parsing.js";

const STAT_DEFAULTS = {
  rounds_min: 8,
  rounds_max: 8,
  spacing_min: 3,
  spacing_max: 14,
  spacing_avg: 7,
  repeat_min: 1,
  repeat_max: 2,
  repeat_avg: 1,
  distinct_min: 21,
  distinct_max: 24,
  distinct_avg: 23,
};

export const getEventStats = () => {
  const stats = {};
  Object.entries(STAT_DEFAULTS).forEach(([key, fallback]) => {
    stats[key] = Number(els.stats[key]?.value || fallback);
  });
  return stats;
};

export const setEventStats = (stats) => {
  if (!stats) return;
  Object.keys(STAT_DEFAULTS).forEach((key) => {
    const el = els.stats[key];
    // Never clobber a field the user is typing in.
    if (el && stats[key] != null && el !== document.activeElement) {
      el.value = stats[key];
    }
  });
};

let lastStatsKey = null;

/** Subscriber: push server event_stats into the inputs only when they change. */
export const applyEventStats = (state) => {
  const stats = state?.config?.event_stats;
  if (!stats || !Object.keys(stats).length) return;
  const key = JSON.stringify(stats);
  if (key === lastStatsKey) return;
  lastStatsKey = key;
  setEventStats(stats);
};

let lastLockKey = null;

/** Subscriber: disable generate buttons when locked; announce lock changes once. */
export const applyLockState = (state) => {
  const locked = Boolean(state?.locked);
  const noshowLocked = Boolean(state?.noshow_locked);

  [
    [els.generateBtn, locked],
    [els.generateNoShowBtn, noshowLocked],
  ].forEach(([button, isLocked]) => {
    if (!button) return;
    button.dataset.locked = String(isLocked);
    button.disabled = isLocked || isBusy(button);
  });

  const key = `${locked}|${noshowLocked}`;
  if (key === lastLockKey) return;
  lastLockKey = key;
  if (locked) {
    setStatus("Schedule locked after printing. Reset to generate again.");
  } else if (noshowLocked) {
    setStatus("No-show schedule locked after printing. Reset to generate again.");
  }
};

export const updateJudgingTimesFromFile = (text) => {
  const matches = parseMatchesFromText(text);
  const matchStart = extractMatchStartTime(matches) || parseQualsStartTime(text);
  if (!matchStart) {
    return;
  }

  if (els.endTime) {
    els.endTime.value = formatTime(matchStart);
  }

  const teamCount = extractTeamCount(matches) || extractTeamCountFromText(text);
  const judgePairs = Math.max(1, Number(els.judgePairs?.value || 1));
  const slotMinutes = Math.max(1, Number(els.slotMinutes?.value || 10));

  const defaultDuration = 100;
  const computedDuration =
    teamCount > 0 ? Math.ceil(teamCount / judgePairs) * slotMinutes : defaultDuration;

  if (els.startTime) {
    const start = new Date(matchStart.getTime() - computedDuration * 60000);
    els.startTime.value = formatTime(start);
  }
};

export const clearMatchScheduleInputs = () => {
  els.matchSchedule.value = "";
  els.finalMatchSchedule.value = "";
  els.fileInput.value = "";
  els.finalFileInput.value = "";
};

export const initSetup = () => {
  if (!els.startTime.value) {
    els.startTime.value = nowLocalInput();
  }
  if (!els.endTime.value) {
    els.endTime.value = futureLocalInput(100);
  }

  const refreshTimes = () => {
    if (els.matchSchedule.value) {
      updateJudgingTimesFromFile(els.matchSchedule.value);
    }
  };
  els.judgePairs.addEventListener("change", refreshTimes);
  els.slotMinutes.addEventListener("change", refreshTimes);
};
