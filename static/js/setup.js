// Lock state for the setup panel's generate buttons.
import { els, setStatus } from "./dom.js";
import { isBusy } from "./busy.js";
import { getUi } from "./store.js";

let lastLockKey = null;

/** Subscriber: disable generate buttons when locked; announce lock changes once. */
export const applyLockState = (state) => {
  const locked = Boolean(state?.locked);
  const noshowLocked = Boolean(state?.noshow_locked);

  [
    [els.generateBtn, locked],
    [els.generateNoShowBtn, noshowLocked],
  ].forEach(([button, isLocked]) => {
    if (!button) return;
    button.dataset.locked = String(isLocked);
    button.disabled = isLocked || isBusy(button);
  });

  // Only admins can reset, so only they are told to.
  const resetHint = getUi().isAdmin ? " Reset to generate again." : "";
  const key = `${locked}|${noshowLocked}|${resetHint}`;
  if (key === lastLockKey) return;
  lastLockKey = key;
  if (locked) {
    setStatus(`Schedule locked after printing.${resetHint}`);
  } else if (noshowLocked) {
    setStatus(`No-show schedule locked after printing.${resetHint}`);
  }
};
