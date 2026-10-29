// User-initiated operations. Each one posts to the API, then feeds any returned
// state through setState() so every renderer stays in sync.
import { api } from "./api.js";
import { els, setStatus } from "./dom.js";
import { withBusy } from "./busy.js";
import { getState, getUi, getDraft, setState, setUi } from "./store.js";
import {
  canEditViewedSchedule,
  hasSchedule,
  getViewedScheduleIdForRequest,
} from "./selectors.js";
import { flushDraft, collectSetup, collectEventStats } from "./draft.js";

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

/** First paint: GET /api/state carries the draft too, so the page renders before SSE connects. */
export const loadInitialState = async () => {
  const result = await api.get("/api/state");
  if (!result.ok) {
    setStatus(result.error);
    return;
  }
  setState(result.data);
};

export const generateSchedule = async () => {
  await withBusy(els.generateBtn, async () => {
    setStatus("Generating schedule...");
    // Make sure typed-but-unsent edits are in the draft before reading it.
    await flushDraft();
    const data = await post(
      "/api/generate",
      collectSetup(getDraft()),
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
    await flushDraft();
    const draft = getDraft();
    const data = await post(
      "/api/generate-noshow",
      {
        match_schedule: draft?.final_schedule?.text || "",
        event_stats: collectEventStats(draft),
      },
      "Failed to generate no-show schedule."
    );
    if (data) setStatus("No-show schedule ready.");
  });
};

export const printSchedule = async () => {
  if (!hasSchedule(getState(), getUi())) {
    setStatus("Generate a schedule before printing.");
    return;
  }
  await withBusy(els.printBtn, async () => {
    const body = { label: "Printed schedule" };
    const scheduleId = getViewedScheduleIdForRequest(getState(), getUi());
    if (scheduleId) body.schedule_id = scheduleId;
    const data = await post("/api/snapshot-print", body, "Failed to snapshot schedule.");
    if (!data) return;
    // Show the freshly printed (locked) schedule here; other devices just
    // receive the locked state over SSE and never open a print dialog.
    setUi({ viewScheduleId: null });
    window.print();
  });
};

export const resetAll = async () => {
  if (!window.confirm("Reset all schedules and no-show data?")) {
    return;
  }
  await withBusy(els.resetBtn, async () => {
    // The empty state and default draft come back through setState and the
    // draft store (and over SSE to every other device); nothing is cleared by hand.
    const data = await post("/api/reset", {}, "Failed to reset.");
    if (data === null) return;
    setStatus("Reset complete.");
  });
};

/** The "View schedule" select is a per-device choice: no server call. */
export const onScheduleSelectChange = () => {
  const id = els.scheduleSelect.value;
  if (id) setUi({ viewScheduleId: id });
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
  if (!canEditViewedSchedule(getState(), getUi())) {
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
      const body = { team };
      const scheduleId = getViewedScheduleIdForRequest(getState(), getUi());
      if (scheduleId) body.schedule_id = scheduleId;
      const data = await post(
        `/api/${action}`,
        body,
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
