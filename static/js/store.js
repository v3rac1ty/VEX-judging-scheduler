// Single source of truth for the shared server state plus per-device UI prefs.
//
// Rendering is a pure function of (state, ui). Every server response that
// carries state must go through setState(); renderers are subscribers. Phase 2
// live updates (SSE) only need to call setState() with each pushed state.

let state = null;
let ui = { search: "" };
const subscribers = new Set();

const notify = () => {
  subscribers.forEach((fn) => {
    try {
      fn(state, ui);
    } catch (error) {
      console.error("Subscriber failed", error);
    }
  });
};

export const getState = () => state;
export const getUi = () => ui;

/** Replace the shared state. Empty/non-object values mean "nothing loaded". */
export const setState = (next) => {
  const isEmpty =
    !next || typeof next !== "object" || Object.keys(next).length === 0;
  state = isEmpty ? null : next;
  notify();
};

/** Merge per-device UI preferences (search term, ...). Never sent to the server. */
export const setUi = (partial) => {
  ui = { ...ui, ...partial };
  notify();
};

/** Register a render function. Returns an unsubscribe function. */
export const subscribe = (fn) => {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
};
