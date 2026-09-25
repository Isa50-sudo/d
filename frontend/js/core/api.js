// REST-Client für das lokale Backend (gleiche Origin – kein API-Key im Browser).
import { store } from "./store.js";

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (store.clientId) headers["X-Jarvis-Client"] = store.clientId;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* leerer Body */ }
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ") : `Fehler ${res.status}`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
};
