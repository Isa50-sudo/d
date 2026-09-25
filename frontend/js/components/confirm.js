// Bestätigungsdialog für kritische Aktionen.
// Bewusst NUR per Mausklick/Touch bestätigbar (kein Enter-Default, Fokus liegt auf "Abbrechen").
import { bus } from "../core/bus.js";
import { sendJson } from "../core/ws.js";
import { setFlags } from "../core/state.js";

const queue = [];
let current = null;
let timer = null;

const el = (id) => document.getElementById(id);

function show(request) {
  current = request;
  el("confirm-title").textContent = request.title;
  el("confirm-summary").textContent = request.summary;
  el("confirm-details").textContent = JSON.stringify(request.details, null, 2);
  el("confirm").classList.remove("hidden");
  el("confirm-no").focus();
  const total = Math.max(1, request.expires_at * 1000 - Date.now());
  const bar = el("confirm-bar");
  clearInterval(timer);
  timer = setInterval(() => {
    const left = request.expires_at * 1000 - Date.now();
    bar.style.width = `${Math.max(0, (left / total) * 100)}%`;
    if (left <= 0) close(request.id);
  }, 200);
}

function close(id) {
  const idx = queue.findIndex((r) => r.id === id);
  if (idx >= 0) queue.splice(idx, 1);
  if (current?.id === id) {
    current = null;
    clearInterval(timer);
    el("confirm").classList.add("hidden");
    if (queue.length) show(queue[0]);
  }
  setFlags({ confirmPending: queue.length });
}

function respond(approved) {
  if (!current) return;
  sendJson({ type: "confirm.response", id: current.id, approved });
  bus.emit("confirm:answered", { tool: current.tool, approved });
  close(current.id);
}

export function initConfirm() {
  el("confirm-yes").addEventListener("click", (e) => {
    // Nur echte Benutzerklicks akzeptieren (keine per Skript ausgelösten Events)
    if (e.isTrusted) respond(true);
  });
  el("confirm-no").addEventListener("click", () => respond(false));
  el("confirm").addEventListener("keydown", (e) => {
    if (e.key === "Escape") respond(false);
    if (e.key === "Enter") e.preventDefault();
  });

  bus.on("ws:confirm.request", ({ request }) => {
    if (queue.some((r) => r.id === request.id)) return;
    queue.push(request);
    setFlags({ confirmPending: queue.length });
    bus.emit("confirm:shown", request);
    if (!current) show(request);
  });
  bus.on("ws:confirm.closed", ({ id }) => close(id));
  bus.on("ws:hello", ({ pending_confirmations: pending }) => {
    (pending || []).forEach((request) => bus.emit("ws:confirm.request", { request }));
  });
}
