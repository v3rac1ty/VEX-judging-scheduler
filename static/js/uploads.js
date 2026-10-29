// File dropzones (initial match schedule + final match schedule).
//
// The client never parses files: it reads the text and PATCHes it to the shared
// draft; the server parses it and sends back a preview (counts, first match) and
// the derived judging times. The read-only textareas just mirror the draft.
import { els, setStatus } from "./dom.js";
import { patchDraft } from "./draft.js";
import { describePreview } from "./util.js";

/** Wire click-to-browse, drag-and-drop and input change to onFile(file). */
const setupDropzone = ({ zone, input, onFile }) => {
  if (!zone || !input) return;

  const handle = async (file) => {
    if (!file) return;
    try {
      await onFile(file);
    } catch {
      setStatus(`Could not read ${file.name}.`);
    } finally {
      // Allow re-selecting the same file to fire "change" again.
      input.value = "";
    }
  };

  zone.addEventListener("click", (event) => {
    // The hidden input lives inside the zone; ignore its own bubbled click.
    if (event.target !== input) input.click();
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("is-dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    handle(event.dataTransfer?.files?.[0]);
  });
  input.addEventListener("change", (event) => handle(event.target.files?.[0]));
};

const upload = async (file, key, doneMessage) => {
  const text = await file.text();
  const result = await patchDraft({ [key]: { text, name: file.name } });
  setStatus(result.ok ? doneMessage : result.error || `Could not upload ${file.name}.`);
};

export const initUploads = () => {
  setupDropzone({
    zone: els.dropzone,
    input: els.fileInput,
    onFile: (file) => upload(file, "match_schedule", `Loaded ${file.name}.`),
  });

  setupDropzone({
    zone: els.finalDropzone,
    input: els.finalFileInput,
    onFile: (file) => upload(file, "final_schedule", `Loaded final schedule ${file.name}.`),
  });
};

// Last value written to each element, so big texts are only reassigned (and the
// browser only re-lays-out the textarea) when they actually change.
const lastText = new WeakMap();

const setText = (textarea, text) => {
  if (!textarea || lastText.get(textarea) === text) return;
  lastText.set(textarea, text);
  if (textarea.value !== text) textarea.value = text;
};

const setPreview = (el, name, preview) => {
  if (!el) return;
  const { text, error } = describePreview(name, preview);
  if (el.textContent !== text) el.textContent = text;
  el.classList.toggle("is-error", error);
};

/** Subscriber: mirror the shared draft's uploaded texts and previews. */
export const renderUploads = (draft) => {
  if (!draft) return;
  setText(els.matchSchedule, draft.match_schedule?.text ?? "");
  setText(els.finalMatchSchedule, draft.final_schedule?.text ?? "");
  setPreview(els.matchPreview, draft.match_schedule?.name, draft.preview);
  setPreview(els.finalPreview, draft.final_schedule?.name, draft.final_preview);
};
