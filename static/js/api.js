// Thin fetch wrapper. Never throws: always resolves to {ok, status, data, error}.

const NETWORK_ERROR =
  "Cannot reach the server. Check your network connection and try again.";

// Shown whenever an admin-only action is attempted without a valid session.
export const ADMIN_REQUIRED_MESSAGE =
  "Admin sign-in required. Use \"Admin sign-in\" at the top of the page.";

// The server's exact reply for a missing/expired admin session.
const ADMIN_REQUIRED_DETAIL = "Admin PIN required.";

// Registered by admin.js (api.js must not import the store). Called once per
// such response; callers never retry, they just surface result.error.
let adminRequiredHandler = null;
export const setAdminRequiredHandler = (handler) => {
  adminRequiredHandler = handler;
};

/** Turn a FastAPI error body (string, validation array, or other) into text. */
const describeDetail = (detail) => {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (typeof item === "string" ? item : item?.msg))
      .filter(Boolean);
    return parts.join("; ");
  }
  return "";
};

const request = async (method, path, body) => {
  const init = { method, cache: "no-store" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch {
    return { ok: false, status: 0, data: null, error: NETWORK_ERROR };
  }

  let text = "";
  try {
    text = await response.text();
  } catch {
    return { ok: false, status: response.status, data: null, error: NETWORK_ERROR };
  }

  // The body may legitimately be non-JSON (e.g. a proxy or 500 HTML page).
  let data = null;
  let parsedOk = false;
  if (text) {
    try {
      data = JSON.parse(text);
      parsedOk = true;
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && parsedOk && data?.detail === ADMIN_REQUIRED_DETAIL) {
      try {
        adminRequiredHandler?.();
      } catch (error) {
        console.error("Admin handler failed", error);
      }
      return {
        ok: false,
        status: 401,
        data,
        error: ADMIN_REQUIRED_MESSAGE,
      };
    }
    const detail = parsedOk ? describeDetail(data?.detail) : "";
    return {
      ok: false,
      status: response.status,
      data: parsedOk ? data : null,
      error: detail || `Server error (${response.status}). Please try again.`,
    };
  }

  if (text && !parsedOk) {
    return {
      ok: false,
      status: response.status,
      data: null,
      error: "Unexpected response from the server.",
    };
  }

  return { ok: true, status: response.status, data, error: null };
};

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  patch: (path, body = {}) => request("PATCH", path, body),
};
