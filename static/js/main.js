// Entry point: subscribe renderers to the store, wire DOM events, load state.
import { els } from "./dom.js";
import { subscribe, setUi } from "./store.js";
import { applyLockState, applyEventStats, initSetup } from "./setup.js";
import { initUploads } from "./uploads.js";
import { renderSchedule } from "./render/schedule.js";
import { renderScheduleSelect } from "./render/scheduleSelect.js";
import { renderPrint } from "./render/print.js";
import { renderNoShow, initNoShowToggleTracking } from "./render/noshow.js";
import {
  loadInitialState,
  generateSchedule,
  generateNoShowSchedule,
  printSchedule,
  resetAll,
  setActiveSchedule,
  onScheduleClick,
  onNoShowClick,
  onSearchInput,
} from "./actions.js";

// Every render is a subscriber: (state, ui) -> DOM.
subscribe((state) => applyLockState(state));
subscribe((state) => applyEventStats(state));
subscribe((state) => renderScheduleSelect(state));
subscribe((state, ui) => renderSchedule(state, ui));
subscribe((state) => renderPrint(state));
subscribe((state) => renderNoShow(state));

initSetup();
initUploads();
initNoShowToggleTracking();

els.generateBtn.addEventListener("click", generateSchedule);
els.printBtn.addEventListener("click", printSchedule);
els.generateNoShowBtn.addEventListener("click", generateNoShowSchedule);
els.resetBtn.addEventListener("click", resetAll);
els.scheduleSelect.addEventListener("change", setActiveSchedule);
els.teamSearch.addEventListener("input", onSearchInput);

// One delegated listener per list instead of per-row listeners on every render.
els.schedule.addEventListener("click", onScheduleClick);
els.noShow.addEventListener("click", onNoShowClick);

// Browsers may restore a previous search value on reload.
setUi({ search: els.teamSearch.value });
loadInitialState();
