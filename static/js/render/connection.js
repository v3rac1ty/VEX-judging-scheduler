import { els } from "../dom.js";

const label = ({ status, clients }) => {
  if (status === "live") {
    return clients > 0 ? `Connected, ${clients} ${clients === 1 ? "device" : "devices"}` : "Connected";
  }
  return status === "reconnecting" ? "Reconnecting…" : "Connecting…";
};

/** Header status: green dot when connected, amber while (re)connecting. */
export const renderConnection = (_state, _ui, _draft, connection) => {
  const { connPill, connLabel } = els;
  if (!connPill || !connLabel || !connection) return;
  const text = label(connection);
  if (connLabel.textContent !== text) connLabel.textContent = text;
  if (connPill.dataset.status !== connection.status) {
    connPill.dataset.status = connection.status;
  }
};
