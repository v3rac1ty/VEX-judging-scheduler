// Tournament Manager (TM) source: "Fetch from TM" + "Auto-update" for the initial
// match schedule and the final schedule.
//
// The server owns everything: it fetches from TM, writes the text into the shared
// draft (which reaches every device through the normal draft SSE patch, so this
// module never touches the inputs) and keeps the polling flags. The `tm` store
// slice mirrors the server's status, fed by the SSE `tm` event and by the
// fetch/poll responses. The toggle is NOT optimistic: it only ever shows the
// server's `polling` value and reverts with a message when a request fails.
//
// Visibility composes three independent rules, none of which re-enables a
// control another rule disabled:
//   * admin-only      the `admin-only` class (body.is-admin) hides the box for everyone else
//   * configured      the `hidden` attribute while `tm` is unknown or not configured
//   * lock state      initial controls disabled once state.locked, final ones once
//                     state.noshow_locked (the server stops polling then too)
import { api } from "./api.js";
import { els, setStatus } from "./dom.js";
import { isBusy, withBusy } from "./busy.js";
import { getState, getTm, getUi, setTm, subscribe } from "./store.js";

const TARGETS = {
  match_schedule: { lockKey: "locked", label: "initial match schedule" },
  final_schedule: { lockKey: "noshow_locked", label: "final schedule" },
};

// Per-device, per-target feedback that is not part of the shared status:
// { text, error, lastChange } where lastChange pins a "No changes" note to the
// status it was produced for (any later change from the server clears it).
const notes = {};
const togglePending = new Set();

const clearNote = (target) => {
  delete notes[target];
};

const setNote = (target, note) => {
  notes[target] = note;
};

/** ISO timestamp -> device-locale clock text, e.g. "10:42 AM"; "" if unparseable. */
const formatClock = (iso) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};

const isLocked = (state, target) => Boolean(state?.[TARGETS[target].lockKey]);

/** Build the status line for one target: { text, error }. */
const describe = (tm, state, target) => {
  const note = notes[target];
  if (note?.error) return { text: note.text, error: true };
  if (tm.error) return { text: tm.error, error: true };

  const parts = [];
  const polling = Boolean(tm.polling?.[target]);
  if (note?.pending) {
    parts.push(note.text);
  } else {
    const clock = tm.last_fetch ? formatClock(tm.last_fetch) : "";
    if (clock) {
      const division = tm.division != null ? ` · Division ${tm.division}` : "";
      parts.push(`Last fetched ${clock}${division}`);
    } else if (tm.division != null) {
      parts.push(`Division ${tm.division} · Not fetched yet`);
    }
    if (note && (note.lastChange ?? null) === (tm.last_change ?? null)) parts.push(note.text);
  }
  if (polling) {
    parts.push(`Auto-updating every ${tm.poll_seconds} s`);
  } else if (isLocked(state, target)) {
    parts.push("Locked, so TM updates are off");
  }
  return { text: parts.join(" · "), error: false };
};

/** Subscriber (state, ui, tm): visibility, disabled state, switch value, status line. */
export const renderTm = (state, _ui, _draft, _connection, tm) => {
  const configured = Boolean(tm && tm.configured);
  Object.keys(TARGETS).forEach((target) => {
    const parts = els.tm?.[target];
    if (!parts?.box) return;
    const { box, fetch, auto, status } = parts;
    if (box.hidden === configured) box.hidden = !configured;
    if (!configured) return;

    const locked = isLocked(state, target);

    // Same convention as the generate buttons: withBusy restores to data-locked.
    fetch.dataset.locked = String(locked);
    fetch.disabled = locked || isBusy(fetch);

    const pending = togglePending.has(target);
    auto.dataset.locked = String(locked);
    auto.disabled = locked || pending;
    auto.setAttribute("aria-busy", String(pending));
    const polling = Boolean(tm.polling?.[target]);
    if (auto.checked !== polling) auto.checked = polling;

    const { text, error } = describe(tm, state, target);
    if (status.textContent !== text) status.textContent = text;
    status.classList.toggle("is-error", error);
  });
};

const rerender = () => renderTm(getState(), getUi(), null, null, getTm());

// ---------------------------------------------------------------- actions

const fetchNow = async (target) => {
  const { fetch } = els.tm[target];
  setNote(target, { text: "Fetching from TM…", pending: true });
  rerender();
  await withBusy(fetch, async () => {
    const result = await api.post("/api/tm/fetch", { target });
    if (!result.ok) {
      setNote(target, { text: result.error || "Could not fetch from TM.", error: true });
      setStatus(result.error || "Could not fetch from TM.");
      return;
    }
    const tm = result.data?.tm;
    const changed = Boolean(result.data?.changed);
    clearNote(target);
    if (!changed) {
      // Pin "No changes" to the status it belongs to; a later server change drops it.
      setNote(target, { text: "No changes", lastChange: tm?.last_change ?? null });
    }
    if (tm) setTm(tm);
    setStatus(
      changed
        ? `Fetched the ${TARGETS[target].label} from TM.`
        : `TM ${TARGETS[target].label} has no changes.`
    );
  });
  rerender();
};

const toggleAuto = async (target) => {
  const { auto } = els.tm[target];
  const wanted = auto.checked;
  // Not optimistic: snap back to the server's value until the server answers.
  auto.checked = Boolean(getTm()?.polling?.[target]);
  if (togglePending.has(target)) return;

  togglePending.add(target);
  clearNote(target);
  rerender();
  const result = await api.post("/api/tm/poll", { target, enabled: wanted });
  togglePending.delete(target);

  if (result.ok && result.data?.tm) {
    setTm(result.data.tm); // renders with the server's returned value
    rerender();
    return;
  }
  const message = result.error || "Could not change auto-update.";
  setNote(target, { text: message, error: true });
  setStatus(message);
  rerender(); // reverts the switch to the last known server value
};

const loadStatus = async () => {
  const result = await api.get("/api/tm/status");
  if (result.ok) setTm(result.data);
  // Failures here (not admin, old server without TM) just leave the controls hidden.
};

export const initTm = () => {
  Object.keys(TARGETS).forEach((target) => {
    const parts = els.tm?.[target];
    if (!parts?.fetch || !parts?.auto) return;
    parts.fetch.addEventListener("click", () => fetchNow(target));
    parts.auto.addEventListener("change", () => toggleAuto(target));
  });

  // /api/tm/* is admin-only: read the status once a session exists (including a
  // session found on boot or restored after an SSE reconnect).
  let wasAdmin = false;
  subscribe((_state, ui) => {
    const admin = Boolean(ui?.isAdmin);
    if (admin && !wasAdmin) loadStatus();
    wasAdmin = admin;
  }, ["ui"]);
};
