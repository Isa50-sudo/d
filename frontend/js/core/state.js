// Zustandsautomat für die JARVIS-Anzeige.
// Leitet aus Einzelsignalen (Backend, Gemini, Mikrofon, Wiedergabe, Tools, Bestätigung)
// einen eindeutigen Anzeigezustand ab.
import { bus } from "./bus.js";

export const STATES = {
  OFFLINE: "OFFLINE",
  STANDBY: "STANDBY",
  IDLE: "READY",
  LISTENING: "LISTENING",
  THINKING: "THINKING",
  EXECUTING: "EXECUTING",
  CONFIRM: "WAITING FOR CONFIRMATION",
  SPEAKING: "SPEAKING",
  COMPLETED: "COMPLETED",
  ERROR: "ERROR",
};

const flags = {
  backendOnline: false,
  geminiOk: true,
  listening: false, // Mikrofon streamt zu Gemini
  userSpeaking: false,
  thinking: false,
  speaking: false,
  toolsRunning: 0,
  confirmPending: 0,
  completedUntil: 0,
  errorUntil: 0,
  standby: false, // Wake-Word-Modus: wartet auf "Jarvis"
};

let current = STATES.OFFLINE;
let thinkingTimeout = null;
let hint = "";

export function setFlags(patch) {
  Object.assign(flags, patch);
  if (patch.thinking === true) {
    clearTimeout(thinkingTimeout);
    // Sicherheitsnetz: THINKING nicht endlos anzeigen
    thinkingTimeout = setTimeout(() => setFlags({ thinking: false }), 30000);
  }
  recompute();
}

export function setHint(text) {
  hint = text || "";
  bus.emit("state:hint", hint);
}

export const getFlags = () => ({ ...flags });
export const getState = () => current;

export function flash(kind, ms) {
  if (kind === "completed") flags.completedUntil = Date.now() + (ms ?? 1600);
  if (kind === "error") flags.errorUntil = Date.now() + (ms ?? 3500);
  recompute();
  setTimeout(recompute, (ms ?? 3500) + 30);
}

function recompute() {
  const now = Date.now();
  let next;
  if (!flags.backendOnline) next = STATES.OFFLINE;
  else if (flags.confirmPending > 0) next = STATES.CONFIRM;
  else if (flags.errorUntil > now) next = STATES.ERROR;
  else if (flags.toolsRunning > 0) next = STATES.EXECUTING;
  else if (flags.speaking) next = STATES.SPEAKING;
  else if (flags.completedUntil > now) next = STATES.COMPLETED;
  else if (!flags.geminiOk) next = STATES.OFFLINE;
  else if (flags.thinking) next = STATES.THINKING;
  else if (flags.listening) next = STATES.LISTENING;
  else if (flags.standby) next = STATES.STANDBY;
  else next = STATES.IDLE;

  if (next !== current) {
    const prev = current;
    current = next;
    bus.emit("state", { state: next, prev, flags: { ...flags } });
  }
}
