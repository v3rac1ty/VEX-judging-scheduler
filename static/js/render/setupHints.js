// Plain-language feedback under the setup fields: what lunch does, and whether
// every team fits in the judging window. Pure function of the shared draft.
import { els } from "../dom.js";
import { isOn } from "../draft.js";
import { clockFromIso, formatClock, parseClock, plural, slotsInWindow } from "../util.js";

const setText = (el, text, tone = "") => {
  if (!el) return;
  if (el.textContent !== text) el.textContent = text;
  el.dataset.tone = tone;
};

/** The lunch break interviews skip, as [start, end) minutes; null when none applies. */
const lunchWindow = (draft) => {
  if (isOn(draft?.judge_during_lunch)) return null;
  const start = parseClock(draft?.lunch_start);
  const end = parseClock(draft?.lunch_end);
  return start != null && end != null && end > start ? [start, end] : null;
};

const renderLunchHint = (draft) => {
  const through = isOn(draft?.judge_during_lunch);
  els.lunchStart?.closest(".lunch-times")?.classList.toggle("is-off", through);
  if (through) {
    setText(els.lunchHint, "Interviews keep going through lunch.");
    return;
  }
  const lunch = lunchWindow(draft);
  if (lunch) {
    setText(els.lunchHint, `No interviews are suggested from ${formatClock(lunch[0])} to ${formatClock(lunch[1])}.`);
  } else if (!String(draft?.lunch_start || "").trim() && !String(draft?.lunch_end || "").trim()) {
    setText(els.lunchHint, "No lunch break was found in the match schedule. Enter one if your event has it.");
  } else {
    setText(els.lunchHint, "Enter lunch times like 12:00 PM.", "warn");
  }
};

const renderCapacity = (draft) => {
  const teams = Number(draft?.preview?.team_count || 0);
  const pairs = Number(draft?.judge_pairs);
  const slot = Number(draft?.slot_minutes);
  const open = parseClock(draft?.start_time);
  const close = parseClock(draft?.end_time);

  if (!teams) {
    setText(els.capacityHint, "");
    return;
  }
  if (open == null || close == null) {
    setText(els.capacityHint, "Enter when interviews open and close, like 11:00 AM.", "warn");
    return;
  }
  if (!(Number.isInteger(pairs) && pairs > 0) || !(Number.isInteger(slot) && slot > 0)) {
    setText(els.capacityHint, "Judge pairs and minutes per interview must be whole numbers.", "warn");
    return;
  }
  // No interviews once eliminations start: the server stops the window there too.
  const elims = parseClock(clockFromIso(draft?.preview?.elims_start));
  const cappedByElims = elims != null && close > elims;
  const effectiveClose = cappedByElims ? elims : close;
  const perPair = slotsInWindow(open, effectiveClose, slot, lunchWindow(draft));
  const spots = perPair * pairs;
  const capNote = cappedByElims
    ? ` Interviews will stop at ${formatClock(elims)}, when eliminations start.`
    : "";
  if (spots >= teams) {
    setText(
      els.capacityHint,
      `${plural(pairs, "judge pair", "judge pairs")} × ${plural(perPair, "interview", "interviews")} = ${spots} spots for ${plural(teams, "team", "teams")}. Everyone fits.${capNote}`,
      cappedByElims ? "warn" : "ok"
    );
  } else {
    const fix =
      elims != null && effectiveClose >= elims
        ? "Add judge pairs or shorten interviews; interviews can't run into eliminations."
        : "Add judge pairs, shorten interviews, or close later.";
    setText(
      els.capacityHint,
      `Not enough time: ${spots} spots for ${plural(teams, "team", "teams")}. ${fix}${capNote}`,
      "warn"
    );
  }
};

export const renderSetupHints = (draft) => {
  if (!draft) return;
  renderLunchHint(draft);
  renderCapacity(draft);
};
