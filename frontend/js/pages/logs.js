// LOGS: Ereignisprotokoll (live per WebSocket).
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml } from "../core/store.js";

const $ = (id) => document.getElementById(id);
const entries = [];
const categories = new Set();

function row(e) {
  const div = document.createElement("div");
  div.className = `log-row ${e.level}`;
  const ts = new Date(e.ts * 1000);
  div.innerHTML = `<span class="dim">${ts.toLocaleDateString("de-DE")} ${ts.toLocaleTimeString("de-DE")}</span><span class="lv">${escapeHtml(e.level)}</span><span class="cat">${escapeHtml(e.category)}</span><span>${escapeHtml(e.message)}</span>${e.data ? `<span class="data">${escapeHtml(JSON.stringify(e.data))}</span>` : ""}`;
  return div;
}

function visible(e) {
  const lv = $("log-level").value;
  const cat = $("log-cat").value;
  return (!lv || e.level === lv) && (!cat || e.category === cat);
}

function renderAll() {
  const view = $("log-view");
  view.innerHTML = "";
  const frag = document.createDocumentFragment();
  entries.filter(visible).forEach((e) => frag.append(row(e)));
  view.append(frag);
  if ($("log-follow").checked) view.scrollTop = view.scrollHeight;
}

function addCategory(cat) {
  if (categories.has(cat)) return;
  categories.add(cat);
  const opt = document.createElement("option");
  opt.value = cat;
  opt.textContent = cat;
  $("log-cat").append(opt);
}

export function initLogsPage() {
  bus.on("ws:open", async () => {
    try {
      const { entries: list } = await api.get("/api/logs?limit=500");
      entries.length = 0;
      entries.push(...list);
      list.forEach((e) => addCategory(e.category));
      renderAll();
    } catch { /* offline */ }
  });
  bus.on("ws:log", ({ entry }) => {
    if (entries.length && entries[entries.length - 1].id >= entry.id) return;
    entries.push(entry);
    if (entries.length > 1500) entries.shift();
    addCategory(entry.category);
    if (visible(entry)) {
      const view = $("log-view");
      view.append(row(entry));
      if ($("log-follow").checked) view.scrollTop = view.scrollHeight;
    }
  });
  $("log-level").addEventListener("change", renderAll);
  $("log-cat").addEventListener("change", renderAll);
  $("log-clear").addEventListener("click", async () => {
    await api.del("/api/logs").catch(() => {});
    entries.length = 0;
    renderAll();
  });
}
