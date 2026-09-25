// JARVIS – Frontend-Bootstrap: verbindet WebSocket, Zustandsautomat, Audio und Seiten.
import { api } from "./core/api.js";
import { bus } from "./core/bus.js";
import { setStore, store } from "./core/store.js";
import { flash, getFlags, setFlags } from "./core/state.js";
import { connect } from "./core/ws.js";
import { conversation } from "./audio/conversation.js";
import { initConfirm } from "./components/confirm.js";
import { initNav } from "./components/nav.js";
import { runStartup } from "./components/startup.js";
import { browserNotify, toast } from "./components/toasts.js";
import { initJarvisPage } from "./pages/jarvis.js";
import { initWorldPage } from "./pages/world.js";
import { initSystemPage } from "./pages/system.js";
import { initMemoryPage } from "./pages/memory.js";
import { initEmailPage } from "./pages/email.js";
import { initLogsPage } from "./pages/logs.js";
import { applyUiSettings, initSettingsPage } from "./pages/settings.js";

const $ = (id) => document.getElementById(id);

function indicator(id, cls, title) {
  const el = $(id);
  el.className = `ind ${cls}`;
  el.title = title;
}

function renderIndicators() {
  indicator("ind-backend", store.backend === "online" ? "ok" : "err", `Backend: ${store.backend}`);
  const g = store.ai;
  const gCls = g.state === "error" || g.state === "missing_model" ? "err" : g.state === "connected" ? "live" : g.state === "connecting" ? "warn" : "ok";
  indicator("ind-ai", gCls, g.message || `Ollama: ${g.state}`);
  const mCls = { live: "live", ready: "ok", muted: "warn", error: "err" }[store.mic] || "";
  indicator("ind-mic", mCls, `Mikrofon: ${store.mic}`);
}

function wireEvents() {
  bus.on("store", renderIndicators);

  bus.on("ws:open", () => setFlags({ backendOnline: true }));
  bus.on("ws:close", () => {
    setFlags({ backendOnline: false, toolsRunning: 0, thinking: false });
    conversation.player.clear();
  });

  bus.on("ws:hello", (msg) => {
    setStore({
      clientId: msg.client_id,
      settings: msg.settings,
      audio: msg.audio,
      ai: { ...store.ai, configured: true, state: msg.ai.state, message: msg.ai.message, model: msg.ai.model },
    });
    applyUiSettings(msg.settings);
    setFlags({ aiOk: !["error", "missing_model"].includes(msg.ai.state) });
    // Nach Wiederverbindung die Sprachsitzung wiederherstellen
    if (conversation.engaged) conversation.setMode(conversation.mode, { persist: false });
  });

  bus.on("ws:ai.status", ({ state, message, model }) => {
    setStore({ ai: { ...store.ai, state, message, model } });
    setFlags({ aiOk: state !== "error" });
    if (state === "error") flash("error");
    conversation.onAiStatus(state);
  });

  bus.on("ws:model.speaking", () => setFlags({ thinking: false }));
  bus.on("ws:voice.activity", ({ activity }) => {
    setFlags({ userSpeaking: activity === "start" });
    conversation.onUserActivity(activity === "start");
  });
  bus.on("ws:turn.complete", () => conversation.onTurnComplete());
  bus.on("ws:interrupted", () => {
    conversation.onInterrupted();
    setFlags({ thinking: false });
  });

  bus.on("ws:tool.start", () => setFlags({ toolsRunning: getFlags().toolsRunning + 1, thinking: false }));
  bus.on("ws:tool.end", ({ ok }) => {
    setFlags({ toolsRunning: Math.max(0, getFlags().toolsRunning - 1) });
    if (ok) flash("completed", 1600);
    else flash("error", 2500);
  });

  bus.on("ws:error", ({ message }) => {
    toast("Hinweis", message, "error", 9000);
    setFlags({ thinking: false });
    flash("error");
  });
  bus.on("ws:notification", ({ title, message, level, browser }) => {
    toast(title, message, level === "warning" ? "warning" : level === "reminder" ? "reminder" : "info", 10000);
    if (browser) browserNotify(title, message);
  });
  bus.on("ws:system.stats", ({ stats }) => (store.lastStats = stats));

  bus.on("voice:error", ({ message }) => toast("Mikrofon", message, "error", 9000));
  bus.on("voice:notice", ({ message }) => toast("Sprache", message, "warning", 9000));
  bus.on("voice:mode-persist", (mode) => {
    api.put("/api/settings", { conversation: { activation_mode: mode } }).catch(() => {});
  });
  bus.on("settings:saved", ({ settings, micChanged }) => {
    if (micChanged) conversation.changeDevice(settings.conversation.microphone_device_id);
    if (settings.conversation.activation_mode !== conversation.mode) conversation.setMode(settings.conversation.activation_mode, { persist: false });
  });
}

async function main() {
  initNav();
  initConfirm();
  initJarvisPage();
  initWorldPage();
  initSystemPage();
  initMemoryPage();
  initEmailPage();
  initLogsPage();
  initSettingsPage();
  wireEvents();
  renderIndicators();
  connect();

  await runStartup();
  $("app").classList.remove("hidden");
  try {
    await conversation.engage();
  } catch (err) {
    toast("Audio", `Audio konnte nicht gestartet werden: ${err.message}`, "error", 10000);
  }
}

main();
