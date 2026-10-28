const statusEl = document.getElementById("status");
const scheduleEl = document.getElementById("schedule");
const noShowEl = document.getElementById("noShowPanel");
const printTableEl = document.getElementById("printTable");
const scheduleSelect = document.getElementById("scheduleSelect");
const teamSearchInput = document.getElementById("teamSearch");

const judgePairsInput = document.getElementById("judgePairs");
const slotMinutesInput = document.getElementById("slotMinutes");
const blockMinutesInput = document.getElementById("blockMinutes");
const startTimeInput = document.getElementById("startTime");
const endTimeInput = document.getElementById("endTime");

const roundsMinInput = document.getElementById("roundsMin");
const roundsMaxInput = document.getElementById("roundsMax");
const spacingMinInput = document.getElementById("spacingMin");
const spacingMaxInput = document.getElementById("spacingMax");
const spacingAvgInput = document.getElementById("spacingAvg");
const repeatMinInput = document.getElementById("repeatMin");
const repeatMaxInput = document.getElementById("repeatMax");
const repeatAvgInput = document.getElementById("repeatAvg");
const distinctMinInput = document.getElementById("distinctMin");
const distinctMaxInput = document.getElementById("distinctMax");
const distinctAvgInput = document.getElementById("distinctAvg");
const matchScheduleInput = document.getElementById("matchSchedule");
const dropzoneEl = document.getElementById("dropzone");
const fileInputEl = document.getElementById("fileInput");
const finalDropzoneEl = document.getElementById("finalDropzone");
const finalFileInputEl = document.getElementById("finalFileInput");
const finalMatchScheduleInput = document.getElementById("finalMatchSchedule");

const generateBtn = document.getElementById("generateBtn");
const printBtn = document.getElementById("printBtn");
const generateNoShowBtn = document.getElementById("generateNoShowBtn");
const resetBtn = document.getElementById("resetBtn");

let currentState = null;

const updateLockState = (state) => {
  const locked = Boolean(state?.locked);
  const noshowLocked = Boolean(state?.noshow_locked);
  if (generateBtn) {
    generateBtn.disabled = locked;
  }
  if (generateNoShowBtn) {
    generateNoShowBtn.disabled = noshowLocked;
  }
  if (locked) {
    statusEl.textContent = "Schedule locked after printing. Reset to generate again.";
  } else if (noshowLocked) {
    statusEl.textContent = "No-show schedule locked after printing. Reset to generate again.";
  }
};

const getEventStats = () => ({
  rounds_min: Number(roundsMinInput?.value || 8),
  rounds_max: Number(roundsMaxInput?.value || 8),
  spacing_min: Number(spacingMinInput?.value || 3),
  spacing_max: Number(spacingMaxInput?.value || 14),
  spacing_avg: Number(spacingAvgInput?.value || 7),
  repeat_min: Number(repeatMinInput?.value || 1),
  repeat_max: Number(repeatMaxInput?.value || 2),
  repeat_avg: Number(repeatAvgInput?.value || 1),
  distinct_min: Number(distinctMinInput?.value || 21),
  distinct_max: Number(distinctMaxInput?.value || 24),
  distinct_avg: Number(distinctAvgInput?.value || 23),
});

const setEventStats = (stats) => {
  if (!stats) return;
  const set = (el, key) => { if (el && stats[key] != null) el.value = stats[key]; };
  set(roundsMinInput, "rounds_min"); set(roundsMaxInput, "rounds_max");
  set(spacingMinInput, "spacing_min"); set(spacingMaxInput, "spacing_max"); set(spacingAvgInput, "spacing_avg");
  set(repeatMinInput, "repeat_min"); set(repeatMaxInput, "repeat_max"); set(repeatAvgInput, "repeat_avg");
  set(distinctMinInput, "distinct_min"); set(distinctMaxInput, "distinct_max"); set(distinctAvgInput, "distinct_avg");
};

const applyState = (state) => {
  currentState = state;
  updateLockState(state);
  renderSchedule(state);
  renderScheduleSelect(state);
  renderNoShow(state);
  if (state?.config?.event_stats) {
    setEventStats(state.config.event_stats);
  }
};

const formatTime = (date) =>
  date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });

const nowLocalInput = () => formatTime(new Date());

const futureLocalInput = (minutesAhead) => {
  const date = new Date();
  date.setMinutes(date.getMinutes() + minutesAhead);
  return formatTime(date);
};

const toLocalTime = (isoString) => {
  const date = new Date(isoString);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const stripLogPreamble = (text) => {
  // Find the start of the JSON payload after a log marker like:
  // ">> Matches: {\n  [" or ">> Fetched matches:\n["
  const markerMatch = text.match(/>>[^\n]*(?:matches|Matches)\s*:\s*/);
  if (markerMatch) {
    const afterMarker = text.slice(markerMatch.index + markerMatch[0].length);
    // Jump to the first [ or {
    const jsonStart = afterMarker.search(/[[{]/);
    if (jsonStart !== -1) {
      return afterMarker.slice(jsonStart);
    }
  }
  return text;
};

const parseMatchesFromText = (text) => {
  if (!text || !text.trim()) {
    return null;
  }

  const workingText = stripLogPreamble(text);

  try {
    const parsed = JSON.parse(workingText);
    if (Array.isArray(parsed)) {
      return parsed;
    }
    if (parsed && Array.isArray(parsed.Matches)) {
      return parsed.Matches;
    }
  } catch {
    // Fall back to extracting the first JSON array.
  }

  const match = workingText.match(/\[(.*)\]/s);
  if (!match) {
    return null;
  }

  try {
    const extracted = JSON.parse(`[${match[1]}]`);
    return Array.isArray(extracted) ? extracted : null;
  } catch {
    return null;
  }
};

const extractTeamCount = (matches) => {
  if (!Array.isArray(matches)) {
    return 0;
  }

  const teams = new Set();
  matches.forEach((match) => {
    const alliances = match?.matchInfo?.alliances || [];
    alliances.forEach((alliance) => {
      const allianceTeams = alliance?.teams || [];
      allianceTeams.forEach((team) => {
        const teamNumber = String(team?.number || "").trim();
        if (teamNumber) {
          teams.add(teamNumber);
        }
      });
    });
  });

  return teams.size;
};

const extractTeamCountFromText = (text) => {
  if (!text) {
    return 0;
  }
  const teams = new Set();
  const numberRegex = /"number"\s*:\s*"?([A-Za-z0-9-]+)"?/g;
  let m;
  while ((m = numberRegex.exec(text)) !== null) {
    const team = String(m[1] || "").trim();
    if (team) {
      teams.add(team);
    }
  }
  return teams.size;
};

const extractMatchStartTime = (matches) => {
  if (!Array.isArray(matches) || !matches.length) {
    return null;
  }

  const qualsTimes = [];
  const allTimes = [];
  matches.forEach((match) => {
    const info = match?.matchInfo || {};
    const epoch = Number(info.timeScheduled);
    if (!Number.isFinite(epoch)) {
      return;
    }
    const round = String(info?.matchTuple?.round || "").toUpperCase();
    allTimes.push(epoch);
    if (round === "QUAL") {
      qualsTimes.push(epoch);
    }
  });

  const source = qualsTimes.length ? qualsTimes : allTimes;
  if (!source.length) {
    return null;
  }

  return new Date(Math.min(...source) * 1000);
};

const parseQualsStartTime = (text) => {
  const match = text.match(/Quals\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))/i);
  if (!match) {
    return null;
  }
  const timeText = match[1].toLowerCase().replace(/\s+/g, "");
  const timeMatch = timeText.match(/^(\d{1,2})(?::(\d{2}))?(am|pm)$/);
  if (!timeMatch) {
    return null;
  }
  let hour = Number(timeMatch[1]);
  const minute = Number(timeMatch[2] || "0");
  const meridiem = timeMatch[3];
  if (hour < 1 || hour > 12 || minute > 59) {
    return null;
  }
  if (meridiem === "pm" && hour !== 12) {
    hour += 12;
  }
  if (meridiem === "am" && hour === 12) {
    hour = 0;
  }
  const date = new Date();
  date.setHours(hour, minute, 0, 0);
  return date;
};

const updateJudgingTimesFromFile = (text) => {
  const matches = parseMatchesFromText(text);
  const matchStart = extractMatchStartTime(matches) || parseQualsStartTime(text);
  if (!matchStart) {
    return;
  }

  if (endTimeInput) {
    endTimeInput.value = formatTime(matchStart);
  }

  const teamCount = extractTeamCount(matches);
  const fallbackTeamCount = teamCount || extractTeamCountFromText(text);
  const effectiveTeamCount = fallbackTeamCount;
  const judgePairs = Math.max(1, Number(judgePairsInput?.value || 1));
  const slotMinutes = Math.max(1, Number(slotMinutesInput?.value || 10));

  const defaultDuration = 100;
  const computedDuration = effectiveTeamCount > 0
    ? Math.ceil(effectiveTeamCount / judgePairs) * slotMinutes
    : defaultDuration;

  if (startTimeInput) {
    const start = new Date(matchStart.getTime() - computedDuration * 60000);
    startTimeInput.value = formatTime(start);
  }
};

const loadScheduleFile = async (file) => {
  if (!file) {
    return;
  }
  const text = await file.text();
  matchScheduleInput.value = text;
  updateJudgingTimesFromFile(text);
  statusEl.textContent = `Loaded ${file.name}.`;
};

const loadFinalScheduleFile = async (file) => {
  if (!file || !finalMatchScheduleInput) {
    return;
  }
  const text = await file.text();
  finalMatchScheduleInput.value = text;
  statusEl.textContent = `Loaded final schedule ${file.name}.`;
};

const clearMatchScheduleInputs = () => {
  if (matchScheduleInput) {
    matchScheduleInput.value = "";
  }
  if (finalMatchScheduleInput) {
    finalMatchScheduleInput.value = "";
  }
  if (fileInputEl) {
    fileInputEl.value = "";
  }
  if (finalFileInputEl) {
    finalFileInputEl.value = "";
  }
};

const getActiveSchedule = (state) => {
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

const canEditActiveSchedule = (state) => {
  const active = getActiveSchedule(state);
  const type = active?.type;
  if (type === "noshow" || type === "printed-noshow") {
    return Boolean(state?.noshow_locked);
  }
  return Boolean(state?.locked);
};

const getSearchTerm = () => {
  if (!teamSearchInput) {
    return "";
  }
  return teamSearchInput.value.trim().toLowerCase();
};

const renderScheduleSelect = (state) => {
  if (!scheduleSelect) {
    return;
  }
  const schedules = state.schedules || [];
  if (!schedules.length) {
    scheduleSelect.innerHTML = "";
    scheduleSelect.disabled = true;
    return;
  }
  scheduleSelect.disabled = false;
  scheduleSelect.innerHTML = schedules
    .map((schedule) => {
      const selected = schedule.id === state.active_schedule_id ? "selected" : "";
      return `<option value="${schedule.id}" ${selected}>${schedule.label}</option>`;
    })
    .join("");
};

const renderSchedule = (state) => {
  const active = getActiveSchedule(state);
  if (!active || !active.slots) {
    scheduleEl.innerHTML = "<p>No schedule loaded.</p>";
    if (printTableEl) {
      printTableEl.innerHTML = "";
    }
    return;
  }

  const byJudge = new Map();
  active.slots.forEach((slot) => {
    if (!byJudge.has(slot.judge_id)) {
      byJudge.set(slot.judge_id, []);
    }
    byJudge.get(slot.judge_id).push(slot);
  });
  byJudge.forEach((slots, judgeId) => {
    slots.sort((a, b) => new Date(a.start) - new Date(b.start));
    byJudge.set(judgeId, slots);
  });

  const searchTerm = getSearchTerm();
  const filteredByJudge = new Map();
  byJudge.forEach((slots, judgeId) => {
    if (!searchTerm) {
      filteredByJudge.set(judgeId, slots);
      return;
    }
    const filtered = slots.filter((slot) => {
      if (!slot.team) {
        return false;
      }
      return slot.team.toLowerCase().includes(searchTerm);
    });
    if (filtered.length) {
      filteredByJudge.set(judgeId, filtered);
    }
  });

  scheduleEl.innerHTML = "";
  if (searchTerm && !filteredByJudge.size) {
    scheduleEl.innerHTML = "<p>No matching teams found.</p>";
    renderPrintTable(state, byJudge);
    return;
  }

  const displayMap = searchTerm ? filteredByJudge : byJudge;
  const actionsEnabled = canEditActiveSchedule(state);
  [...displayMap.entries()].forEach(([judgeId, slots]) => {
    const card = document.createElement("div");
    card.className = "judge-card";
    card.innerHTML = `<h3>Judge Pair ${judgeId}</h3>`;

    slots.forEach((slot) => {
      const row = document.createElement("div");
      row.className = "slot";
      const statusClass = slot.status === "checked" ? "checked" : slot.status === "no-show" ? "no-show" : "";
      const statusLabel = slot.status === "checked" ? "Checked" : slot.status === "no-show" ? "No-show" : "Scheduled";
      row.innerHTML = `
        <div>
          <div><strong>${slot.team || "Unassigned"}</strong></div>
          ${slot.between ? `<div class="muted">${slot.between}</div>` : ""}
          <div>${toLocalTime(slot.start)} - ${toLocalTime(slot.end)}</div>
          <span class="badge ${statusClass}">${statusLabel}</span>
        </div>
        <div class="slot-actions">
          <button class="check" data-team="${slot.team}" ${actionsEnabled ? "" : "disabled"}>Check off</button>
          <button class="no-show" data-team="${slot.team}" ${actionsEnabled ? "" : "disabled"}>No show</button>
        </div>
      `;
      row.querySelector(".check").addEventListener("click", () => updateStatus("checkoff", slot.team));
      row.querySelector(".no-show").addEventListener("click", () => updateStatus("noshow", slot.team));
      card.appendChild(row);
    });

    scheduleEl.appendChild(card);
  });

  renderPrintTable(state, byJudge);
};

const renderPrintTable = (state, byJudge) => {
  if (!printTableEl) {
    return;
  }
  const slotMinutes = Number(state?.config?.slot_minutes || 0);
  if (!slotMinutes || !byJudge?.size) {
    printTableEl.innerHTML = "";
    return;
  }

  const active = getActiveSchedule(state);
  const isNoShow = active?.type === "noshow" || active?.type === "printed-noshow";

  const judgeIds = [...byJudge.keys()].sort((a, b) => a - b);
  const slotCounts = judgeIds.map((id) => byJudge.get(id)?.length || 0);
  const rowCount = Math.max(...slotCounts, 0);

  const rows = Array.from({ length: rowCount }, (_, idx) => {
    const rowSlots = judgeIds.map((id) => (byJudge.get(id) || [])[idx]).filter(Boolean);
    const time = rowSlots[0] ? `${toLocalTime(rowSlots[0].start)} - ${toLocalTime(rowSlots[0].end)}` : "";
    return { time, index: idx };
  });

  if (isNoShow) {
    printTableEl.innerHTML = judgeIds
      .map((id) => {
        const slots = byJudge.get(id) || [];
        return `
          <div class="print-table-section">
            <h3>Judge Pair ${id}</h3>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Team</th>
                  <th>Between</th>
                </tr>
              </thead>
              <tbody>
                ${slots
                  .map(
                    (slot) => `
                  <tr>
                    <td>${toLocalTime(slot.start)} - ${toLocalTime(slot.end)}</td>
                    <td>${slot.team || ""}</td>
                    <td>${slot.between || ""}</td>
                  </tr>
                `
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        `;
      })
      .join("");
    return;
  }

  const header = `
    <thead>
      <tr>
        <th>Time</th>
        ${judgeIds.map((id) => `<th>Judge Pair ${id}</th>`).join("")}
      </tr>
    </thead>
  `;

  const body = `
    <tbody>
      ${rows
        .map((row) => {
          const cells = judgeIds
            .map((id) => {
              const slot = (byJudge.get(id) || [])[row.index];
              if (!slot) {
                return "<td></td>";
              }
              return `<td>${slot.team || ""}</td>`;
            })
            .join("");
          return `<tr><td>${row.time}</td>${cells}</tr>`;
        })
        .join("")}
    </tbody>
  `;

  printTableEl.innerHTML = `
    <table>
      ${header}
      ${body}
    </table>
  `;
};

const renderNoShow = (state) => {
  const suggestions = state.no_show_suggestions || (state.last_suggestions ? [state.last_suggestions] : []);
  if (!suggestions.length) {
    noShowEl.innerHTML = "<p>No no-show events yet.</p>";
    return;
  }

  noShowEl.innerHTML = suggestions
    .map((entry) => {
      // Team not in the final match schedule — show as crossed off, excluded from recovery
      if (entry.not_in_schedule) {
        return `
          <div class="noshow-item not-in-schedule">
            <span class="crossed-off">Team ${entry.team}</span>
            <span class="badge checked">Not in final schedule</span>
          </div>
        `;
      }

      const judgeLabel = entry.judge_id ? `Judge Pair ${entry.judge_id}` : "Judge Pair TBD";
      const gaps = entry.gaps || [];
      if (!gaps.length) {
        return `
          <details class="noshow-item">
            <summary>
              <span>Team ${entry.team}</span>
              <span class="badge">${judgeLabel}</span>
              <span class="muted">No gaps found</span>
            </summary>
          </details>
        `;
      }

      return `
        <details class="noshow-item">
          <summary>
            <span>Team ${entry.team}</span>
            <span class="badge">${judgeLabel}</span>
            <span class="muted">Top gap: ${gaps[0].minutes} min</span>
          </summary>
          <div class="noshow-gaps">
            <button class="secondary not-competing" data-team="${entry.team}">Not competing</button>
            ${gaps
              .map(
                (gap) => `
              <div class="gap">
                <span>${gap.between} · ${toLocalTime(gap.start)} - ${toLocalTime(gap.end)}</span>
                <span>${gap.minutes} min</span>
              </div>
            `
              )
              .join("")}
          </div>
        </details>
      `;
    })
    .join("");

  noShowEl.querySelectorAll(".not-competing").forEach((button) => {
    button.addEventListener("click", () => {
      const team = button.getAttribute("data-team");
      markNotCompeting(team);
    });
  });
};

const updateStatus = async (endpoint, team) => {
  if (!team) {
    statusEl.textContent = "Cannot update unassigned slot.";
    return;
  }
  if (!canEditActiveSchedule(currentState)) {
    statusEl.textContent = "Print the schedule before checking off teams.";
    return;
  }
  const response = await fetch(`/api/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to update.";
    return;
  }
  renderSchedule(data);
  renderNoShow(data);
  statusEl.textContent = `Updated team ${team}.`;
};

const markNotCompeting = async (team) => {
  if (!team) {
    return;
  }
  if (!canEditActiveSchedule(currentState)) {
    statusEl.textContent = "Print the schedule before updating teams.";
    return;
  }
  const response = await fetch("/api/not-competing", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to update team.";
    return;
  }
  applyState(data);
  statusEl.textContent = `Marked team ${team} as not competing.`;
};

const generateSchedule = async () => {
  statusEl.textContent = "Generating schedule...";
  const payload = {
    judge_pairs: judgePairsInput.value,
    slot_minutes: slotMinutesInput.value,
    block_minutes: blockMinutesInput.value,
    start_time: startTimeInput.value,
    end_time: endTimeInput.value,
    match_schedule: matchScheduleInput.value,
    event_stats: getEventStats(),
  };

  const response = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to generate schedule.";
    return;
  }
  applyState(data);
  if (data.unassigned?.length) {
    const totalTeams = data.team_count ?? "some";
    statusEl.textContent = `Error: scheduled ${totalTeams - data.unassigned.length} of ${totalTeams} teams. ${data.unassigned.length} unassigned.`;
  } else {
    statusEl.textContent = "Schedule ready.";
  }
};

const generateNoShowSchedule = async () => {
  statusEl.textContent = "Generating no-show schedule...";
  const payload = {
    match_schedule: finalMatchScheduleInput?.value || "",
    event_stats: getEventStats(),
  };
  const response = await fetch("/api/generate-noshow", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to generate no-show schedule.";
    return;
  }
  applyState(data);
  statusEl.textContent = "No-show schedule ready.";
};


const loadState = async () => {
  const response = await fetch("/api/state");
  const data = await response.json();
  if (data && data.slots) {
    applyState(data);
  } else {
    scheduleEl.innerHTML = "<p>No schedule loaded.</p>";
    if (printTableEl) {
      printTableEl.innerHTML = "";
    }
    noShowEl.innerHTML = "<p>No no-show events yet.</p>";
    if (scheduleSelect) {
      scheduleSelect.innerHTML = "";
      scheduleSelect.disabled = true;
    }
    updateLockState(null);
    currentState = null;
    clearMatchScheduleInputs();
  }
};

const resetAll = async () => {
  const confirmed = window.confirm("Reset all schedules and no-show data?");
  if (!confirmed) {
    return;
  }
  const response = await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to reset.";
    return;
  }
  currentState = null;
  updateLockState(null);
  clearMatchScheduleInputs();
  statusEl.textContent = "Reset complete.";
  loadState();
};

const setActiveSchedule = async () => {
  if (!scheduleSelect.value) {
    return;
  }
  const response = await fetch("/api/active-schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schedule_id: scheduleSelect.value }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to switch schedule.";
    return;
  }
  applyState(data);
};

const printSchedule = async () => {
  if (!scheduleEl.innerHTML.trim()) {
    statusEl.textContent = "Generate a schedule before printing.";
    return;
  }
  const response = await fetch("/api/snapshot-print", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: "Printed schedule" }),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Failed to snapshot schedule.";
    return;
  }
  applyState(data);
  window.print();
};

generateBtn.addEventListener("click", generateSchedule);
printBtn.addEventListener("click", printSchedule);
generateNoShowBtn.addEventListener("click", generateNoShowSchedule);
scheduleSelect.addEventListener("change", setActiveSchedule);
resetBtn.addEventListener("click", resetAll);
if (teamSearchInput) {
  teamSearchInput.addEventListener("input", () => renderSchedule(currentState));
}
if (dropzoneEl && fileInputEl) {
  dropzoneEl.addEventListener("click", () => fileInputEl.click());
  dropzoneEl.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzoneEl.classList.add("is-dragging");
  });
  dropzoneEl.addEventListener("dragleave", () => {
    dropzoneEl.classList.remove("is-dragging");
  });
  dropzoneEl.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzoneEl.classList.remove("is-dragging");
    const file = event.dataTransfer?.files?.[0];
    loadScheduleFile(file);
  });
  fileInputEl.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    loadScheduleFile(file);
  });
}
if (finalDropzoneEl && finalFileInputEl) {
  finalDropzoneEl.addEventListener("click", () => finalFileInputEl.click());
  finalDropzoneEl.addEventListener("dragover", (event) => {
    event.preventDefault();
    finalDropzoneEl.classList.add("is-dragging");
  });
  finalDropzoneEl.addEventListener("dragleave", () => {
    finalDropzoneEl.classList.remove("is-dragging");
  });
  finalDropzoneEl.addEventListener("drop", (event) => {
    event.preventDefault();
    finalDropzoneEl.classList.remove("is-dragging");
    const file = event.dataTransfer?.files?.[0];
    loadFinalScheduleFile(file);
  });
  finalFileInputEl.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    loadFinalScheduleFile(file);
  });
}
if (!startTimeInput.value) {
  startTimeInput.value = nowLocalInput();
}
if (endTimeInput && !endTimeInput.value) {
  endTimeInput.value = futureLocalInput(100);
}

if (judgePairsInput) {
  judgePairsInput.addEventListener("change", () => {
    if (matchScheduleInput?.value) {
      updateJudgingTimesFromFile(matchScheduleInput.value);
    }
  });
}

if (slotMinutesInput) {
  slotMinutesInput.addEventListener("change", () => {
    if (matchScheduleInput?.value) {
      updateJudgingTimesFromFile(matchScheduleInput.value);
    }
  });
}

loadState();
