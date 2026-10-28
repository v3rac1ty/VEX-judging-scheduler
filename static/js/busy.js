// In-flight tracking: disable the triggering control(s) while a request runs
// so slow Wi-Fi cannot cause double submits.

const busy = new Set();

export const isBusy = (el) => busy.has(el);

/**
 * Run fn with the given control(s) disabled. Controls that are also managed by
 * lock state mark that with data-locked="true"; those stay disabled afterwards.
 */
export const withBusy = async (controls, fn) => {
  const list = [controls].flat().filter(Boolean);
  list.forEach((el) => {
    busy.add(el);
    el.disabled = true;
  });
  try {
    return await fn();
  } finally {
    list.forEach((el) => {
      busy.delete(el);
      el.disabled = el.dataset.locked === "true";
    });
  }
};
