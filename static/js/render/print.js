import { els, patchHtml } from "../dom.js";
import { escapeHtml, timeRange } from "../util.js";
import { getActiveSchedule, groupByJudge, isNoShowSchedule } from "../selectors.js";

/** Print-only table; always shows the full schedule (ignores the search filter). */
export const renderPrint = (state) => {
  const active = getActiveSchedule(state);
  const slotMinutes = Number(state?.config?.slot_minutes || 0);
  const byJudge = groupByJudge(active?.slots);

  if (!active || !slotMinutes || !byJudge.size) {
    patchHtml(els.printTable, "");
    return;
  }

  const judgeIds = [...byJudge.keys()];

  if (isNoShowSchedule(active)) {
    patchHtml(
      els.printTable,
      judgeIds
        .map((id) => {
          const rows = byJudge
            .get(id)
            .map(
              (slot) => `
                <tr>
                  <td>${escapeHtml(timeRange(slot.start, slot.end))}</td>
                  <td>${escapeHtml(slot.team || "")}</td>
                  <td>${escapeHtml(slot.between || "")}</td>
                </tr>`
            )
            .join("");
          return `
            <div class="print-table-section">
              <h3>Judge Pair ${escapeHtml(id)}</h3>
              <table>
                <thead><tr><th>Time</th><th>Team</th><th>Between</th></tr></thead>
                <tbody>${rows}</tbody>
              </table>
            </div>`;
        })
        .join("")
    );
    return;
  }

  const rowCount = Math.max(...judgeIds.map((id) => byJudge.get(id).length), 0);
  const rows = Array.from({ length: rowCount }, (_, idx) => {
    const first = judgeIds.map((id) => byJudge.get(id)[idx]).find(Boolean);
    const time = first ? timeRange(first.start, first.end) : "";
    const cells = judgeIds
      .map((id) => {
        const slot = byJudge.get(id)[idx];
        return slot ? `<td>${escapeHtml(slot.team || "")}</td>` : "<td></td>";
      })
      .join("");
    return `<tr><td>${escapeHtml(time)}</td>${cells}</tr>`;
  }).join("");

  patchHtml(
    els.printTable,
    `<table>
      <thead>
        <tr><th>Time</th>${judgeIds.map((id) => `<th>Judge Pair ${escapeHtml(id)}</th>`).join("")}</tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`
  );
};
