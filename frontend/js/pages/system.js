// SYSTEM: Live-Monitoring mit echten Werten vom Backend (psutil / nvidia-smi).
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml, store } from "../core/store.js";

const $ = (id) => document.getElementById(id);
const HISTORY = 90;
const history = { cpu: [], ram: [], down: [], up: [] };

function push(arr, v) {
  arr.push(v);
  if (arr.length > HISTORY) arr.shift();
}

function spark(canvas, series, max = 100) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const styles = getComputedStyle(document.documentElement);
  const rgb = styles.getPropertyValue("--accent-rgb").trim();
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = `rgba(${rgb},.08)`;
  for (let y = 0; y <= 4; y++) {
    ctx.beginPath();
    ctx.moveTo(0, (h / 4) * y + 0.5);
    ctx.lineTo(w, (h / 4) * y + 0.5);
    ctx.stroke();
  }
  series.forEach(({ data, alpha }) => {
    if (data.length < 2) return;
    const top = Math.max(max, ...data) || 1;
    const step = w / (HISTORY - 1);
    const off = (HISTORY - data.length) * step;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = off + i * step;
      const y = h - (v / top) * (h - 4) - 2;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = `rgba(${rgb},${alpha})`;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.lineTo(off + (data.length - 1) * step, h);
    ctx.lineTo(off, h);
    ctx.closePath();
    ctx.fillStyle = `rgba(${rgb},${alpha * 0.12})`;
    ctx.fill();
  });
}

const rate = (k) => (k > 1024 ? `${(k / 1024).toFixed(2)} MB/s` : `${k.toFixed(1)} KB/s`);
const dur = (s) => {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${d ? d + " d " : ""}${h} h ${m} min`;
};

function render(s) {
  push(history.cpu, s.cpu.percent);
  push(history.ram, s.memory.percent);
  push(history.down, s.network.down_kbps);
  push(history.up, s.network.up_kbps);
  if (!document.querySelector(".page.active[data-page='system']")) return;

  $("sys-cpu-val").textContent = s.cpu.percent.toFixed(0);
  $("sys-cpu-info").textContent = [s.cpu.freq_mhz ? `${s.cpu.freq_mhz} MHz` : "", s.cpu.load_avg ? `Load ${s.cpu.load_avg.join(" / ")}` : ""].filter(Boolean).join(" · ");
  const cores = $("sys-cores");
  if (cores.children.length !== s.cpu.per_core.length) cores.innerHTML = s.cpu.per_core.map(() => "<div><b></b></div>").join("");
  s.cpu.per_core.forEach((v, i) => (cores.children[i].firstChild.style.height = `${v}%`));
  spark($("spark-cpu"), [{ data: history.cpu, alpha: 0.9 }]);

  $("sys-ram-val").textContent = s.memory.percent.toFixed(0);
  $("sys-ram-used").textContent = `${s.memory.used_gb} / ${s.memory.total_gb} GB`;
  $("sys-swap").textContent = `${s.memory.swap_percent}%`;
  spark($("spark-ram"), [{ data: history.ram, alpha: 0.9 }]);

  $("sys-down").textContent = rate(s.network.down_kbps);
  $("sys-up").textContent = rate(s.network.up_kbps);
  $("sys-ifaces").textContent = s.network.interfaces.map((i) => `${i.name}: ${i.ipv4.join(", ")}`).join(" · ");
  spark($("spark-net"), [{ data: history.down, alpha: 0.9 }, { data: history.up, alpha: 0.4 }], 10);

  $("sys-gpu").innerHTML = s.gpu?.length
    ? s.gpu.map((g) => `<div class="kv"><span>${escapeHtml(g.name)}</span><span>${g.utilization_percent ?? "–"}%</span></div>
        <div class="kv"><span>VRAM</span><span>${g.vram_used_mb != null ? (g.vram_used_mb / 1024).toFixed(1) : "–"} / ${g.vram_total_mb != null ? (g.vram_total_mb / 1024).toFixed(1) : "–"} GB</span></div>
        <div class="kv"><span>Temperatur</span><span>${g.temperature_c ?? "–"} °C</span></div>
        <div class="kv"><span>Leistung</span><span>${g.power_w ?? "–"} W</span></div>`).join("")
    : `<span class="dim">Nicht verfügbar – GPU-Werte werden derzeit über nvidia-smi (NVIDIA) gelesen.</span>`;

  $("sys-disks").innerHTML = s.disks
    .map((d) => `<div class="disk"><div class="kv"><span>${escapeHtml(d.mount)}</span><span>${d.used_gb} / ${d.total_gb} GB</span></div>
      <div class="meter ${d.percent >= 90 ? "hot" : ""}" style="grid-template-columns:1fr 50px;margin:0"><div class="bar"><b style="width:${d.percent}%"></b></div><span class="val">${d.percent.toFixed(0)}%</span></div></div>`)
    .join("");

  const temps = s.temperatures?.map((t) => `<div class="kv"><span>${escapeHtml(t.sensor)}</span><span>${t.celsius} °C</span></div>`).join("") || "";
  const bat = s.battery ? `<div class="kv"><span>Akku</span><span>${s.battery.percent}%${s.battery.plugged ? " ⚡" : ""}</span></div>` : "";
  $("sys-temps").innerHTML = temps || bat ? temps + bat : `<span class="dim">Temperatursensoren sind auf diesem System nicht verfügbar.</span>`;

  if (s.processes) {
    $("sys-procs").querySelector("tbody").innerHTML = s.processes
      .map((p) => `<tr><td>${p.pid}</td><td>${escapeHtml(p.name)}</td><td>${p.cpu_percent.toFixed(1)}</td><td>${p.memory_percent.toFixed(1)}</td></tr>`)
      .join("");
  }

  $("svc-backend").textContent = `online · ${dur(s.backend_uptime_s)}`;
  const g = store.gemini;
  $("svc-gemini").textContent = { ok: "verbunden", connected: "verbunden", error: "Fehler", unconfigured: "kein API-Key", unknown: "bereit", standby: "Standby", closed: "getrennt", connecting: "verbindet …" }[g.state] || g.state;
  $("svc-model").textContent = g.model || store.settings?.ai?.live_model || "–";
  $("svc-time").textContent = new Date(s.ts * 1000).toLocaleString("de-DE");
  $("svc-uptime").textContent = dur(s.uptime_s);
}

export function initSystemPage() {
  bus.on("ws:system.stats", ({ stats }) => render(stats));
  bus.on("page:enter:system", async () => {
    try {
      const info = await api.get("/api/system/info");
      $("sys-info").innerHTML = [
        ["Betriebssystem", info.os], ["Host", info.hostname], ["Prozessor", info.processor],
        ["Kerne", `${info.cpu_cores_physical ?? "?"} physisch / ${info.cpu_cores_logical} logisch`],
        ["RAM", `${info.memory_total_gb} GB`], ["Gestartet", new Date(info.boot_time).toLocaleString("de-DE")],
        ["Python", info.python], ["GPU-Monitoring", info.gpu_monitoring],
      ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("");
      if (store.lastStats) render(store.lastStats);
    } catch { /* offline */ }
  });
}
