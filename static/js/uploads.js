// File dropzones (initial match schedule + final match schedule).
import { els, setStatus } from "./dom.js";
import { updateJudgingTimesFromFile } from "./setup.js";

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

export const initUploads = () => {
  setupDropzone({
    zone: els.dropzone,
    input: els.fileInput,
    onFile: async (file) => {
      const text = await file.text();
      els.matchSchedule.value = text;
      updateJudgingTimesFromFile(text);
      setStatus(`Loaded ${file.name}.`);
    },
  });

  setupDropzone({
    zone: els.finalDropzone,
    input: els.finalFileInput,
    onFile: async (file) => {
      els.finalMatchSchedule.value = await file.text();
      setStatus(`Loaded final schedule ${file.name}.`);
    },
  });
};
