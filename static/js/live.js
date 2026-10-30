// Server-Sent Events client: pushes `state`, `draft` and `presence` events into
// the store. EventSource reconnects by itself; on every (re)connect the server
// re-sends the full state and draft, so no extra fetch is needed.
import { setState, applyDraftFull, applyDraftPatch, setConnection } from "./store.js";
import { refreshAdminStatus } from "./admin.js";

const RECONNECT_MS = 2000;

const parse = (event) => {
  try {
    return JSON.parse(event.data);
  } catch {
    return null;
  }
};

export const startLive = () => {
  let source = null;
  let retryTimer = null;
  let connectedBefore = false;

  const open = () => {
    source = new EventSource("/api/events");

    source.onopen = () => {
      setConnection({ status: "live" });
      // The server may have restarted (or the PIN changed) while we were away.
      if (connectedBefore) refreshAdminStatus();
      connectedBefore = true;
    };

    source.onerror = () => {
      setConnection({ status: "reconnecting" });
      // A CLOSED source (e.g. non-200 reply) will not retry on its own.
      if (source.readyState === 2 /* CLOSED */) {
        source.close();
        clearTimeout(retryTimer);
        retryTimer = setTimeout(open, RECONNECT_MS);
      }
    };

    source.addEventListener("state", (event) => {
      const data = parse(event);
      if (data) setState(data);
    });

    source.addEventListener("draft", (event) => {
      const data = parse(event);
      if (!data) return;
      if (data.full) {
        applyDraftFull({ ...data.changes, revision: data.revision });
      } else {
        applyDraftPatch(data);
      }
    });

    source.addEventListener("presence", (event) => {
      const data = parse(event);
      if (data && Number.isFinite(Number(data.clients))) {
        setConnection({ clients: Number(data.clients) });
      }
    });
  };

  open();
  return () => {
    clearTimeout(retryTimer);
    source?.close();
  };
};
