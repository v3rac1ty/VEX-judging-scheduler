// Client-side match parsing, used only to autofill the judging start/end times.
// The server remains the source of truth for the real schedule.

export const stripLogPreamble = (text) => {
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

export const parseMatchesFromText = (text) => {
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

export const extractTeamCount = (matches) => {
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

export const extractTeamCountFromText = (text) => {
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

export const extractMatchStartTime = (matches) => {
  if (!Array.isArray(matches) || !matches.length) {
    return null;
  }

  let qualsMin = Infinity;
  let allMin = Infinity;
  matches.forEach((match) => {
    const info = match?.matchInfo || {};
    const epoch = Number(info.timeScheduled);
    if (!Number.isFinite(epoch)) {
      return;
    }
    const round = String(info?.matchTuple?.round || "").toUpperCase();
    allMin = Math.min(allMin, epoch);
    if (round === "QUAL") {
      qualsMin = Math.min(qualsMin, epoch);
    }
  });

  // Prefer the first qualification match; fall back to any scheduled match.
  const earliest = Number.isFinite(qualsMin) ? qualsMin : allMin;
  if (!Number.isFinite(earliest)) {
    return null;
  }

  return new Date(earliest * 1000);
};

export const parseQualsStartTime = (text) => {
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
