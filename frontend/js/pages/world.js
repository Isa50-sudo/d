// WORLD: interaktiver 3D-Globus (globe.gl / Three.js / WebGL).
// JARVIS steuert ihn über "world.focus"/"world.clear"-Events (Tool show_location_on_globe).
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml } from "../core/store.js";

// Lokal gebündelt (MIT-Lizenz, siehe assets/vendor/globe/LICENSE.txt) – funktioniert ohne CDN
const GLOBE_URL = "assets/vendor/globe/globe.gl.min.js";
const IMG = "assets/vendor/globe";
const $ = (id) => document.getElementById(id);

let globe = null;
let loading = null;
let markers = loadMarkers();
let pendingFocus = null;

function loadMarkers() {
  try { return JSON.parse(localStorage.getItem("jarvis.markers") || "[]"); } catch { return []; }
}
function saveMarkers() {
  try { localStorage.setItem("jarvis.markers", JSON.stringify(markers.slice(-40))); } catch { /* privat/voll */ }
}

function accent() {
  return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#4fd8ff";
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = resolve;
    s.onerror = () => reject(new Error("Globus-Bibliothek konnte nicht geladen werden."));
    document.head.append(s);
  });
}

async function ensureGlobe() {
  if (globe) return globe;
  if (loading) return loading;
  loading = (async () => {
    const status = $("globe-status");
    try {
      if (!window.Globe) await loadScript(GLOBE_URL);
      const container = $("globe");
      globe = window.Globe({ animateIn: true })(container)
        .globeImageUrl(`${IMG}/earth-night.jpg`)
        .bumpImageUrl(`${IMG}/earth-topology.png`)
        .backgroundColor("rgba(0,0,0,0)")
        .showAtmosphere(true)
        .atmosphereColor(accent())
        .atmosphereAltitude(0.18)
        .pointAltitude(0.02)
        .pointRadius(0.35)
        .pointColor(() => accent())
        .pointLabel((d) => `<div class="globe-label">${escapeHtml(d.label || d.name)}</div>`)
        .onPointClick((d) => focus(d, false))
        .ringColor(() => (t) => `rgba(${getComputedStyle(document.documentElement).getPropertyValue("--accent-rgb")},${1 - t})`)
        .ringMaxRadius(4)
        .ringPropagationSpeed(2.5)
        .ringRepeatPeriod(900)
        .htmlElementsData([])
        .htmlElement((d) => {
          const el = document.createElement("div");
          el.className = "globe-label";
          el.textContent = d.name;
          return el;
        });
      const controls = globe.controls();
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.35;
      controls.enableDamping = true;
      const resize = () => globe.width(container.clientWidth).height(container.clientHeight);
      new ResizeObserver(resize).observe(container);
      resize();
      status.remove();
      render();
      if (pendingFocus) {
        focus(pendingFocus, false);
        pendingFocus = null;
      }
      return globe;
    } catch (err) {
      status.textContent = err.message;
      loading = null;
      throw err;
    }
  })();
  return loading;
}

function render() {
  const list = $("marker-list");
  list.innerHTML = markers.length
    ? markers.map((m, i) => `<li data-i="${i}"><span>${escapeHtml(m.name)}</span><span class="dim mono small">${m.lat.toFixed(2)}, ${m.lon.toFixed(2)}</span></li>`).join("")
    : `<li class="dim">Keine Markierungen</li>`;
  if (!globe) return;
  const data = markers.map((m) => ({ ...m, lng: m.lon }));
  globe.pointsData(data).htmlElementsData(data);
}

function focus(marker, add = true) {
  if (add && !markers.some((m) => Math.abs(m.lat - marker.lat) < 0.01 && Math.abs(m.lon - marker.lon) < 0.01)) {
    markers.push({ name: marker.name, full_name: marker.full_name, label: marker.label, lat: marker.lat, lon: marker.lon });
    saveMarkers();
  }
  render();
  $("world-info").innerHTML = `<div style="color:var(--text)">${escapeHtml(marker.full_name || marker.name)}</div><div class="mono small">${marker.lat.toFixed(4)}°, ${marker.lon.toFixed(4)}°</div>${marker.label && marker.label !== marker.full_name ? `<div class="small">${escapeHtml(marker.label)}</div>` : ""}`;
  if (!globe) {
    pendingFocus = marker;
    return;
  }
  globe.controls().autoRotate = false;
  globe.pointOfView({ lat: marker.lat, lng: marker.lon, altitude: 1.4 }, 2200);
  globe.ringsData([{ lat: marker.lat, lng: marker.lon }]);
  clearTimeout(focus.t);
  focus.t = setTimeout(() => globe && (globe.controls().autoRotate = true), 20000);
}

export function initWorldPage() {
  bus.on("page:enter:world", () => ensureGlobe().catch(() => {}));
  bus.on("ws:world.focus", ({ marker, keep }) => {
    if (!keep) markers = [];
    focus(marker, true);
    ensureGlobe().catch(() => {});
  });
  bus.on("ws:world.clear", () => {
    markers = [];
    saveMarkers();
    globe?.ringsData([]);
    render();
  });
  $("world-clear").addEventListener("click", () => bus.emit("ws:world.clear", {}));
  $("marker-list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li) focus(markers[Number(li.dataset.i)], false);
  });
  $("world-search").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("world-query").value.trim();
    if (!q) return;
    try {
      const place = await api.get(`/api/geocode?q=${encodeURIComponent(q)}`);
      focus({ name: place.name, full_name: place.full_name, label: place.full_name, lat: place.lat, lon: place.lon });
    } catch (err) {
      $("world-info").textContent = err.message;
    }
  });
  bus.on("store", () => globe?.atmosphereColor(accent()));
  render();
}
