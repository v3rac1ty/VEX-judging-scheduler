// Entry point: subscribe renderers to the store, wire DOM events, load state, go live.
import { els } from "./dom.js";
import { subscribe, setUi, setConnection, getState, tick } from "./store.js";
import { applyLockState } from "./setup.js";
import { initDraft, applyDraftToInputs, applySetupAccess } from "./draft.js";
import { initUploads, renderUploads, renderUploadAccess } from "./uploads.js";
import { initAdmin, renderAdmin } from "./admin.js";
import { initTm, renderTm } from "./tm.js";
import { startLive } from "./live.js";
import { renderSchedule } from "./render/schedule.js";
import { renderScheduleSelect } from "./render/scheduleSelect.js";
import { renderPrint } from "./render/print.js";
import { renderNoShow, initNoShowToggleTracking } from "./render/noshow.js";
import { renderConnection } from "./render/connection.js";
import { renderTabs, initTabs } from "./render/tabs.js";
import { renderSetupHints } from "./render/setupHints.js";
import { renderAnnounce, initAnnounce } from "./render/announce.js";
import {
  loadInitialState,
  generateSchedule,
  generateNoShowSchedule,
  printSchedule,
  resetAll,
  onScheduleSelectChange,
  onScheduleClick,
  onNoShowClick,
  onSearchInput,
  onFilterClick,
  onViewClick,
} from "./actions.js";

// Every render is a subscriber: (state, ui, draft, connection) -> DOM, limited
// to the slices it reads so frequent draft/connection updates stay cheap.
subscribe((state) => applyLockState(state), ["state", "ui"]);
subscribe(renderTabs, ["state", "ui"]);
subscribe(renderScheduleSelect, ["state", "ui"]);
subscribe(renderSchedule, ["state", "ui"]);
subscribe(renderPrint, ["state", "ui"]);
subscribe((state) => renderNoShow(state), ["state"]);
subscribe((state) => renderAnnounce(state), ["state", "clock"]);
subscribe((_state, _ui, draft) => applyDraftToInputs(draft), ["draft"]);
subscribe((_state, _ui, draft) => renderUploads(draft), ["draft"]);
subscribe((_state, _ui, draft) => renderSetupHints(draft), ["draft"]);
subscribe(renderConnection, ["connection"]);
subscribe(renderAdmin, ["ui"]);
subscribe(applySetupAccess, ["ui"]);
subscribe(renderUploadAccess, ["ui"]);
subscribe(renderTm, ["state", "ui", "tm"]);

initTabs();
initDraft();
initUploads();
initNoShowToggleTracking();
initAdmin();
initTm();
initAnnounce(getState);

els.generateBtn.addEventListener("click", generateSchedule);
els.printBtn.addEventListener("click", printSchedule);
els.generateNoShowBtn.addEventListener("click", generateNoShowSchedule);
els.resetBtn.addEventListener("click", resetAll);
els.scheduleSelect.addEventListener("change", onScheduleSelectChange);
els.teamSearch.addEventListener("input", onSearchInput);
els.filterButtons.forEach((button) => button.addEventListener("click", onFilterClick));
els.viewButtons.forEach((button) => button.addEventListener("click", onViewClick));

// One delegated listener per list instead of per-row listeners on every render.
els.schedule.addEventListener("click", onScheduleClick);
els.noShow.addEventListener("click", onNoShowClick);

// Time-based UI (the one-hour announcement reminder) re-checks twice a minute.
setInterval(tick, 30_000);

// Browsers may restore a previous search value on reload. (This also runs every
// ui subscriber once, so the signed-out view is applied before the status arrives.)
setUi({ search: els.teamSearch.value });
setConnection({ status: "connecting" });

// Render from a plain GET first so the page works before (or without) SSE,
// then stay live. Revision guards make the order of the two irrelevant.
loadInitialState().finally(startLive);
