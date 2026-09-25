// SETTINGS: alle Benutzereinstellungen (gespeichert im Backend unter data/settings.json).
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml, setStore, store } from "../core/store.js";
import { Microphone } from "../audio/mic.js";
import { toast, requestBrowserNotifications } from "../components/toasts.js";

const $ = (id) => document.getElementById(id);
const LANGS = [["de-DE", "Deutsch (Deutschland)"], ["de-AT", "Deutsch (Österreich)"], ["de-CH", "Deutsch (Schweiz)"], ["en-US", "Englisch (USA)"], ["en-GB", "Englisch (UK)"], ["fr-FR", "Französisch"], ["es-ES", "Spanisch"], ["it-IT", "Italienisch"]];
const RISK = { read: "LESEN", write: "ÄNDERN", critical: "KRITISCH" };
const POLICY = { allow: "erlaubt", confirm: "Bestätigung", deny: "verboten" };

let meta = null;
let draft = null;

const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
const set = (obj, path, value) => {
  const keys = path.split(".");
  const last = keys.pop();
  keys.reduce((o, k) => (o[k] ??= {}), obj)[last] = value;
};

function field(path, label, type, opts = {}) {
  const value = get(draft, path);
  const id = `set-${path.replace(/\./g, "-")}`;
  let control;
  if (type === "select") {
    control = `<select id="${id}" data-path="${path}">${opts.options.map(([v, l]) => `<option value="${escapeHtml(v)}" ${String(v) === String(value) ? "selected" : ""}>${escapeHtml(l)}</option>`).join("")}</select>`;
  } else if (type === "check") {
    return `<label class="field check"><span>${label}</span><input type="checkbox" id="${id}" data-path="${path}" data-type="bool" ${value ? "checked" : ""} />${opts.help ? `<small>${opts.help}</small>` : ""}</label>`;
  } else if (type === "number") {
    control = `<input type="number" id="${id}" data-path="${path}" data-type="int" value="${escapeHtml(value)}" min="${opts.min ?? ""}" max="${opts.max ?? ""}" />`;
  } else {
    control = `<input type="text" id="${id}" data-path="${path}" value="${escapeHtml(value ?? "")}" ${opts.list ? `list="${opts.list}"` : ""} />`;
  }
  return `<label class="field"><span>${label}</span>${control}${opts.help ? `<small>${opts.help}</small>` : ""}</label>`;
}

function panel(title, body, cls = "") {
  return `<div class="panel ${cls}"><div class="panel-head"><span>${title}</span></div>${body}</div>`;
}

function toolTable(tools) {
  const rows = tools
    .map((t) => {
      const opts = [["", `Standard`], ["allow", "erlauben"], ["confirm", "Bestätigung"], ["deny", "verbieten"]]
        .map(([v, l]) => `<option value="${v}" ${(t.override || "") === v ? "selected" : ""}>${l}</option>`)
        .join("");
      return `<tr title="${escapeHtml(t.description)}">
        <td>${escapeHtml(t.name)}${t.active ? "" : ' <span class="dim">(inaktiv)</span>'}</td>
        <td class="risk-${t.risk}">${RISK[t.risk]}</td>
        <td><select data-tool="${escapeHtml(t.name)}">${opts}</select></td>
        <td class="small ${t.policy === "deny" ? "danger" : ""}" title="${escapeHtml(t.policy_reason)}">${POLICY[t.policy]}${t.locked ? " 🔒" : ""}</td></tr>`;
    })
    .join("");
  return `<table class="perm-table"><tbody>${rows}</tbody></table>
    <div class="dim small" style="margin-top:6px">🔒 = durch Sicherheitsregel festgelegt. Kritische Aktionen benötigen immer eine Bestätigung; READ_ONLY erlaubt nur lesende Tools.</div>`;
}

function newsSources() {
  return (
    draft.web.news_sources
      .map((s, i) => `<div class="news-src" data-i="${i}">
        <input type="checkbox" data-ns="enabled" ${s.enabled ? "checked" : ""} title="aktiv" />
        <input type="text" data-ns="name" value="${escapeHtml(s.name)}" placeholder="Name" />
        <input type="text" data-ns="url" value="${escapeHtml(s.url)}" placeholder="RSS-URL" />
        <input type="text" data-ns="language" value="${escapeHtml(s.language)}" placeholder="de" />
        <button type="button" class="link danger" data-ns-del="${i}">✕</button></div>`)
      .join("") + `<button type="button" class="btn btn-ghost" id="ns-add">+ QUELLE</button>`
  );
}

async function render() {
  const [data, tools] = await Promise.all([api.get("/api/settings"), api.get("/api/tools")]);
  meta = data;
  draft = structuredClone(data.settings);
  const voices = data.voices.map((v) => [v.name, `${v.name} – ${v.character}${v.recommended ? " ★" : ""}`]);
  const models = data.models.map((m) => m.id);
  let mics = [["", "Standardmikrofon"]];
  try {
    mics = mics.concat((await Microphone.listDevices()).map((d, i) => [d.deviceId, d.label || `Mikrofon ${i + 1}`]));
  } catch { /* keine Berechtigung */ }

  $("settings-form").innerHTML = `
    <datalist id="model-list">${models.map((m) => `<option value="${escapeHtml(m)}">`).join("")}</datalist>
    ${panel("GEMINI", [
      field("ai.live_model", "Live-Modell", "text", { list: "model-list", help: data.models.map((m) => `${m.id}: ${m.label.split("–")[1] || ""}`).join(" · ") }),
      field("ai.google_search", "Google-Suche (Grounding)", "check"),
      field("ai.session_idle_timeout_s", "Session-Timeout (s)", "number", { min: 30, max: 3600, help: "Inaktive Gemini-Verbindung wird danach geschlossen (spart Kosten)." }),
      `<div class="kv"><span>API-Key</span><span>${data.env.gemini_configured ? "hinterlegt (.env)" : "FEHLT – in .env eintragen"}</span></div>`,
    ].join(""))}
    ${panel("STIMME", [
      field("voice.name", "Stimme", "select", { options: voices, help: "★ = tief, ruhig, männlich – empfohlen für JARVIS." }),
      field("voice.language", "Primäre Sprache", "select", { options: LANGS }),
      field("voice.lock_language", "Sprache fixieren", "check", { help: "Aus: JARVIS antwortet automatisch in der Sprache, in der du sprichst." }),
      field("voice.speed", "Tempo", "select", { options: [["slow", "langsam"], ["calm", "ruhig"], ["normal", "normal"], ["fast", "zügig"]] }),
      field("voice.style", "Stil", "text", { help: "Beschreibung der Sprechweise (fließt in die Systemanweisung ein)." }),
    ].join(""))}
    ${panel("GESPRÄCH & WAKE WORD", [
      field("conversation.activation_mode", "Aktivierung", "select", { options: [["wake_word", "Wake Word"], ["continuous", "Mikrofon offen"], ["push_to_talk", "Push-to-talk"]] }),
      field("conversation.wake_word", "Wake Word", "text"),
      field("conversation.follow_up_window_s", "Follow-up-Fenster (s)", "number", { min: 3, max: 120, help: "So lange hört JARVIS nach einer Antwort ohne erneutes Wake Word weiter zu." }),
      field("conversation.vad_sensitivity", "Sprach-Erkennung", "select", { options: [["low", "unempfindlich (laute Umgebung)"], ["normal", "normal"], ["high", "empfindlich"]] }),
      field("conversation.microphone_device_id", "Mikrofon", "select", { options: mics }),
    ].join(""))}
    ${panel("BERECHTIGUNGEN", [
      field("permissions.access_level", "Zugriffsstufe", "select", { options: [["READ_ONLY", "READ_ONLY – nur lesen"], ["LIMITED", "LIMITED – Standard"], ["FULL_ACCESS", "FULL_ACCESS – ohne Rückfragen (außer kritisch)"]] }),
      field("permissions.confirmation_timeout_s", "Bestätigungs-Timeout (s)", "number", { min: 10, max: 600 }),
      `<div class="dim small">Dateizugriff nur in: ${data.env.allowed_paths.map(escapeHtml).join("; ")} (ALLOWED_PATHS in .env)</div>`,
      toolTable(tools.tools),
    ].join(""), "wide")}
    ${panel("BENACHRICHTIGUNGEN", [
      field("notifications.enabled", "Aktiv", "check"),
      field("notifications.speak", "Von JARVIS aussprechen", "check"),
      field("notifications.browser_notifications", "Browser-Benachrichtigungen", "check"),
      field("notifications.disk_threshold_percent", "Warnung Festplatte ab %", "number", { min: 50, max: 99 }),
      field("notifications.memory_threshold_percent", "Warnung RAM ab %", "number", { min: 50, max: 99 }),
      field("notifications.cpu_threshold_percent", "Warnung CPU ab %", "number", { min: 50, max: 100 }),
      field("notifications.email_alerts", "Wichtige E-Mails melden", "check"),
      field("notifications.email_check_minutes", "E-Mail-Prüfintervall (min)", "number", { min: 1, max: 120 }),
    ].join(""))}
    ${panel("MEMORY", [
      field("memory.enabled", "Langzeit-Memory aktiv", "check"),
      field("memory.inject_into_context", "Erinnerungen an Gemini geben", "check"),
      field("memory.allow_model_save", "JARVIS darf speichern", "check", { help: "Nur auf ausdrücklichen Wunsch („Merk dir …“)." }),
      field("memory.confirm_saves", "Speichern bestätigen", "check"),
      field("memory.short_term_turns", "Kurzzeit-Kontext (Runden)", "number", { min: 0, max: 200 }),
    ].join(""))}
    ${panel("WEBZUGRIFF & NEWS", [
      field("web.enabled", "Webzugriff aktiv", "check"),
      field("web.weather_default_location", "Standardort Wetter", "text"),
      `<div class="dim small">Nachrichtenquellen (RSS):</div><div id="news-sources">${newsSources()}</div>`,
    ].join(""))}
    ${panel("LOGGING", [
      field("logging.level", "Log-Level", "select", { options: [["DEBUG", "DEBUG"], ["INFO", "INFO"], ["WARNING", "WARNING"], ["ERROR", "ERROR"]] }),
      field("logging.log_tool_arguments", "Tool-Parameter protokollieren", "check", { help: "Aus Datenschutzgründen standardmäßig aus." }),
      field("logging.log_transcripts", "Gespräche protokollieren", "check", { help: "Aus Datenschutzgründen standardmäßig aus." }),
    ].join(""))}
    ${panel("DARSTELLUNG", [
      field("ui.theme", "Theme", "select", { options: [["arc", "Arc Reactor (Cyan)"], ["amber", "Amber"], ["crimson", "Crimson"], ["mono", "Monochrom"]] }),
      field("ui.reduced_motion", "Reduzierte Animationen", "check"),
      field("ui.show_subtitles", "Untertitel anzeigen", "check"),
    ].join(""))}
    <div class="settings-actions">
      <button type="button" class="btn btn-ghost" id="settings-reset">STANDARD</button>
      <button type="submit" class="btn btn-primary">SPEICHERN</button>
    </div>`;
}

function collect() {
  const form = $("settings-form");
  form.querySelectorAll("[data-path]").forEach((el) => {
    let value = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.type === "int") value = parseInt(value, 10);
    if (el.dataset.path === "conversation.microphone_device_id" && !value) value = null;
    set(draft, el.dataset.path, value);
  });
  const overrides = {};
  form.querySelectorAll("select[data-tool]").forEach((el) => el.value && (overrides[el.dataset.tool] = el.value));
  draft.permissions.tool_overrides = overrides;
  draft.web.news_sources = [...form.querySelectorAll(".news-src")].map((row) => ({
    enabled: row.querySelector('[data-ns="enabled"]').checked,
    name: row.querySelector('[data-ns="name"]').value.trim(),
    url: row.querySelector('[data-ns="url"]').value.trim(),
    language: row.querySelector('[data-ns="language"]').value.trim() || "de",
  })).filter((s) => s.name && s.url);
  return draft;
}

export function applyUiSettings(settings) {
  if (!settings) return;
  document.documentElement.dataset.theme = settings.ui.theme;
  document.documentElement.classList.toggle("reduced-motion", settings.ui.reduced_motion);
}

export function initSettingsPage() {
  bus.on("page:enter:settings", () => render().catch((err) => toast("Settings", err.message, "error")));
  const form = $("settings-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const prevMic = store.settings?.conversation?.microphone_device_id;
    try {
      const { settings } = await api.put("/api/settings", collect());
      setStore({ settings });
      applyUiSettings(settings);
      if (settings.notifications.browser_notifications) requestBrowserNotifications();
      bus.emit("settings:saved", { settings, micChanged: prevMic !== settings.conversation.microphone_device_id });
      toast("Settings", "Einstellungen gespeichert.");
      render();
    } catch (err) {
      toast("Settings", err.message, "error", 9000);
    }
  });
  form.addEventListener("click", async (e) => {
    if (e.target.id === "settings-reset") {
      if (!confirm("Alle Einstellungen auf Standardwerte (aus .env) zurücksetzen?")) return;
      const { settings } = await api.post("/api/settings/reset");
      setStore({ settings });
      applyUiSettings(settings);
      render();
    } else if (e.target.id === "ns-add") {
      collect();
      draft.web.news_sources.push({ name: "", url: "", language: "de", enabled: true });
      $("news-sources").innerHTML = newsSources();
    } else if (e.target.dataset.nsDel !== undefined) {
      collect();
      draft.web.news_sources.splice(Number(e.target.dataset.nsDel), 1);
      $("news-sources").innerHTML = newsSources();
    }
  });
  bus.on("ws:settings.changed", ({ settings }) => {
    setStore({ settings });
    applyUiSettings(settings);
  });
}
