import { els, patchHtml } from "../dom.js";
import { escapeHtml } from "../util.js";
import { getViewedScheduleId } from "../selectors.js";

/** Options come from the server's schedules; the selection is this device's view. */
export const renderScheduleSelect = (state, ui) => {
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
  const viewedId = getViewedScheduleId(state, ui);
  if (viewedId != null && select.value !== String(viewedId)) {
    select.value = String(viewedId);
  }
};
