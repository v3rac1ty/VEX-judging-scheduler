// Pure derivations from server state. All tolerate null/empty state.

export const getActiveSchedule = (state) => {
  if (!state) {
    return null;
  }
  const schedules = state.schedules || [];
  const activeId = state.active_schedule_id;
  if (schedules.length && activeId) {
    return schedules.find((schedule) => schedule.id === activeId) || null;
  }
  if (state.slots) {
    return { id: "legacy", label: "Current", slots: state.slots };
  }
  return null;
};

export const hasSchedule = (state) => {
  const active = getActiveSchedule(state);
  return Boolean(active && Array.isArray(active.slots) && active.slots.length);
};

export const isNoShowSchedule = (schedule) =>
  schedule?.type === "noshow" || schedule?.type === "printed-noshow";

/** Check-off / no-show actions are only allowed once the relevant schedule is printed. */
export const canEditActiveSchedule = (state) => {
  if (isNoShowSchedule(getActiveSchedule(state))) {
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
