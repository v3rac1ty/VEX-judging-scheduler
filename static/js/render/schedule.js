import { els, patchHtml } from "../dom.js";
import { escapeHtml, timeRange } from "../util.js";
import {
  getActiveSchedule,
  canEditActiveSchedule,
  groupByJudge,
} from "../selectors.js";

const statusInfo = (status) => {
  if (status === "checked") return { cls: "checked", label: "Checked" };
  if (status === "no-show") return { cls: "no-show", label: "No-show" };
  return { cls: "", label: "Scheduled" };
};

const renderSlot = (slot, actionsEnabled) => {
  const team = slot.team == null ? "" : String(slot.team);
  const { cls, label } = statusInfo(slot.status);
  const disabled = actionsEnabled ? "" : "disabled";
  const teamAttr = escapeHtml(team);
  // Unassigned slots have nothing to check off, so they get no buttons.
  const actions = team
    ? `<button class="check" data-action="checkoff" data-team="${teamAttr}" ${disabled}>Check off</button>
       <button class="no-show" data-action="noshow" data-team="${teamAttr}" ${disabled}>No show</button>`
    : "";
  return `
    <div class="slot">
      <div>
        <div><strong>${team ? escapeHtml(team) : "Unassigned"}</strong></div>
        ${slot.between ? `<div class="muted">${escapeHtml(slot.between)}</div>` : ""}
        <div>${escapeHtml(timeRange(slot.start, slot.end))}</div>
        <span class="badge ${cls}">${label}</span>
      </div>
      <div class="slot-actions">${actions}</div>
    </div>`;
};

/** Pure function of (state, ui): the judge-card list for the active schedule. */
export const renderSchedule = (state, ui) => {
  const active = getActiveSchedule(state);
  if (!active || !Array.isArray(active.slots) || !active.slots.length) {
    patchHtml(els.schedule, "<p>No schedule loaded.</p>");
    return;
  }

  const searchTerm = (ui?.search || "").trim().toLowerCase();
  const actionsEnabled = canEditActiveSchedule(state);

  const cards = [];
  groupByJudge(active.slots).forEach((slots, judgeId) => {
    const visible = searchTerm
      ? slots.filter((slot) => slot.team && String(slot.team).toLowerCase().includes(searchTerm))
      : slots;
    if (!visible.length) return;
    cards.push(`
      <div class="judge-card">
        <h3>Judge Pair ${escapeHtml(judgeId)}</h3>
        ${visible.map((slot) => renderSlot(slot, actionsEnabled)).join("")}
      </div>`);
  });

  patchHtml(els.schedule, cards.length ? cards.join("") : "<p>No matching teams found.</p>");
};
