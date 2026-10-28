import { els, patchHtml } from "../dom.js";
import { escapeHtml, timeRange } from "../util.js";

// Which <details> the user has expanded, keyed by team. Survives re-renders so
// live updates never collapse something the user opened.
const openTeams = new Set();

/** Track open/closed state; `toggle` doesn't bubble, so listen in capture phase. */
export const initNoShowToggleTracking = () => {
  els.noShow.addEventListener(
    "toggle",
    (event) => {
      const details = event.target;
      const team = details?.dataset?.team;
      if (team == null) return;
      if (details.open) openTeams.add(team);
      else openTeams.delete(team);
    },
    true
  );
};

const renderEntry = (entry) => {
  const team = escapeHtml(entry.team);

  // Team not in the final match schedule: crossed off, excluded from recovery.
  if (entry.not_in_schedule) {
    return `
      <div class="noshow-item not-in-schedule">
        <span class="crossed-off">Team ${team}</span>
        <span class="badge checked">Not in final schedule</span>
      </div>`;
  }

  const judgeLabel = entry.judge_id ? `Judge Pair ${escapeHtml(entry.judge_id)}` : "Judge Pair TBD";
  const gaps = entry.gaps || [];
  const open = openTeams.has(String(entry.team)) ? "open" : "";
  const detailsAttrs = `class="noshow-item" data-team="${team}" ${open}`;

  if (!gaps.length) {
    return `
      <details ${detailsAttrs}>
        <summary>
          <span>Team ${team}</span>
          <span class="badge">${judgeLabel}</span>
          <span class="muted">No gaps found</span>
        </summary>
      </details>`;
  }

  return `
    <details ${detailsAttrs}>
      <summary>
        <span>Team ${team}</span>
        <span class="badge">${judgeLabel}</span>
        <span class="muted">Top gap: ${escapeHtml(gaps[0].minutes)} min</span>
      </summary>
      <div class="noshow-gaps">
        <button class="secondary not-competing" data-action="not-competing" data-team="${team}">Not competing</button>
        ${gaps
          .map(
            (gap) => `
          <div class="gap">
            <span>${escapeHtml(gap.between)} · ${escapeHtml(timeRange(gap.start, gap.end))}</span>
            <span>${escapeHtml(gap.minutes)} min</span>
          </div>`
          )
          .join("")}
      </div>
    </details>`;
};

export const renderNoShow = (state) => {
  const suggestions =
    state?.no_show_suggestions ||
    (state?.last_suggestions ? [state.last_suggestions] : []);

  if (!suggestions.length) {
    patchHtml(els.noShow, "<p>No no-show events yet.</p>");
    return;
  }

  // Forget open-state for teams that are gone so it doesn't leak across resets.
  const present = new Set(suggestions.map((entry) => String(entry.team)));
  openTeams.forEach((team) => {
    if (!present.has(team)) openTeams.delete(team);
  });

  patchHtml(els.noShow, suggestions.map(renderEntry).join(""));
};
