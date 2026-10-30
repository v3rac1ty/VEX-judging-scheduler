// Admin sign-in (PIN). Admin state is per device: it is never shared over SSE
// and never persisted. The server owns the session (an HttpOnly cookie); this
// module only mirrors it into `ui.isAdmin`, re-checking on boot, after SSE
// reconnects, and whenever an admin-only request is rejected.
//
// The PIN lives only in the dialog's input while it is open: it is cleared on
// submit and on close, and never written to storage or logged.
import { api, setAdminRequiredHandler, ADMIN_REQUIRED_MESSAGE } from "./api.js";
import { els, setStatus } from "./dom.js";
import { getUi, setUi } from "./store.js";

let submitting = false;

/** Re-read the session from the server. Network failures leave the current state alone. */
export const refreshAdminStatus = async () => {
  const result = await api.get("/api/admin/status");
  if (!result.ok) return;
  const admin = Boolean(result.data?.admin);
  if (admin !== getUi().isAdmin) setUi({ isAdmin: admin });
};

// ---------------------------------------------------------------- dialog

const setError = (message) => {
  const { adminError, adminPin } = els;
  adminError.textContent = message || "";
  if (message) adminPin.setAttribute("aria-invalid", "true");
  else adminPin.removeAttribute("aria-invalid");
};

const setSubmitting = (value) => {
  submitting = value;
  els.adminSubmit.disabled = value;
  els.adminForm.setAttribute("aria-busy", String(value));
};

const openDialog = () => {
  const { adminDialog, adminPin } = els;
  if (adminDialog.open) return;
  adminPin.value = "";
  setError("");
  setSubmitting(false);
  if (typeof adminDialog.showModal === "function") adminDialog.showModal();
  else adminDialog.setAttribute("open", "");
  adminPin.focus();
};

const closeDialog = () => {
  const { adminDialog } = els;
  if (adminDialog.open) adminDialog.close();
};

/** `close` fires for Esc, Cancel, backdrop click and success alike. */
const onDialogClosed = () => {
  els.adminPin.value = "";
  setError("");
  setSubmitting(false);
  // Hand focus back to the header button (Safari does not focus buttons on click,
  // so the browser's own restore is unreliable).
  els.adminBtn.focus();
};

const submit = async (event) => {
  event.preventDefault();
  if (submitting) return;
  const pin = els.adminPin.value;
  if (!pin) {
    setError("Enter the admin PIN.");
    els.adminPin.focus();
    return;
  }

  setError("");
  setSubmitting(true);
  const result = await api.post("/api/admin/login", { pin });
  setSubmitting(false);

  if (result.ok && result.data?.admin) {
    els.adminPin.value = "";
    setUi({ isAdmin: true });
    closeDialog();
    setStatus("Signed in as admin.");
    return;
  }

  // 401 "Incorrect PIN." and 429 "Too many attempts. ..." arrive as result.error.
  setError(result.error || "Could not sign in. Try again.");
  if (els.adminDialog.open) {
    els.adminPin.focus();
    els.adminPin.select();
  }
};

// ---------------------------------------------------------------- sign out

const signOut = async () => {
  els.adminBtn.disabled = true;
  try {
    const result = await api.post("/api/admin/logout", {});
    if (!result.ok) {
      setStatus(result.error || "Could not sign out.");
      return;
    }
    setUi({ isAdmin: false });
    setStatus("Signed out.");
  } finally {
    els.adminBtn.disabled = false;
  }
};

const onAdminButtonClick = () => {
  if (getUi().isAdmin) signOut();
  else openDialog();
};

// ---------------------------------------------------------------- render

/** Subscriber (ui): body class, header button, and closing a stale dialog. */
export const renderAdmin = (_state, ui) => {
  const admin = Boolean(ui?.isAdmin);
  document.body.classList.toggle("is-admin", admin);

  const { adminBtn, adminBtnLabel } = els;
  if (adminBtn && adminBtnLabel) {
    const label = admin ? "Admin · Sign out" : "Admin sign-in";
    if (adminBtnLabel.textContent !== label) adminBtnLabel.textContent = label;
    adminBtn.dataset.admin = String(admin);
    if (admin) adminBtn.removeAttribute("aria-haspopup");
    else adminBtn.setAttribute("aria-haspopup", "dialog");
  }

  if (admin) closeDialog();
};

export const initAdmin = () => {
  // A rejected admin-only request means the session ended (expired, PIN changed,
  // server restarted). Flip to the signed-out view; callers surface the message.
  setAdminRequiredHandler(() => {
    if (getUi().isAdmin) setUi({ isAdmin: false });
    setStatus(ADMIN_REQUIRED_MESSAGE);
  });

  if (!els.adminBtn || !els.adminDialog) return;

  els.adminBtn.addEventListener("click", onAdminButtonClick);
  els.adminForm.addEventListener("submit", submit);
  els.adminCancel.addEventListener("click", closeDialog);
  els.adminDialog.addEventListener("close", onDialogClosed);
  // Clicking the backdrop (the dialog element itself, outside the form) closes it.
  els.adminDialog.addEventListener("click", (event) => {
    if (event.target === els.adminDialog) closeDialog();
  });

  refreshAdminStatus();
};
