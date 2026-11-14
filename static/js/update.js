// "New version available" notice for admins. The server checks GitHub once at
// startup (judging/updates.py); this reads the result from GET /api/version,
// asking again while that check is still running. Nothing is installed: the
// notice links to the release page. Dismissing hides it for that version on
// this device.
import { api } from "./api.js";
import { els } from "./dom.js";

const RETRY_MS = 5_000;
const MAX_TRIES = 12; // the server gives up on GitHub after a few seconds anyway
const DISMISS_KEY = "judging.updateDismissed";

let info = null;
let retryTimer = null;

const dismissedVersion = () => {
  try {
    return localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
};

const render = () => {
  const banner = els.updateBanner;
  if (!banner) return;
  const show = Boolean(info?.update_available && info.url) && dismissedVersion() !== info.latest;
  banner.hidden = !show;
  if (!show) return;
  els.updateText.textContent = `Version ${info.latest} is available (this is ${info.current}). `
    + "To update, close the server window, replace the program with the new download, and start it again.";
  els.updateLink.href = info.url;
};

const load = async (attempt = 1) => {
  clearTimeout(retryTimer);
  const result = await api.get("/api/version");
  if (!result.ok || !result.data) return;
  info = result.data;
  render();
  if (info.checking && attempt < MAX_TRIES) {
    retryTimer = setTimeout(() => load(attempt + 1), RETRY_MS);
  }
};

/** Re-read after the server restarts (it may be the new version now). */
export const refreshUpdateNotice = () => load();

export const initUpdateNotice = () => {
  els.updateDismiss?.addEventListener("click", () => {
    try {
      if (info?.latest) localStorage.setItem(DISMISS_KEY, info.latest);
    } catch {
      // Not remembered; hidden until the next reload.
    }
    els.updateBanner.hidden = true;
  });
  load();
};
