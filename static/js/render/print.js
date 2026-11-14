// What goes on paper for the viewed schedule (always the full schedule, never
// the search filter).
//
// Main schedule:
//   page 1+  "Suggested interview times": every team in team-number order, to post
//            for teams. Split into side-by-side columns so it fills the page.
//   next     "Judge sheet": one row per time, one column per judge pair, with a
//            tick box per team and the lunch break marked.
// No-show schedule: one small table per judge pair.
import { els, patchHtml } from "../dom.js";
import { clockFromIso, compareTeams, escapeHtml } from "../util.js";
import { getViewedSchedule, groupByJudge, isNoShowSchedule, matchRange } from "../selectors.js";

const ROWS_PER_COLUMN = 26; // fits a letter/A4 page under the header at print size

/** "2026-11-13T09:00:00-06:00" -> "Friday, November 13" (read from the string: event date). */
const eventDate = (iso) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ""));
  if (!match) return "";
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });
};

const header = (title, state, { compact = false } = {}) => {
  if (compact) {
    return `<header class="ps-head"><h1>${escapeHtml(title)} (continued)</h1></header>`;
  }
  const config = state?.config || {};
  const facts = [];
  const date = eventDate(config.start_time);
  if (date) facts.push(`${escapeHtml(date)}.`);
  const open = clockFromIso(config.start_time);
  const close = clockFromIso(config.end_time);
  if (open && close) facts.push(`Interviews ${escapeHtml(open)} to ${escapeHtml(close)}.`);
  // Mention lunch only when it falls inside the interview window.
  const lunchInWindow =
    Date.parse(config.lunch_start) < Date.parse(config.end_time) &&
    Date.parse(config.lunch_end) > Date.parse(config.start_time);
  if (!config.judge_during_lunch && config.lunch_start && config.lunch_end && lunchInWindow) {
    facts.push(
      `No interviews during lunch, ${escapeHtml(clockFromIso(config.lunch_start))} to ${escapeHtml(
        clockFromIso(config.lunch_end)
      )}.`
    );
  }
  return `
    <header class="ps-head">
      <h1>${escapeHtml(title)}</h1>
      ${facts.length ? `<p>${facts.join(" ")}</p>` : ""}
    </header>`;
};

const SUGGESTION_NOTE = `
  <p class="ps-note">
    These are suggested times, not appointments. Interviews are first come, first served:
    arriving at your suggested time does not move you ahead of a team that is already waiting.
  </p>`;

const chunk = (list, size) =>
  Array.from({ length: Math.ceil(list.length / size) }, (_, i) => list.slice(i * size, (i + 1) * size));

/** Team list pages: columns of ROWS_PER_COLUMN, filled left to right, page by page. */
const renderTeamPages = (state, slots) => {
  const teams = slots.filter((slot) => slot.team).sort((a, b) => compareTeams(a.team, b.team));
  const columnsPerPage = teams.length > ROWS_PER_COLUMN * 2 ? 3 : 2;
  // Balance the columns instead of one full and one nearly empty.
  const pageCount = Math.max(1, Math.ceil(teams.length / (ROWS_PER_COLUMN * columnsPerPage)));
  const perPage = Math.ceil(teams.length / pageCount);
  const rowsPerColumn = Math.max(1, Math.ceil(perPage / columnsPerPage));

  return chunk(teams, perPage)
    .map((pageTeams, pageIndex) => {
      const columns = chunk(pageTeams, rowsPerColumn)
        .map(
          (column) => `
            <table class="ps-table ps-teams">
              <thead><tr><th>Team</th><th>Time</th><th>Matches</th><th>Judges</th></tr></thead>
              <tbody>
                ${column
                  .map(
                    (slot) => `
                  <tr>
                    <td class="ps-team">${escapeHtml(slot.team)}</td>
                    <td class="ps-nowrap">${escapeHtml(clockFromIso(slot.start))}</td>
                    <td class="ps-matches">${escapeHtml(matchRange(state, slot.start, slot.end))}</td>
                    <td class="ps-pair">Pair ${escapeHtml(slot.judge_id)}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>`
        )
        .join("");
      return `
        <section class="ps-page">
          ${pageIndex === 0 ? header("Suggested Judging Interview Times", state) + SUGGESTION_NOTE : header("Suggested Judging Interview Times", state, { compact: true })}
          <div class="ps-columns ps-cols-${columnsPerPage}">${columns}</div>
        </section>`;
    })
    .join("");
};

/** Judge sheet: rows by start time; a lunch row where the break falls. */
const renderJudgeSheet = (state, byJudge) => {
  const config = state?.config || {};
  const judgeIds = [...byJudge.keys()];
  const slotsByStart = new Map();
  [...byJudge.values()].flat().forEach((slot) => {
    if (!slotsByStart.has(slot.start)) slotsByStart.set(slot.start, slot);
  });
  const starts = [...slotsByStart.keys()].sort((a, b) => Date.parse(a) - Date.parse(b));
  const cell = new Map(); // `${judge}|${start}` -> slot
  byJudge.forEach((slots, id) => slots.forEach((slot) => cell.set(`${id}|${slot.start}`, slot)));

  const lunchStart = !config.judge_during_lunch ? Date.parse(config.lunch_start || "") : NaN;
  const lunchEnd = Date.parse(config.lunch_end || "");
  let lunchShown = false;
  let seenBeforeLunch = false; // only mark lunch when it falls between interviews
  const lunchRow = () =>
    `<tr class="ps-lunch"><td>${escapeHtml(clockFromIso(config.lunch_start))}</td><td></td><td colspan="${judgeIds.length}">Lunch. No interviews until ${escapeHtml(clockFromIso(config.lunch_end))}.</td></tr>`;

  const rows = starts
    .map((start) => {
      let before = "";
      const at = Date.parse(start);
      if (Number.isFinite(lunchStart) && Number.isFinite(lunchEnd)) {
        if (at < lunchStart) seenBeforeLunch = true;
        if (!lunchShown && seenBeforeLunch && at >= lunchEnd) {
          before = lunchRow();
          lunchShown = true;
        }
      }
      const cells = judgeIds
        .map((id) => {
          const slot = cell.get(`${id}|${start}`);
          return slot?.team
            ? `<td><span class="ps-box"></span>${escapeHtml(slot.team)}</td>`
            : `<td></td>`;
        })
        .join("");
      const matches = matchRange(state, start, slotsByStart.get(start).end);
      return `${before}<tr><td class="ps-time">${escapeHtml(clockFromIso(start))}</td><td class="ps-matches">${escapeHtml(matches)}</td>${cells}</tr>`;
    })
    .join("");

  return `
    <section class="ps-page ps-break">
      ${header("Judge Sheet", state)}
      <p class="ps-note">Tick the box when a team has been interviewed. Teams may come early or late; take whoever is waiting.</p>
      <table class="ps-table ps-grid">
        <thead>
          <tr><th class="ps-time">Time</th><th>Matches</th>${judgeIds.map((id) => `<th>Pair ${escapeHtml(id)}</th>`).join("")}</tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
};

const renderNoShow = (state, byJudge) => {
  const sections = [...byJudge.keys()]
    .map((id) => {
      const rows = byJudge
        .get(id)
        .map(
          (slot) => `
            <tr>
              <td class="ps-time">${escapeHtml(clockFromIso(slot.start))}</td>
              <td class="ps-matches">${escapeHtml(matchRange(state, slot.start, slot.end))}</td>
              <td class="ps-team"><span class="ps-box"></span>${escapeHtml(slot.team || "")}</td>
              <td>${escapeHtml(slot.between || "")}</td>
            </tr>`
        )
        .join("");
      return `
        <div class="ps-section">
          <h2>Judge pair ${escapeHtml(id)}</h2>
          <table class="ps-table ps-grid">
            <thead><tr><th class="ps-time">Time</th><th>Matches</th><th>Team</th><th>Team's free gap</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    })
    .join("");
  return `
    <section class="ps-page">
      ${header("No-show Recovery: Suggested Times", state)}
      ${SUGGESTION_NOTE}
      ${sections}
    </section>`;
};

export const renderPrint = (state, ui) => {
  const active = getViewedSchedule(state, ui);
  const byJudge = groupByJudge(active?.slots);
  if (!active || !byJudge.size) {
    patchHtml(els.printTable, "");
    return;
  }
  patchHtml(
    els.printTable,
    isNoShowSchedule(active)
      ? renderNoShow(state, byJudge)
      : renderTeamPages(state, [...active.slots]) + renderJudgeSheet(state, byJudge)
  );
};
