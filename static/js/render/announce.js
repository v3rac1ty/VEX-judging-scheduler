// Emcee announcement: which teams still need an interview, and a reminder to
// announce it one hour before interviews close. Re-rendered on state changes
// and on the clock tick, so the reminder appears without a reload.
import { els, patchHtml, setStatus } from "../dom.js";
import { setUi } from "../store.js";
import { teamRoster, teamsStillWaiting } from "../selectors.js";
import { clockFromIso, escapeHtml, formatClock, plural } from "../util.js";

export const ANNOUNCE_MINUTES_BEFORE_CLOSE = 60;

/** Wall-clock minutes of an ISO time, read from the string (event time, any device). */
const minutesOfIso = (iso) => {
  const match = /T(\d{2}):(\d{2})/.exec(String(iso ?? ""));
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
};

/**
 * Where we are relative to closing time:
 *   none   no generated schedule (no close time)
 *   early  more than an hour before close
 *   now    within the last hour: announce
 *   closed interviews have closed
 */
export const announcePhase = (state, now = Date.now()) => {
  const close = Date.parse(state?.config?.end_time || "");
  if (!Number.isFinite(close)) return { phase: "none" };
  const announceAt = close - ANNOUNCE_MINUTES_BEFORE_CLOSE * 60_000;
  const minutesLeft = Math.max(0, Math.ceil((close - now) / 60_000));
  if (now >= close) return { phase: "closed", minutesLeft };
  if (now >= announceAt) return { phase: "now", minutesLeft };
  return { phase: "early", minutesLeft };
};

export const announcementText = (state, waiting = teamsStillWaiting(state)) => {
  const close = clockFromIso(state?.config?.end_time);
  if (!waiting.length) return "";
  const teams = waiting.map((entry) => entry.team).join(", ");
  const when = close ? `judging interviews close at ${close}. ` : "";
  const lead = when ? `Attention teams: ${when}` : "Attention teams. ";
  return `${lead}The following teams still need a judging interview. Please send someone to the judging area now: ${teams}.`;
};

const setText = (el, text) => {
  if (el && el.textContent !== text) el.textContent = text;
};

const renderBanner = (state, phase, waiting) => {
  const banner = els.reminderBanner;
  if (!banner) return;
  const show = phase.phase === "now" && waiting.length > 0;
  if (els.announceDot) els.announceDot.hidden = !show;
  if (!show) {
    banner.hidden = true;
    patchHtml(banner, "");
    return;
  }
  const close = clockFromIso(state?.config?.end_time);
  patchHtml(
    banner,
    `<span><strong>Interviews close at ${escapeHtml(close)}</strong> (${escapeHtml(
      plural(phase.minutesLeft, "minute", "minutes")
    )} left). ${escapeHtml(plural(waiting.length, "team still needs", "teams still need"))} an interview.</span>
     <button type="button" class="primary" data-goto="announce">Show announcement</button>`
  );
  banner.hidden = false;
};

const renderTiming = (state, phase) => {
  const closeIso = state?.config?.end_time;
  const close = clockFromIso(closeIso);
  const closeMinutes = minutesOfIso(closeIso);
  const announceAt =
    closeMinutes == null ? "" : formatClock(closeMinutes - ANNOUNCE_MINUTES_BEFORE_CLOSE);
  let html = "";
  let tone = "";
  if (phase.phase === "early") {
    html = `Announce at <strong>${escapeHtml(announceAt)}</strong>, one hour before interviews close at ${escapeHtml(close)}.`;
  } else if (phase.phase === "now") {
    html = `<strong>Time to announce.</strong> Interviews close at ${escapeHtml(close)}, ${escapeHtml(
      plural(phase.minutesLeft, "minute", "minutes")
    )} left.`;
    tone = "now";
  } else if (phase.phase === "closed") {
    html = `Interviews closed at ${escapeHtml(close)}.`;
    tone = "closed";
  }
  els.announceTiming.dataset.tone = tone;
  els.announceTiming.hidden = !html;
  patchHtml(els.announceTiming, html);
};

export const renderAnnounce = (state) => {
  if (!els.announceList) return;
  const roster = teamRoster(state).filter((entry) => !entry.notCompeting);
  const waiting = teamsStillWaiting(state);
  const phase = announcePhase(state);

  renderTiming(state, phase);
  renderBanner(state, phase, waiting);
  setText(els.announceCount, roster.length ? `${waiting.length} of ${roster.length} teams` : "");

  if (!roster.length) {
    patchHtml(els.announceList, `<p class="empty">No suggested times yet, so there's nobody to announce.</p>`);
  } else if (!waiting.length) {
    patchHtml(els.announceList, `<p class="empty all-done">Every team has been interviewed.</p>`);
  } else {
    patchHtml(
      els.announceList,
      waiting
        .map(
          (entry) =>
            `<span class="announce-team${entry.status === "no-show" ? " is-no-show" : ""}">${escapeHtml(entry.team)}${
              entry.status === "no-show" ? '<small>missed</small>' : ""
            }</span>`
        )
        .join("")
    );
  }

  const script = announcementText(state, waiting);
  setText(els.announceScript, script || "Nothing to announce.");
  els.copyAnnounce.disabled = !script;
};

/** Clipboard API needs a secure context; on plain http over the LAN fall back to execCommand. */
const copyText = async (text) => {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) throw new Error("copy failed");
};

export const initAnnounce = (getState) => {
  els.copyAnnounce?.addEventListener("click", async () => {
    const text = announcementText(getState());
    if (!text) return;
    try {
      await copyText(text);
      setStatus("Announcement copied.");
    } catch {
      setStatus("Couldn't copy automatically. Select the text and copy it.");
    }
  });
  els.reminderBanner?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-goto]");
    if (button) setUi({ tab: button.dataset.goto });
  });
};
