// JARVIS-Hauptseite: Kern, Status, Untertitel, Aktivität, Quellen, News, Mini-Systemwerte, Chat.
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml, fmtDateTime, store } from "../core/store.js";
import { getState } from "../core/state.js";
import { conversation } from "../audio/conversation.js";
import { CoreVisualizer } from "../components/core-viz.js";

const $ = (id) => document.getElementById(id);
const TOOL_LABELS = {
  open_url: "Webseite öffnen", open_web_search: "Websuche öffnen", open_application: "Programm öffnen",
  close_application: "Programm beenden", install_application: "Programm installieren", get_weather: "Wetter abrufen",
  get_news: "Nachrichten abrufen", wikipedia_lookup: "Wikipedia", read_webpage: "Webseite lesen",
  show_location_on_globe: "Globus", search_files: "Dateisuche", read_file: "Datei lesen", create_file: "Datei anlegen",
  edit_file: "Datei bearbeiten", delete_file: "Datei löschen", remember: "Erinnerung speichern",
  create_reminder: "Erinnerung stellen", email_send: "E-Mail senden", email_list_recent: "E-Mails abrufen",
};
const label = (tool) => TOOL_LABELS[tool] || tool;

let subtitleTimer = null;

function analyserFor(state) {
  if (state === "SPEAKING") return conversation.playerAnalyser;
  if (state === "LISTENING" || state === "STANDBY" || state === "READY") return conversation.micAnalyser;
  return null;
}

function feed(text, kind = "") {
  const list = $("activity-feed");
  const li = document.createElement("li");
  const now = new Date();
  li.innerHTML = `<time>${now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}</time><span class="${kind}"></span>`;
  li.querySelector("span").textContent = text;
  list.prepend(li);
  while (list.children.length > 60) list.lastChild.remove();
  $("activity-count").textContent = `${list.children.length}`;
}

function setSubtitle(role, text, final) {
  const node = $(role === "user" ? "sub-user" : "sub-jarvis");
  node.classList.remove("fade");
  node.textContent = role === "user" ? (text ? `„${text}“` : "") : text;
  if (role === "jarvis") node.scrollTop = node.scrollHeight;
  clearTimeout(subtitleTimer);
  if (final) {
    subtitleTimer = setTimeout(() => {
      $("sub-user").classList.add("fade");
      $("sub-jarvis").classList.add("fade");
    }, 9000);
  }
}

function chatMessage(role, text) {
  const log = $("chat-log");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  log.append(div);
  while (log.children.length > 40) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
}

function renderSources(sources) {
  const list = $("source-list");
  list.innerHTML = "";
  for (const s of sources.slice(0, 6)) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = s.uri;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = s.title || s.uri;
    const small = document.createElement("small");
    const when = s.published ? new Date(s.published).toLocaleString("de-DE") : s.retrieved_at ? `abgerufen ${new Date(s.retrieved_at).toLocaleTimeString("de-DE")}` : "";
    small.textContent = [s.publisher, when].filter(Boolean).join(" · ");
    li.append(a, small);
    list.append(li);
  }
}

async function loadNews() {
  const list = $("news-list");
  try {
    const data = await api.get("/api/news?limit=6");
    if (data.disabled) { list.innerHTML = `<li class="dim">Webzugriff deaktiviert</li>`; return; }
    if (!data.items?.length) { list.innerHTML = `<li class="dim">${escapeHtml(data.error || "Keine Nachrichten verfügbar")}</li>`; return; }
    list.innerHTML = data.items
      .map((i) => `<li><a href="${escapeHtml(i.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(i.title)}</a><small>${escapeHtml(i.source)}${i.published ? " · " + escapeHtml(new Date(i.published).toLocaleString("de-DE", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" })) : ""}</small></li>`)
      .join("");
  } catch {
    list.innerHTML = `<li class="dim">Nachrichten nicht erreichbar</li>`;
  }
}

async function loadReminders() {
  try {
    const { reminders } = await api.get("/api/reminders");
    const list = $("reminder-list");
    list.innerHTML = reminders.length
      ? reminders.map((r) => `<li><span class="mono dim">${escapeHtml(fmtDateTime(r.due_at))}</span><br />${escapeHtml(r.text)}</li>`).join("")
      : `<li class="dim">Keine offenen Erinnerungen</li>`;
  } catch { /* Backend offline */ }
}

function meter(id, percent, text) {
  const m = $(id);
  if (!m) return;
  m.querySelector("b").style.width = `${Math.max(0, Math.min(100, percent ?? 0))}%`;
  m.querySelector(".val").textContent = text;
  m.classList.toggle("hot", (percent ?? 0) >= 90);
}

function renderStats(s) {
  meter("m-cpu", s.cpu.percent, `${s.cpu.percent.toFixed(0)}%`);
  meter("m-ram", s.memory.percent, `${s.memory.percent.toFixed(0)}%`);
  const gpu = s.gpu?.[0];
  meter("m-gpu", gpu?.utilization_percent ?? 0, gpu ? `${gpu.utilization_percent ?? "–"}%` : "n. v.");
  const disk = [...(s.disks || [])].sort((a, b) => b.percent - a.percent)[0];
  meter("m-disk", disk?.percent ?? 0, disk ? `${disk.percent.toFixed(0)}%` : "–");
  const rate = (k) => (k > 1024 ? `${(k / 1024).toFixed(1)} MB/s` : `${k.toFixed(0)} KB/s`);
  $("m-down").textContent = rate(s.network.down_kbps);
  $("m-up").textContent = rate(s.network.up_kbps);
}

export function initJarvisPage() {
  const reduced = () => document.documentElement.classList.contains("reduced-motion");
  const viz = new CoreVisualizer($("core-canvas"), { getAnalyser: analyserFor, reducedMotion: reduced });
  const mini = new CoreVisualizer(document.querySelector("#mini-core canvas"), { getAnalyser: analyserFor, mini: true, reducedMotion: reduced });
  viz.start();
  mini.start();

  bus.on("state", ({ state }) => {
    viz.setState(state);
    mini.setState(state);
    const el = $("core-state");
    el.textContent = state === "WAITING FOR CONFIRMATION" ? state : `${state}${["LISTENING", "THINKING", "EXECUTING", "SPEAKING"].includes(state) ? "…" : ""}`;
    el.dataset.state = state;
    document.querySelector("#mini-core .mini-state").textContent = state.split(" ")[0];
    document.title = state === "READY" || state === "STANDBY" ? "JARVIS" : `JARVIS · ${state}`;
  });
  bus.on("state:hint", (hint) => ($("core-hint").textContent = hint));

  // Kern: Tippen = aktivieren/deaktivieren, Gedrückthalten = Push-to-talk
  const hit = $("core-hit");
  hit.addEventListener("pointerdown", () => conversation.pttStart());
  hit.addEventListener("pointerup", () => conversation.pttEnd());
  hit.addEventListener("pointerleave", () => conversation.pttEnd());
  hit.addEventListener("click", () => conversation.mode !== "push_to_talk" && conversation.toggleFromCore());

  // Modus + Stummschalten
  $("mode-switch").addEventListener("click", (e) => {
    const mode = e.target.closest("button")?.dataset.mode;
    if (mode) conversation.setMode(mode);
  });
  bus.on("voice:mode", (mode) => {
    document.querySelectorAll("#mode-switch button").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === mode);
      b.setAttribute("aria-checked", String(b.dataset.mode === mode));
    });
  });
  $("btn-mute").addEventListener("click", () => {
    conversation.setMuted(!conversation.muted);
    $("btn-mute").classList.toggle("muted", conversation.muted);
    $("btn-mute").textContent = conversation.muted ? "MIC AN" : "MIC AUS";
  });

  // Chat (Fallback)
  const chat = $("chat");
  $("btn-chat-toggle").addEventListener("click", () => {
    chat.classList.toggle("open");
    if (chat.classList.contains("open")) $("chat-text").focus();
  });
  chat.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("chat-text");
    const text = input.value.trim();
    if (!text) return;
    if (conversation.sendText(text)) {
      chatMessage("user", text);
      setSubtitle("user", text, false);
      input.value = "";
    }
  });

  // Transkripte
  bus.on("ws:transcript", ({ role, text, final, interrupted }) => {
    if (store.settings?.ui?.show_subtitles !== false) setSubtitle(role, text, final);
    if (final) {
      chatMessage(role, text + (interrupted ? " …" : ""));
      bus.emit("conversation:turn", { role, text });
    }
    if (role === "user" && !final) conversation.onUserActivity(true);
  });

  // Tools / Quellen / Suche
  bus.on("ws:tool.start", ({ tool, summary }) => feed(`▸ ${label(tool)}: ${summary}`, "t-run"));
  bus.on("ws:tool.end", ({ tool, ok, message, ms }) => feed(ok ? `✓ ${label(tool)} (${ms} ms)` : `✕ ${label(tool)}: ${message || "fehlgeschlagen"}`, ok ? "t-ok" : "t-err"));
  bus.on("ws:tool.denied", ({ tool, reason }) => feed(`⊘ ${label(tool)} verweigert – ${reason}`, "t-warn"));
  bus.on("confirm:shown", (r) => feed(`? Bestätigung angefordert: ${r.summary}`, "t-warn"));
  bus.on("confirm:answered", ({ tool, approved }) => feed(approved ? `✓ ${label(tool)} bestätigt` : `✕ ${label(tool)} abgelehnt`, approved ? "t-ok" : "t-warn"));
  bus.on("ws:search", ({ queries }) => feed(`⌕ Google-Suche: ${queries.join(" · ")}`, "t-run"));
  bus.on("ws:sources", ({ sources }) => renderSources(sources));
  bus.on("ws:notification", ({ title, message }) => feed(`◆ ${title}: ${message}`, "t-warn"));
  bus.on("ws:error", ({ message }) => feed(`✕ ${message}`, "t-err"));
  bus.on("voice:activated", ({ source }) => feed(source === "wake" ? "◉ Wake Word erkannt" : "◉ Zuhören aktiviert"));

  // Uhr
  const tick = () => {
    const now = new Date();
    $("clock").textContent = now.toLocaleTimeString("de-DE");
    $("big-time").textContent = now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    $("big-date").textContent = now.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  };
  tick();
  setInterval(tick, 1000);

  bus.on("ws:system.stats", ({ stats }) => renderStats(stats));
  bus.on("ws:reminders.changed", loadReminders);
  bus.on("ws:open", () => { loadReminders(); loadNews(); });
  $("news-refresh").addEventListener("click", loadNews);
  setInterval(loadNews, 10 * 60 * 1000);
  api.get("/api/system/info").then((i) => ($("mini-os").textContent = i.os)).catch(() => {});

  // Tastatur: Leertaste = PTT/aktivieren, M = stumm, T = Chat
  const typing = (e) => ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
  window.addEventListener("keydown", (e) => {
    if (typing(e) || e.repeat || !document.querySelector(".page.active[data-page='jarvis']")) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (conversation.mode === "push_to_talk") conversation.pttStart();
      else conversation.toggleFromCore();
    } else if (e.key === "m" || e.key === "M") $("btn-mute").click();
    else if (e.key === "t" || e.key === "T") { e.preventDefault(); $("btn-chat-toggle").click(); }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && !typing(e)) conversation.pttEnd();
  });

  return { getState };
}
