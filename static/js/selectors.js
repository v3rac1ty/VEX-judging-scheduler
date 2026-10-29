// Pure derivations from server state. All tolerate null/empty state.

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
