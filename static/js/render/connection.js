import { els } from "../dom.js";

const label = ({ status, clients }) => {
  if (status === "live") {
    return clients > 0 ? `Live · ${clients} ${clients === 1 ? "device" : "devices"}` : "Live";
  }
  return status === "reconnecting" ? "Reconnecting…" : "Connecting…";
};

/** Header pill: green when live, amber while (re)connecting. */
export const renderConnection = (_state, _ui, _draft, connection) => {
  const { connPill, connLabel } = els;
  if (!connPill || !connLabel || !connection) return;
  const text = label(connection);
  if (connLabel.textContent !== text) connLabel.textContent = text;
  if (connPill.dataset.status !== connection.status) {
    connPill.dataset.status = connection.status;
  }
};
