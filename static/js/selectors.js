// Pure derivations from server state. All tolerate null/empty state.
import { compareTeams } from "./util.js";

const findSchedule = (state, id) =>
  (state?.schedules || []).find((schedule) => schedule.id === id) || null;

/** The schedule the server considers "current" (set on generate / print). */
export const getActiveSchedule = (state) => {
  if (!state) {
    return null;
  }
  const schedules = state.schedules || [];
  const activeId = state.active_schedule_id;
  if (schedules.length && activeId) {
    return findSchedule(state, activeId);
  }
  // Pre-schedules-list state: only top-level slots exist.
  if (!schedules.length && state.slots) {
    return { id: "legacy", label: "Current", slots: state.slots };
  }
  return null;
};

/**
 * Id of the schedule this device is looking at: its own pick if that schedule
 * still exists, otherwise it follows the server's active schedule.
 */
export const getViewedScheduleId = (state, ui) => {
  const picked = ui?.viewScheduleId;
  if (picked != null && findSchedule(state, picked)) {
    return picked;
  }
  return getActiveSchedule(state)?.id ?? null;
};

/**
 * The schedule this device renders. Slots must be read from the returned
 * schedule, never from top-level `state.slots` (that is the server's active one).
 */
export const getViewedSchedule = (state, ui) => {
  const picked = ui?.viewScheduleId;
  if (picked != null) {
    const schedule = findSchedule(state, picked);
    if (schedule) {
      return schedule;
    }
  }
  return getActiveSchedule(state);
};

/** schedule_id to send with actions; undefined lets the server use its active one. */
export const getViewedScheduleIdForRequest = (state, ui) => {
  const schedule = getViewedSchedule(state, ui);
  return schedule && schedule.id !== "legacy" ? schedule.id : undefined;
};

export const hasSchedule = (state, ui) => {
  const viewed = getViewedSchedule(state, ui);
  return Boolean(viewed && Array.isArray(viewed.slots) && viewed.slots.length);
};

export const isNoShowSchedule = (schedule) =>
  schedule?.type === "noshow" || schedule?.type === "printed-noshow";

/** Check-off / no-show actions are only allowed once the viewed schedule's kind is printed. */
export const canEditViewedSchedule = (state, ui) => {
  if (isNoShowSchedule(getViewedSchedule(state, ui))) {
    return Boolean(state?.noshow_locked);
  }
  return Boolean(state?.locked);
};

const compareJudge = (a, b) => {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) {
    return na - nb;
  }
  return String(a).localeCompare(String(b));
};

/** Map(judge_id -> slots sorted by start), judges in ascending order. */
export const groupByJudge = (slots) => {
  const grouped = new Map();
  (slots || []).forEach((slot) => {
    if (!grouped.has(slot.judge_id)) {
      grouped.set(slot.judge_id, []);
    }
    grouped.get(slot.judge_id).push(slot);
  });
  grouped.forEach((list) =>
    list.sort((a, b) => Date.parse(a.start) - Date.parse(b.start))
  );
  return new Map([...grouped.entries()].sort((a, b) => compareJudge(a[0], b[0])));
};

/**
 * Every team the event knows about, with its interview status, in team-number order.
 * Status is one fact per team (the server sets it on every schedule version), so
 * any slot that mentions the team will do. Teams that never got a suggested time
 * (`unassigned`) are included; teams marked not competing are flagged.
 */
export const teamRoster = (state) => {
  const byTeam = new Map();
  const notCompeting = new Set((state?.not_competing || []).map(String));
  const add = (team, status) => {
    const key = String(team);
    const current = byTeam.get(key);
    // "checked" wins over anything; then "no-show"; then whatever we saw first.
    const rank = (s) => (s === "checked" ? 2 : s === "no-show" ? 1 : 0);
    if (!current || rank(status) > rank(current.status)) {
      byTeam.set(key, { team: key, status: status || "scheduled" });
    }
  };
  (state?.schedules || []).forEach((schedule) =>
    (schedule.slots || []).forEach((slot) => {
      if (slot.team) add(slot.team, slot.status);
    })
  );
  (state?.unassigned || []).forEach((team) => add(team, "scheduled"));
  return [...byTeam.values()]
    .map((entry) => ({ ...entry, notCompeting: notCompeting.has(entry.team) }))
    .sort((a, b) => compareTeams(a.team, b.team));
};

/** Teams that still need an interview: not checked off and still competing. */
export const teamsStillWaiting = (state) =>
  teamRoster(state).filter((entry) => entry.status !== "checked" && !entry.notCompeting);

// ------------------------------------------------------------ match ranges

const timelineCache = new WeakMap();

/** Every scheduled match once, earliest first: [{at: epoch ms, label: "Q12"}]. */
export const matchTimeline = (state) => {
  const source = state?.team_matches;
  if (!source || typeof source !== "object") return [];
  if (timelineCache.has(source)) return timelineCache.get(source);
  const byLabel = new Map();
  Object.values(source).forEach((entries) =>
    (entries || []).forEach((entry) => {
      const at = Date.parse(entry?.time);
      if (Number.isFinite(at) && entry?.label && !byLabel.has(entry.label)) byLabel.set(entry.label, at);
    })
  );
  const timeline = [...byLabel.entries()]
    .map(([label, at]) => ({ label, at }))
    .sort((a, b) => a.at - b.at || compareTeams(a.label, b.label));
  timelineCache.set(source, timeline);
  return timeline;
};

/**
 * Which matches are on the field during [start, end): "Q13–Q14", "Q13", or, when
 * no match starts in the window (before quals, lunch), "after Q24" / "before Q1".
 */
export const matchRange = (state, startIso, endIso) => {
  const timeline = matchTimeline(state);
  const start = Date.parse(startIso);
  const end = Date.parse(endIso);
  if (!timeline.length || !Number.isFinite(start) || !Number.isFinite(end)) return "";
  const during = timeline.filter((match) => match.at >= start && match.at < end);
  if (during.length) {
    const first = during[0].label;
    const last = during[during.length - 1].label;
    return first === last ? first : `${first}–${last}`;
  }
  const before = timeline.filter((match) => match.at < start).pop();
  if (before) return `after ${before.label}`;
  return `before ${timeline[0].label}`;
};
