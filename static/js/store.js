// Single source of truth for everything the UI renders from.
//
//   state       shared scheduling state (server-owned, `revision` guarded)
//   draft       shared setup form: judge pairs, times, uploaded texts, event stats
//               (server-owned, own `revision` counter)
//   connection  live-sync status for the header pill (per device)
//   tm          Tournament Manager source status (server-owned, shared; null until known)
//   ui          per-device prefs: team search, which schedule this device views, and
//               whether this device is signed in as admin (never shared or persisted)
//
// Rendering is a pure function of these. Every server payload that carries
// state or draft must go through setState / applyDraft*, which enforce the
// revision guards so out-of-order responses can never move the UI backwards.
import { api } from "./api.js";

let state = null;
let stateRevision = -1;
let draft = null;
let draftRevision = -1;
// The pinned schedule view survives a reload of this tab (per tab, not shared).
// Storage can throw (private mode, blocked site data), so every access is guarded.
const VIEW_KEY = "judging.viewScheduleId";
const loadViewScheduleId = () => {
  try {
    return sessionStorage.getItem(VIEW_KEY) || null;
  } catch {
    return null;
  }
};
const saveViewScheduleId = (id) => {
  try {
    if (id == null) sessionStorage.removeItem(VIEW_KEY);
    else sessionStorage.setItem(VIEW_KEY, String(id));
  } catch {
    // Not persisted; the view still works for this page load.
  }
};

let ui = { search: "", viewScheduleId: loadViewScheduleId(), isAdmin: false };
let connection = { status: "connecting", clients: 0 };
let tm = null;

const ALL = ["state", "draft", "connection", "ui", "tm"];
const subscribers = new Map(); // fn -> Set of slices it cares about

const notify = (changed) => {
  subscribers.forEach((deps, fn) => {
    if (!changed.some((slice) => deps.has(slice))) return;
    try {
      fn(state, ui, draft, connection, tm);
    } catch (error) {
      console.error("Subscriber failed", error);
    }
  });
};

export const getState = () => state;
export const getUi = () => ui;
export const getDraft = () => draft;
export const getConnection = () => connection;
export const getTm = () => tm;

const revisionOf = (value) => {
  const n = Number(value?.revision);
  return Number.isFinite(n) ? n : 0;
};

// ---------------------------------------------------------------- state

/**
 * Apply a scheduling-state payload (SSE event, POST response, GET /api/state).
 * Payloads older than what we already hold are ignored. A bundled `draft`
 * (GET /api/state) is routed to the draft store. Returns true if applied.
 */
export const setState = (next) => {
  if (!next || typeof next !== "object" || Object.keys(next).length === 0) {
    return false;
  }
  const { draft: bundledDraft, ...rest } = next;
  if (bundledDraft) {
    applyDraftFull(bundledDraft);
  }
  if (revisionOf(rest) < stateRevision) {
    return false;
  }
  stateRevision = revisionOf(rest);
  state = rest;

  // A pinned view whose schedule is gone (e.g. after reset) falls back to
  // following the server's active schedule.
  const changed = ["state"];
  if (ui.viewScheduleId != null) {
    const exists = (rest.schedules || []).some((s) => s.id === ui.viewScheduleId);
    if (!exists) {
      ui = { ...ui, viewScheduleId: null };
      saveViewScheduleId(null);
      changed.push("ui");
    }
  }
  notify(changed);
  return true;
};

// ---------------------------------------------------------------- draft

const mergeObject = (base, changes) => ({ ...(base || {}), ...(changes || {}) });

/** Replace the whole draft if it is at least as new as the one we hold. */
export const applyDraftFull = (next) => {
  if (!next || typeof next !== "object") return false;
  const revision = revisionOf(next);
  if (revision < draftRevision) return false;
  draftRevision = revision;
  draft = { ...next, revision };
  notify(["draft"]);
  return true;
};

let refetching = false;
let refetchAgain = false;

/** Fetch the whole draft (used after a gap in patch revisions). */
export const refetchDraft = async () => {
  if (refetching) {
    refetchAgain = true;
    return;
  }
  refetching = true;
  try {
    do {
      refetchAgain = false;
      const result = await api.get("/api/draft");
      if (result.ok) applyDraftFull(result.data);
    } while (refetchAgain);
  } finally {
    refetching = false;
  }
};

/**
 * Apply a partial draft update `{revision, changes}`. Only the very next
 * revision merges in; older ones are ignored and a gap triggers a refetch.
 */
export const applyDraftPatch = (patch) => {
  if (!patch || typeof patch !== "object") return false;
  const revision = revisionOf(patch);
  if (!draft || revision > draftRevision + 1) {
    refetchDraft();
    return false;
  }
  if (revision <= draftRevision) return false;

  const changes = patch.changes || {};
  const next = { ...draft, revision };
  Object.entries(changes).forEach(([key, value]) => {
    if (key === "event_stats") {
      next.event_stats = mergeObject(draft.event_stats, value);
    } else if (key === "match_schedule" || key === "final_schedule") {
      next[key] = value && typeof value === "object" ? mergeObject(draft[key], value) : value;
    } else if (key !== "revision") {
      next[key] = value;
    }
  });
  draftRevision = revision;
  draft = next;
  notify(["draft"]);
  return true;
};

// ------------------------------------------------------ ui + connection

/** Merge per-device UI preferences (search term, viewed schedule, ...). Never sent to the server. */
export const setUi = (partial) => {
  const previousView = ui.viewScheduleId;
  ui = { ...ui, ...partial };
  if (ui.viewScheduleId !== previousView) saveViewScheduleId(ui.viewScheduleId);
  notify(["ui"]);
};

export const setConnection = (partial) => {
  connection = { ...connection, ...partial };
  notify(["connection"]);
};

/**
 * Replace the Tournament Manager status (SSE `tm` event, fetch/poll responses,
 * GET /api/tm/status). The status carries no revision, so the latest arrival wins.
 */
export const setTm = (next) => {
  if (!next || typeof next !== "object" || Array.isArray(next)) return false;
  tm = next;
  notify(["tm"]);
  return true;
};

/**
 * Register a render function called as fn(state, ui, draft, connection, tm).
 * `slices` limits which kinds of change trigger it (default: all).
 * Returns an unsubscribe function.
 */
export const subscribe = (fn, slices = ALL) => {
  subscribers.set(fn, new Set(slices));
  return () => subscribers.delete(fn);
};
