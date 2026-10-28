import { els, patchHtml } from "../dom.js";
import { escapeHtml } from "../util.js";

export const renderScheduleSelect = (state) => {
  const select = els.scheduleSelect;
  if (!select) return;

  const schedules = state?.schedules || [];
  if (!schedules.length) {
    patchHtml(select, "");
    select.disabled = true;
    return;
  }

  select.disabled = false;
  const html = schedules
    .map(
      (schedule) =>
        `<option value="${escapeHtml(schedule.id)}">${escapeHtml(schedule.label)}</option>`
    )
    .join("");
  patchHtml(select, html);
  // Always re-assert the selection: patchHtml may have skipped the rewrite.
  if (state.active_schedule_id != null) {
    select.value = String(state.active_schedule_id);
  }
};
