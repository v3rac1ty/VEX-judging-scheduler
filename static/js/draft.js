// Shared setup form <-> server draft.
//
// Every setup input is PATCHed to /api/draft (debounced while typing, at once on
// change/blur) and every incoming draft is written back into the inputs, with
// two protections so live updates never fight the person typing:
//   * the focused input is never overwritten;
//   * an input with unsent or in-flight local edits is never overwritten
//     (last writer wins once the PATCH lands).
//
// Only the admin can edit the draft (PATCH /api/draft is admin-gated): for
// everyone else the inputs are disabled, and nothing is ever sent. Signing out
// (or an expired session) drops unsent edits so no debounced PATCH can 401 later.
import { els, setStatus } from "./dom.js";
import { api, ADMIN_REQUIRED_MESSAGE } from "./api.js";
import { getDraft, getUi, applyDraftPatch } from "./store.js";

const DEBOUNCE_MS = 300;

export const STAT_DEFAULTS = {
  rounds_min: 8,
  rounds_max: 8,
  spacing_min: 3,
  spacing_max: 14,
  spacing_avg: 7,
  repeat_min: 1,
  repeat_max: 2,
  repeat_avg: 1,
  distinct_min: 21,
  distinct_max: 24,
  distinct_avg: 23,
};

const field = (path, el) => ({ key: path.join("."), path, el });

const FIELDS = [
  field(["judge_pairs"], els.judgePairs),
  field(["slot_minutes"], els.slotMinutes),
  field(["block_minutes"], els.blockMinutes),
  field(["start_time"], els.startTime),
  field(["end_time"], els.endTime),
  ...Object.keys(STAT_DEFAULTS).map((key) => field(["event_stats", key], els.stats[key])),
].filter((f) => f.el);

const readPath = (obj, path) => path.reduce((acc, key) => acc?.[key], obj);

const nestPatch = (path, value) =>
  path.reduceRight((acc, key) => ({ [key]: acc }), value);

/** Deep-merge `patch` into `target` (plain objects only). */
const mergeInto = (target, patch) => {
  Object.entries(patch).forEach(([key, value]) => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      target[key] = mergeInto(target[key] || {}, value);
    } else {
      target[key] = value;
    }
  });
  return target;
};

let pending = {}; // nested patch not yet sent
const pendingKeys = new Set();
const inFlight = new Map(); // field key -> number of unacknowledged PATCHes
let timer = null;
let queue = Promise.resolve(); // PATCHes go out strictly in order

const isDirty = (key) => pendingKeys.has(key) || (inFlight.get(key) || 0) > 0;

/** Write the draft into the inputs, skipping focused and locally-dirty ones. */
export const applyDraftToInputs = (draft = getDraft()) => {
  if (!draft) return;
  FIELDS.forEach(({ key, path, el }) => {
    // A disabled input cannot be edited, so focus no longer protects it.
    if ((el === document.activeElement && !el.disabled) || isDirty(key)) return;
    const value = readPath(draft, path);
    const text = value == null ? "" : String(value);
    if (el.value !== text) {
      el.value = text;
    }
  });
};

const enqueue = (body, keys) => {
  keys.forEach((key) => inFlight.set(key, (inFlight.get(key) || 0) + 1));
  queue = queue.then(async () => {
    // Signed out while this was queued: do not send (it would only 401).
    const skipped = !getUi().isAdmin;
    const result = skipped
      ? { ok: false, status: 0, data: null, error: ADMIN_REQUIRED_MESSAGE }
      : await api.patch("/api/draft", body);
    if (result.ok) {
      applyDraftPatch(result.data);
    } else if (!skipped) {
      setStatus(result.error || "Could not save your changes.");
    }
    keys.forEach((key) => {
      const left = (inFlight.get(key) || 0) - 1;
      if (left > 0) inFlight.set(key, left);
      else inFlight.delete(key);
    });
    // Failed or superseded edits snap back to the shared value.
    applyDraftToInputs();
    return result;
  });
  return queue;
};

/** Forget edits that have not been sent yet (their inputs snap back to the shared value). */
const discardPending = () => {
  clearTimeout(timer);
  timer = null;
  pending = {};
  pendingKeys.clear();
};

/** Send any debounced edits now. Resolves once everything queued has landed. */
export const flushDraft = () => {
  if (!getUi().isAdmin) {
    discardPending();
    return queue;
  }
  clearTimeout(timer);
  timer = null;
  if (!pendingKeys.size && !Object.keys(pending).length) {
    return queue;
  }
  const body = pending;
  const keys = [...pendingKeys];
  pending = {};
  pendingKeys.clear();
  return enqueue(body, keys);
};

/** PATCH an arbitrary draft fragment (uploads) after flushing typed edits. */
export const patchDraft = async (body) => {
  if (!getUi().isAdmin) {
    return { ok: false, status: 0, data: null, error: ADMIN_REQUIRED_MESSAGE };
  }
  flushDraft();
  return enqueue(body, []);
};

const onInput = ({ key, path, el }) => {
  if (!getUi().isAdmin) return;
  mergeInto(pending, nestPatch(path, el.value));
  pendingKeys.add(key);
  clearTimeout(timer);
  timer = setTimeout(flushDraft, DEBOUNCE_MS);
};

let lastAccess = null;

/**
 * Subscriber (ui): enable the setup inputs for the admin, disable them for
 * everyone else. Signing out discards unsent edits; either way the inputs are
 * re-synced to the shared draft since focus protection no longer applies.
 */
export const applySetupAccess = (_state, ui) => {
  const admin = Boolean(ui?.isAdmin);
  if (admin === lastAccess) return;
  lastAccess = admin;
  if (!admin) discardPending();
  FIELDS.forEach(({ el }) => {
    el.disabled = !admin;
  });
  applyDraftToInputs();
};

export const initDraft = () => {
  FIELDS.forEach((f) => {
    f.el.addEventListener("input", () => onInput(f));
    f.el.addEventListener("change", () => {
      onInput(f);
      flushDraft();
    });
    f.el.addEventListener("blur", () => {
      flushDraft();
      // Focus is gone: show the latest shared value unless our own PATCH is still out.
      applyDraftToInputs();
    });
  });
};

/** Request body for /api/generate, built from the current draft values. */
export const collectSetup = (draft = getDraft()) => {
  const stats = {};
  Object.entries(STAT_DEFAULTS).forEach(([key, fallback]) => {
    stats[key] = Number(draft?.event_stats?.[key] || fallback);
  });
  return {
    judge_pairs: draft?.judge_pairs ?? "",
    slot_minutes: draft?.slot_minutes ?? "",
    block_minutes: draft?.block_minutes ?? "",
    start_time: draft?.start_time ?? "",
    end_time: draft?.end_time ?? "",
    match_schedule: draft?.match_schedule?.text ?? "",
    event_stats: stats,
  };
};

export const collectEventStats = (draft = getDraft()) => collectSetup(draft).event_stats;
