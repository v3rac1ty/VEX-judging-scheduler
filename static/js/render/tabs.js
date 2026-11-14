// Tabs: Setup (admin) · Check-in · Announce · No-shows. The open tab is a
// per-device preference (ui.tab); with none saved, the admin starts on Setup
// until a schedule exists and everyone else starts on Check-in.
import { els } from "../dom.js";
import { setUi } from "../store.js";
import { teamRoster } from "../selectors.js";

export const TABS = ["setup", "checkin", "announce", "noshow"];

const hasAnySchedule = (state) =>
  (state?.schedules || []).some((schedule) => (schedule.slots || []).length);

/** The tab this device shows right now. */
export const effectiveTab = (state, ui) => {
  const admin = Boolean(ui?.isAdmin);
  let tab = TABS.includes(ui?.tab) ? ui.tab : null;
  if (tab === "setup" && !admin) tab = null;
  if (!tab) tab = admin && !hasAnySchedule(state) ? "setup" : "checkin";
  return tab;
};

export const renderTabs = (state, ui) => {
  const active = effectiveTab(state, ui);
  els.tabButtons.forEach((button) => {
    const selected = button.dataset.tab === active;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  els.tabPanels.forEach((panel) => {
    const hidden = panel.dataset.tab !== active;
    if (panel.hidden !== hidden) panel.hidden = hidden;
  });

  // "12/40" on the Check-in tab: interviewed out of competing teams.
  const roster = teamRoster(state).filter((entry) => !entry.notCompeting);
  const done = roster.filter((entry) => entry.status === "checked").length;
  const text = roster.length ? `${done}/${roster.length}` : "";
  if (els.checkinCount && els.checkinCount.textContent !== text) els.checkinCount.textContent = text;
};

const visibleTabs = () => els.tabButtons.filter((button) => button.offsetParent !== null);

export const initTabs = () => {
  els.tabButtons.forEach((button) => {
    button.addEventListener("click", () => setUi({ tab: button.dataset.tab }));
    // Arrow keys move between tabs (WAI-ARIA tabs pattern).
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const tabs = visibleTabs();
      const index = tabs.indexOf(button);
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      if (!next) return;
      event.preventDefault();
      setUi({ tab: next.dataset.tab });
      next.focus();
    });
  });
};
