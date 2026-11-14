// Check-in list. Default order is by team number so a volunteer can find a team
// in one glance; "By judge pair" keeps the per-judge, time-ordered view.
// Times are shown as suggestions, never as appointments.
import { els, patchHtml } from "../dom.js";
import { clockFromIso, compareTeams, escapeHtml, plural } from "../util.js";
import {
  getViewedSchedule,
  canEditViewedSchedule,
  groupByJudge,
  isNoShowSchedule,
  matchRange,
} from "../selectors.js";

const statusInfo = (status, notCompeting) => {
  if (notCompeting) return { cls: "not-competing", label: "Not competing", done: true };
  if (status === "checked") return { cls: "checked", label: "Interviewed", done: true };
  if (status === "no-show") return { cls: "no-show", label: "No-show", done: false };
  return { cls: "waiting", label: "Waiting", done: false };
};

const actionsFor = (team, status, enabled) => {
  if (!team) return "";
  const attr = escapeHtml(team);
  const disabled = enabled ? "" : "disabled";
  const isChecked = status === "checked";
  const isNoShow = status === "no-show";
  return `
    <button class="check" data-action="checkoff" data-team="${attr}" aria-pressed="${isChecked}" ${disabled}>Interviewed</button>
    <button class="no-show" data-action="noshow" data-team="${attr}" aria-pressed="${isNoShow}" ${disabled}>No-show</button>`;
};

const cell = (cls, text, extra = "") =>
  `<div class="cell ${cls}" role="cell">${escapeHtml(text)}${extra}</div>`;

/** Column headings for a list; `lead` matches renderRow. */
const headRow = (lead) => {
  const names = lead === "team" ? ["Team", "Suggested", "Matches", "Judges", "Status", ""] : ["Time", "Matches", "Team", "Status", ""];
  return `<div class="team-row team-head" role="row">${names
    .map((name) => `<div class="cell" role="columnheader">${escapeHtml(name)}</div>`)
    .join("")}</div>`;
};

/** One row. `lead` "team": team-number list; "time": a judge pair's time-ordered list. */
const renderRow = (entry, { enabled, lead = "team", state }) => {
  const { team, slot, notCompeting } = entry;
  const status = slot?.status || "scheduled";
  const { cls, label } = statusInfo(status, notCompeting);
  const time = slot ? clockFromIso(slot.start) : "";
  const matches = slot ? matchRange(state, slot.start, slot.end) : "";
  const gap = slot?.between ? `<small>Free between ${escapeHtml(slot.between)}</small>` : "";
  const cells =
    lead === "team"
      ? cell("team", team) +
        cell("time", time || "None") +
        cell("matches", matches, gap) +
        cell("judge", slot?.judge_id != null ? `Pair ${slot.judge_id}` : "")
      : cell("time", time) + cell("matches", matches, gap) + cell("team", team || "Open");
  return `
    <div class="team-row is-${cls}" role="row">
      ${cells}
      <div class="cell status" role="cell"><span class="badge ${cls}">${label}</span></div>
      <div class="cell slot-actions" role="cell">${notCompeting || !slot ? "" : actionsFor(team, status, enabled)}</div>
    </div>`;
};

const matchesFilter = (entry, filter) => {
  const { done } = statusInfo(entry.slot?.status, entry.notCompeting);
  if (filter === "waiting") return !done;
  if (filter === "done") return done;
  return true;
};

const setPressed = (buttons, key, value) =>
  buttons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset[key] === value)));

const setText = (el, text) => {
  if (el && el.textContent !== text) el.textContent = text;
};

const renderGate = (state, ui, viewed) => {
  const gate = els.checkinGate;
  if (!gate) return;
  let text = "";
  if (viewed?.slots?.length && !canEditViewedSchedule(state, ui)) {
    if (isNoShowSchedule(viewed)) {
      text = ui?.isAdmin
        ? "Print the no-show times (No-shows tab) to check these teams off."
        : "Check-off turns on once the admin prints the no-show times.";
    } else {
      text = ui?.isAdmin
        ? "Print the suggested times (Setup, step 3) to turn on check-in."
        : "Check-in turns on once the admin prints the suggested times.";
    }
  }
  setText(gate, text);
  gate.hidden = !text;
};

/** Pure function of (state, ui): the check-in list for the schedule this device views. */
export const renderSchedule = (state, ui) => {
  const filter = ui?.filter || "all";
  const view = ui?.view || "team";
  setPressed(els.filterButtons, "filter", filter);
  setPressed(els.viewButtons, "view", view);
  if (els.versionBox) els.versionBox.hidden = (state?.schedules || []).length < 2;

  const viewed = getViewedSchedule(state, ui);
  renderGate(state, ui, viewed);

  if (!viewed || !Array.isArray(viewed.slots) || !viewed.slots.length) {
    setText(els.checkinProgress, "");
    patchHtml(
      els.schedule,
      `<p class="empty">No suggested times yet. ${
        ui?.isAdmin ? "Make them in the <strong>Setup</strong> tab." : "The admin hasn't made them yet."
      }</p>`
    );
    return;
  }

  const notCompeting = new Set((state?.not_competing || []).map(String));
  const entries = viewed.slots
    .filter((slot) => slot.team)
    .map((slot) => ({ team: String(slot.team), slot, notCompeting: notCompeting.has(String(slot.team)) }));
  // Teams that did not fit get listed too (no time), so nobody is invisible.
  if (!isNoShowSchedule(viewed)) {
    const listed = new Set(entries.map((entry) => entry.team));
    (state?.unassigned || []).forEach((team) => {
      if (!listed.has(String(team))) entries.push({ team: String(team), slot: null, notCompeting: false });
    });
  }

  const competing = entries.filter((entry) => !entry.notCompeting);
  const done = competing.filter((entry) => entry.slot?.status === "checked").length;
  setText(els.checkinProgress, `${done} of ${plural(competing.length, "team", "teams")} interviewed`);

  const search = (ui?.search || "").trim().toLowerCase();
  const visible = entries.filter(
    (entry) => matchesFilter(entry, filter) && (!search || entry.team.toLowerCase().includes(search))
  );
  const enabled = canEditViewedSchedule(state, ui);

  if (!visible.length) {
    const why = search ? "No team matches that number." : filter === "waiting" ? "Nobody is waiting. Every team has been interviewed." : "No teams to show.";
    patchHtml(els.schedule, `<p class="empty">${escapeHtml(why)}</p>`);
    return;
  }

  if (view === "judge") {
    const visibleSlots = new Set(visible.map((entry) => entry.slot).filter(Boolean));
    const byTeam = new Map(visible.map((entry) => [entry.slot, entry]));
    const cards = [];
    groupByJudge(viewed.slots).forEach((slots, judgeId) => {
      const rows = slots
        .filter((slot) => visibleSlots.has(slot))
        .map((slot) => renderRow(byTeam.get(slot), { enabled, lead: "time", state }));
      if (!rows.length) return;
      cards.push(`
        <div class="judge-card">
          <h3>Judge pair ${escapeHtml(judgeId)}</h3>
          <div class="team-list by-time" role="table">${headRow("time")}${rows.join("")}</div>
        </div>`);
    });
    const noTime = visible.filter((entry) => !entry.slot);
    if (noTime.length) {
      cards.push(`
        <div class="judge-card">
          <h3>No suggested time</h3>
          <div class="team-list" role="table">${headRow("team")}${noTime.map((entry) => renderRow(entry, { enabled, state })).join("")}</div>
        </div>`);
    }
    patchHtml(els.schedule, `<div class="judge-grid">${cards.join("")}</div>`);
    return;
  }

  visible.sort((a, b) => compareTeams(a.team, b.team));
  patchHtml(
    els.schedule,
    `<div class="team-list" role="table">${headRow("team")}${visible.map((entry) => renderRow(entry, { enabled, state })).join("")}</div>`
  );
};
