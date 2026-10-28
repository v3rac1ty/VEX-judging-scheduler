// User-initiated operations. Each one posts to the API, then feeds any returned
// state through setState() so every renderer stays in sync.
import { api } from "./api.js";
import { els, setStatus } from "./dom.js";
import { withBusy } from "./busy.js";
import { getState, setState, setUi } from "./store.js";
import { canEditActiveSchedule, hasSchedule } from "./selectors.js";
import { renderScheduleSelect } from "./render/scheduleSelect.js";
import { getEventStats, clearMatchScheduleInputs } from "./setup.js";

// Guards against duplicate submits of the same logical action (e.g. the same
// team's check-off) even after the button has been re-rendered.
const inFlight = new Set();

/** POST and, on success, apply returned state. Returns the response data or null. */
const post = async (path, body, fallbackError) => {
  const result = await api.post(path, body);
  if (!result.ok) {
    setStatus(result.error || fallbackError);
    return null;
  }
  setState(result.data);
  return result.data;
};

export const loadInitialState = async () => {
  const result = await api.get("/api/state");
  if (!result.ok) {
    setStatus(result.error);
    setState(null);
    return;
  }
  setState(result.data);
  if (!getState()) {
    clearMatchScheduleInputs();
  }
};

export const generateSchedule = async () => {
  await withBusy(els.generateBtn, async () => {
    setStatus("Generating schedule...");
    const data = await post(
      "/api/generate",
      {
        judge_pairs: els.judgePairs.value,
        slot_minutes: els.slotMinutes.value,
        block_minutes: els.blockMinutes.value,
        start_time: els.startTime.value,
        end_time: els.endTime.value,
        match_schedule: els.matchSchedule.value,
        event_stats: getEventStats(),
      },
      "Failed to generate schedule."
    );
    if (!data) return;
    const unassigned = data.unassigned?.length || 0;
    if (unassigned) {
      const total = Number(data.team_count);
      setStatus(
        Number.isFinite(total)
          ? `Error: scheduled ${total - unassigned} of ${total} teams. ${unassigned} unassigned.`
          : `Error: ${unassigned} teams unassigned.`
      );
    } else {
      setStatus("Schedule ready.");
    }
  });
};

export const generateNoShowSchedule = async () => {
  await withBusy(els.generateNoShowBtn, async () => {
    setStatus("Generating no-show schedule...");
    const data = await post(
      "/api/generate-noshow",
      {
        match_schedule: els.finalMatchSchedule.value || "",
        event_stats: getEventStats(),
      },
      "Failed to generate no-show schedule."
    );
    if (data) setStatus("No-show schedule ready.");
  });
};

export const printSchedule = async () => {
  if (!hasSchedule(getState())) {
    setStatus("Generate a schedule before printing.");
    return;
  }
  await withBusy(els.printBtn, async () => {
    const data = await post(
      "/api/snapshot-print",
      { label: "Printed schedule" },
      "Failed to snapshot schedule."
    );
    // State (and the print table) is already rendered synchronously by setState.
    if (data) window.print();
  });
};

export const resetAll = async () => {
  if (!window.confirm("Reset all schedules and no-show data?")) {
    return;
  }
  await withBusy(els.resetBtn, async () => {
    const data = await post("/api/reset", {}, "Failed to reset.");
    if (data === null) return;
    setState(null);
    clearMatchScheduleInputs();
    setStatus("Reset complete.");
  });
};

export const setActiveSchedule = async () => {
  const select = els.scheduleSelect;
  if (!select.value) return;
  await withBusy(select, async () => {
    const data = await post(
      "/api/active-schedule",
      { schedule_id: select.value },
      "Failed to switch schedule."
    );
    // On failure, snap the dropdown back to what the server says is active.
    if (!data) renderScheduleSelect(getState());
  });
};

/**
 * Check off / mark no-show / mark not-competing for a team.
 * action: "checkoff" | "noshow" | "not-competing"; button: the clicked control.
 */
export const updateTeamStatus = async (action, team, button) => {
  if (!team) {
    setStatus("Cannot update unassigned slot.");
    return;
  }
  if (!canEditActiveSchedule(getState())) {
    setStatus(
      action === "not-competing"
        ? "Print the schedule before updating teams."
        : "Print the schedule before checking off teams."
    );
    return;
  }
  const key = `${action}:${team}`;
  if (inFlight.has(key)) return;
  inFlight.add(key);

  // Disable all of this slot's buttons (check + no-show) while in flight.
  const group = button?.closest(".slot-actions") || button;
  const controls = group?.querySelectorAll ? [...group.querySelectorAll("button")] : [button];
  try {
    await withBusy(controls, async () => {
      const data = await post(
        `/api/${action}`,
        { team },
        action === "not-competing" ? "Failed to update team." : "Failed to update."
      );
      if (!data) return;
      setStatus(
        action === "not-competing"
          ? `Marked team ${team} as not competing.`
          : `Updated team ${team}.`
      );
    });
  } finally {
    inFlight.delete(key);
  }
};

export const onScheduleClick = (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled || !els.schedule.contains(button)) return;
  updateTeamStatus(button.dataset.action, button.dataset.team, button);
};

export const onNoShowClick = (event) => {
  const button = event.target.closest('button[data-action="not-competing"]');
  if (!button || button.disabled || !els.noShow.contains(button)) return;
  updateTeamStatus("not-competing", button.dataset.team, button);
};

export const onSearchInput = () => setUi({ search: els.teamSearch.value });
