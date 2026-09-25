// MEMORY: Langzeit-Erinnerungen ansehen/hinzufügen/löschen + Kurzzeit-Kontext.
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml, fmtDateTime } from "../core/store.js";
import { toast } from "../components/toasts.js";

const $ = (id) => document.getElementById(id);
const CAT = { fact: "FAKT", preference: "PRÄFERENZ", event: "EREIGNIS", note: "NOTIZ" };
let searchTimer = null;

async function load() {
  const q = $("mem-search").value.trim();
  try {
    const data = await api.get(`/api/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    $("mem-list").innerHTML = data.memories.length
      ? data.memories
          .map((m) => `<li><span class="tag">${CAT[m.category] || m.category}</span><div>${escapeHtml(m.content)}<time>${fmtDateTime(m.created_at)} · ${m.source === "jarvis" ? "von JARVIS gespeichert" : "manuell"}</time></div><button class="link danger" data-id="${m.id}" title="Löschen">✕</button></li>`)
          .join("")
      : `<li class="dim">Keine Erinnerungen gespeichert. JARVIS speichert nur, wenn du ihn ausdrücklich darum bittest („Merk dir …“).</li>`;
    $("conv-list").innerHTML = data.short_term.length
      ? data.short_term.map((t) => `<li class="${t.role}"><b>${t.role === "user" ? "DU" : "JARVIS"}</b>${escapeHtml(t.text)}</li>`).join("")
      : `<li class="dim">Noch kein Gesprächsverlauf.</li>`;
  } catch (err) {
    toast("Memory", err.message, "error");
  }
}

export function initMemoryPage() {
  bus.on("page:enter:memory", load);
  bus.on("ws:memory.changed", () => document.querySelector(".page.active[data-page='memory']") && load());
  bus.on("conversation:turn", () => document.querySelector(".page.active[data-page='memory']") && load());
  $("mem-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 250);
  });
  $("mem-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = $("mem-text").value.trim();
    if (content.length < 2) return;
    try {
      await api.post("/api/memories", { content, category: $("mem-cat").value });
      $("mem-text").value = "";
      load();
    } catch (err) { toast("Memory", err.message, "error"); }
  });
  $("mem-list").addEventListener("click", async (e) => {
    const id = e.target.dataset.id;
    if (!id) return;
    await api.del(`/api/memories/${id}`).catch((err) => toast("Memory", err.message, "error"));
    load();
  });
  $("mem-clear").addEventListener("click", async () => {
    if (!confirm("Wirklich ALLE Langzeit-Erinnerungen löschen?")) return;
    await api.del("/api/memories");
    load();
  });
  $("conv-clear").addEventListener("click", async () => {
    await api.del("/api/conversation");
    load();
  });
}
