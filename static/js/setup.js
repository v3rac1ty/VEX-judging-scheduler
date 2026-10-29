// Lock state for the setup panel's generate buttons.
import { els, setStatus } from "./dom.js";
import { isBusy } from "./busy.js";

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

  const key = `${locked}|${noshowLocked}`;
  if (key === lastLockKey) return;
  lastLockKey = key;
  if (locked) {
    setStatus("Schedule locked after printing. Reset to generate again.");
  } else if (noshowLocked) {
    setStatus("No-show schedule locked after printing. Reset to generate again.");
  }
};
