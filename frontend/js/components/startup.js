// Startsequenz mit ECHTEN Prüfungen (Backend /api/startup-check + Mikrofon-Berechtigung).
import { api } from "../core/api.js";
import { escapeHtml } from "../core/store.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ORDER = ["system", "microphone", "ollama", "stt", "voice", "tools", "network", "memory", "email"];

function line(list, label) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="lbl">${label}</span><span class="dots">${".".repeat(60)}</span><span class="st WAIT">…</span>`;
  list.appendChild(li);
  return li;
}

function settle(li, status, detail) {
  const st = li.querySelector(".st");
  st.className = `st ${status}`;
  st.textContent = status;
  if (detail) {
    const d = document.createElement("span");
    d.className = "det";
    d.textContent = detail;
    li.after(d);
  }
}

async function checkMicrophone() {
  if (!navigator.mediaDevices?.getUserMedia) return { status: "FAIL", detail: "Browser unterstützt keinen Mikrofonzugriff" };
  try {
    const perm = await navigator.permissions?.query({ name: "microphone" });
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
    if (!devices.length) return { status: "FAIL", detail: "Kein Mikrofon gefunden" };
    if (perm?.state === "denied") return { status: "FAIL", detail: "Zugriff im Browser verweigert" };
    if (perm?.state === "prompt") return { status: "OK", detail: `${devices.length} Gerät(e) · Freigabe wird beim Aktivieren angefragt` };
    return { status: "OK", detail: `${devices.length} Gerät(e) gefunden` };
  } catch {
    return { status: "OK", detail: "Freigabe wird beim Aktivieren angefragt" };
  }
}

/** Führt die Startsequenz aus. Resolved, sobald der Benutzer "Aktivieren" klickt. */
export async function runStartup() {
  const root = document.getElementById("startup");
  const list = document.getElementById("startup-lines");
  const final = document.getElementById("startup-final");
  const setup = document.getElementById("startup-setup");
  const quick = sessionStorage.getItem("jarvis.booted") === "1";
  const pace = quick ? 40 : 170;

  let backend;
  for (let attempt = 0; attempt < 30; attempt++) {
    try {
      backend = await api.get("/api/startup-check");
      break;
    } catch {
      if (attempt === 0) line(list, "BACKEND").classList.add("waiting");
      await sleep(1000);
    }
  }
  list.innerHTML = "";
  if (!backend) {
    const li = line(list, "BACKEND");
    settle(li, "FAIL", "Das lokale Backend antwortet nicht. Läuft start.bat / start.sh?");
    return new Promise(() => {});
  }

  const byKey = Object.fromEntries(backend.checks.map((c) => [c.key, c]));
  byKey.microphone = { label: "MICROPHONE", ...(await checkMicrophone()) };

  for (const key of ORDER) {
    const check = byKey[key];
    if (!check) continue;
    const li = line(list, check.label || key.toUpperCase());
    await sleep(pace);
    settle(li, check.status, check.detail);
  }

  const model = escapeHtml(byKey.ollama?.detail?.split(" · ")[0] || "qwen3:8b");
  const hints = [];
  if (!backend.ai_ready) {
    hints.push(`<b>Ollama ist nicht bereit.</b> ${escapeHtml(backend.ai_message || "")}<br />
      1. Ollama installieren und starten: <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a><br />
      2. Modell laden (Terminal): <code>ollama pull ${backend.ai_message?.includes("pull") ? escapeHtml(backend.ai_message.split("pull ").pop()) : model}</code><br />
      3. Danach hier einfach auf „JARVIS AKTIVIEREN“ klicken – ein Neustart ist nicht nötig.`);
  }
  if (!backend.voice_ready) {
    hints.push(`<b>Stimme fehlt.</b> Einmalig herunterladen: <code>python scripts/download_models.py</code> (danach JARVIS neu starten). Bis dahin antwortet JARVIS als Text.`);
  }
  if (hints.length) {
    setup.classList.remove("hidden");
    setup.innerHTML = hints.join("<br /><br />") + `<br /><span class="dim">JARVIS läuft vollständig lokal – es werden keine API-Keys benötigt.</span>`;
  }

  const failed = backend.checks.filter((c) => c.status === "FAIL").map((c) => c.label);
  final.classList.remove("hidden");
  final.querySelector(".startup-ready").textContent = failed.length ? `READY WITH WARNINGS (${escapeHtml(failed.join(", "))})` : "ALL SYSTEMS READY";
  if (failed.length) final.querySelector(".startup-ready").style.color = "var(--warn)";
  const button = document.getElementById("startup-engage");
  button.focus();

  return new Promise((resolve) => {
    const go = () => {
      sessionStorage.setItem("jarvis.booted", "1");
      root.style.transition = "opacity .5s";
      root.style.opacity = "0";
      setTimeout(() => root.remove(), 520);
      resolve(backend);
    };
    button.addEventListener("click", go, { once: true });
  });
}
