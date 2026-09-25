// Globaler, beobachtbarer Anwendungszustand.
import { bus } from "./bus.js";

export const store = {
  clientId: null,
  settings: null,
  audio: { input_sample_rate: 16000, output_sample_rate: 24000 },
  backend: "connecting", // connecting | online | offline
  gemini: { configured: false, state: "unknown", message: null, model: "" },
  mic: "off", // off | ready | live | muted | error
  lastStats: null,
};

export function setStore(patch) {
  Object.assign(store, patch);
  bus.emit("store", store);
}

export const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
export const fmtDateTime = (ts) =>
  new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
