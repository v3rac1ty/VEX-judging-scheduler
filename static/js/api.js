// Thin fetch wrapper. Never throws: always resolves to {ok, status, data, error}.

const NETWORK_ERROR =
  "Cannot reach the server. Check your network connection and try again.";

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
