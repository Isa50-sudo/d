// Zentrale JARVIS-Visualisierung (Canvas 2D).
// Reagiert auf den Systemzustand (Farbe, Rotation, Partikelbewegung, Intensität)
// und auf echte Audiopegel (Mikrofon beim Zuhören, Stimme beim Sprechen).

const PALETTE = {
  OFFLINE: [110, 130, 140],
  STANDBY: [79, 216, 255],
  READY: [79, 216, 255],
  LISTENING: [90, 235, 255],
  THINKING: [157, 140, 255],
  EXECUTING: [255, 181, 71],
  "WAITING FOR CONFIRMATION": [255, 181, 71],
  SPEAKING: [120, 225, 255],
  COMPLETED: [84, 240, 176],
  ERROR: [255, 84, 112],
};

const PROFILE = {
  OFFLINE: { spin: 0.1, energy: 0.08, swirl: 0.2, glow: 0.25 },
  STANDBY: { spin: 0.25, energy: 0.18, swirl: 0.35, glow: 0.45 },
  READY: { spin: 0.35, energy: 0.25, swirl: 0.5, glow: 0.6 },
  LISTENING: { spin: 0.55, energy: 0.55, swirl: 0.7, glow: 0.85 },
  THINKING: { spin: 1.6, energy: 0.45, swirl: 2.2, glow: 0.8 },
  EXECUTING: { spin: 2.2, energy: 0.6, swirl: 1.4, glow: 0.9 },
  "WAITING FOR CONFIRMATION": { spin: 0.3, energy: 0.35, swirl: 0.3, glow: 0.9 },
  SPEAKING: { spin: 0.7, energy: 0.8, swirl: 0.9, glow: 1.0 },
  COMPLETED: { spin: 0.6, energy: 0.5, swirl: 0.6, glow: 1.0 },
  ERROR: { spin: 0.2, energy: 0.4, swirl: 0.3, glow: 0.8 },
};

const lerp = (a, b, t) => a + (b - a) * t;

export class CoreVisualizer {
  constructor(canvas, { getAnalyser, mini = false, reducedMotion = () => false } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.getAnalyser = getAnalyser;
    this.mini = mini;
    this.reducedMotion = reducedMotion;
    this.state = "OFFLINE";
    this.color = [...PALETTE.OFFLINE];
    this.profile = { ...PROFILE.OFFLINE };
    this.t = 0;
    this.rot = [0, 0, 0];
    this.level = 0;
    this.bars = new Float32Array(mini ? 24 : 96);
    this.freq = new Uint8Array(256);
    this.particles = Array.from({ length: mini ? 0 : 90 }, () => this._particle());
    this.running = false;
    this._resize = this._resize.bind(this);
    new ResizeObserver(this._resize).observe(canvas);
    this._resize();
  }

  _particle() {
    return { a: Math.random() * Math.PI * 2, r: 0.42 + Math.random() * 0.5, s: 0.2 + Math.random() * 0.8, z: Math.random() };
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    this.w = Math.max(1, rect.width);
    this.h = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  setState(state) {
    this.state = PALETTE[state] ? state : "READY";
  }

  start() {
    if (this.running) return;
    this.running = true;
    let last = performance.now();
    const frame = (now) => {
      if (!this.running) return;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!document.hidden) {
        try { this._draw(dt); } catch (err) { console.error("[core-viz]", err); }
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  _readAudio() {
    const analyser = this.getAnalyser?.(this.state);
    if (!analyser) {
      this.level = lerp(this.level, 0, 0.1);
      return false;
    }
    if (this.freq.length !== analyser.frequencyBinCount) this.freq = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(this.freq);
    // Sprachbereich (~80 Hz – 5 kHz) auf die Balken abbilden
    const usable = Math.floor(this.freq.length * 0.45);
    let sum = 0;
    const n = this.bars.length;
    for (let i = 0; i < n; i++) {
      const mirrored = i < n / 2 ? i : n - 1 - i; // symmetrisch
      const idx = 2 + Math.floor((mirrored / (n / 2)) * usable);
      const v = this.freq[idx] / 255;
      this.bars[i] = lerp(this.bars[i], v, 0.35);
      sum += v;
    }
    this.level = lerp(this.level, Math.min(1, (sum / n) * 2.2), 0.25);
    return true;
  }

  _draw(dt) {
    const { ctx, w, h } = this;
    if (w < 16 || h < 16) return; // Canvas (noch) unsichtbar
    const motion = this.reducedMotion() ? 0.25 : 1;
    const target = PALETTE[this.state];
    const prof = PROFILE[this.state];
    for (let i = 0; i < 3; i++) this.color[i] = lerp(this.color[i], target[i], 0.06);
    for (const k of Object.keys(prof)) this.profile[k] = lerp(this.profile[k], prof[k], 0.05);
    const p = this.profile;
    this.t += dt;

    const hasAudio = this._readAudio();
    if (!hasAudio) {
      // Ruhige, synthetische "Atmung" ohne Audio
      for (let i = 0; i < this.bars.length; i++) {
        const v = 0.05 + 0.04 * Math.sin(this.t * 1.3 + i * 0.35) * p.energy;
        this.bars[i] = lerp(this.bars[i], v, 0.1);
      }
    }

    const [r, g, b] = this.color.map(Math.round);
    const c = (a) => `rgba(${r},${g},${b},${a})`;
    const cx = w / 2;
    const cy = h / 2;
    const R = Math.min(w, h) / 2 - 2;

    ctx.clearRect(0, 0, w, h);
    this.rot[0] += dt * 0.12 * p.spin * motion;
    this.rot[1] -= dt * 0.22 * p.spin * motion;
    this.rot[2] += dt * 0.5 * p.spin * motion;

    // Hintergrund-Glow
    const glow = ctx.createRadialGradient(cx, cy, R * 0.05, cx, cy, R);
    glow.addColorStop(0, c(0.22 * p.glow + this.level * 0.25));
    glow.addColorStop(0.35, c(0.06 * p.glow));
    glow.addColorStop(1, c(0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fill();

    if (!this.mini) {
      // Äußerer Skalenring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(this.rot[0]);
      ctx.strokeStyle = c(0.35);
      ctx.lineWidth = 1;
      for (let i = 0; i < 120; i++) {
        const a = (i / 120) * Math.PI * 2;
        const long = i % 10 === 0;
        const r1 = R * 0.96;
        const r2 = R * (long ? 0.9 : 0.93);
        ctx.globalAlpha = long ? 0.9 : 0.45;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();
      ctx.globalAlpha = 1;

      // Segmentierter Bogenring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(this.rot[1]);
      const segs = 6;
      for (let i = 0; i < segs; i++) {
        const start = (i / segs) * Math.PI * 2;
        const len = (Math.PI * 2) / segs - 0.18;
        ctx.strokeStyle = c(i % 2 ? 0.25 : 0.55 + 0.3 * p.glow);
        ctx.lineWidth = i % 2 ? 1 : 2.2;
        ctx.beginPath();
        ctx.arc(0, 0, R * 0.84, start, start + len);
        ctx.stroke();
      }
      ctx.restore();

      // Bestätigung: pulsierender Warnring
      if (this.state === "WAITING FOR CONFIRMATION" || this.state === "ERROR") {
        const pulse = 0.5 + 0.5 * Math.sin(this.t * 4);
        ctx.strokeStyle = c(0.25 + 0.5 * pulse);
        ctx.lineWidth = 2;
        ctx.setLineDash([10, 8]);
        ctx.beginPath();
        ctx.arc(cx, cy, R * (0.78 + 0.01 * pulse), 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Partikel
      for (const pt of this.particles) {
        pt.a += dt * pt.s * 0.35 * p.swirl * motion * (pt.z > 0.5 ? 1 : -1);
        const rr = R * (pt.r * (0.72 + 0.1 * Math.sin(this.t * 0.7 + pt.z * 9)));
        const x = cx + Math.cos(pt.a) * rr;
        const y = cy + Math.sin(pt.a) * rr * 0.98;
        ctx.fillStyle = c(0.15 + 0.5 * pt.z * p.glow);
        ctx.beginPath();
        ctx.arc(x, y, 0.6 + pt.z * 1.3, 0, Math.PI * 2);
        ctx.fill();
      }

      // Denk-/Ausführungsbogen
      if (this.state === "THINKING" || this.state === "EXECUTING") {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(this.rot[2] * 2.2);
        ctx.strokeStyle = c(0.9);
        ctx.lineWidth = 2.5;
        ctx.shadowColor = c(0.9);
        ctx.shadowBlur = 12;
        ctx.beginPath();
        ctx.arc(0, 0, R * 0.7, 0, Math.PI * 0.55);
        ctx.stroke();
        ctx.rotate(Math.PI);
        ctx.beginPath();
        ctx.arc(0, 0, R * 0.7, 0, Math.PI * 0.3);
        ctx.stroke();
        ctx.restore();
      }
    }

    // Radiale Audio-Balken
    const n = this.bars.length;
    const inner = R * (this.mini ? 0.42 : 0.5);
    const maxLen = R * (this.mini ? 0.35 : 0.17);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(this.mini ? 0 : this.rot[0] * 0.5);
    ctx.lineCap = "round";
    ctx.lineWidth = this.mini ? 2 : 2;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const v = this.bars[i] * (0.4 + p.energy);
      const len = 2 + v * maxLen * 1.6;
      ctx.strokeStyle = c(0.35 + v * 0.65);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
      ctx.lineTo(Math.cos(a) * (inner + len), Math.sin(a) * (inner + len));
      ctx.stroke();
    }
    ctx.restore();

    // Innerer Ring
    ctx.strokeStyle = c(0.7);
    ctx.lineWidth = this.mini ? 1.5 : 1.2;
    ctx.beginPath();
    ctx.arc(cx, cy, inner * 0.92, 0, Math.PI * 2);
    ctx.stroke();

    // Kern
    const coreR = inner * (0.5 + 0.12 * this.level + 0.03 * Math.sin(this.t * 2));
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
    core.addColorStop(0, `rgba(255,255,255,${0.75 * p.glow})`);
    core.addColorStop(0.25, c(0.85 * p.glow));
    core.addColorStop(1, c(0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
    ctx.fill();

    if (!this.mini) {
      // Feines Hexagon-Gitter im Kern
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-this.rot[1] * 0.5);
      ctx.strokeStyle = c(0.35 * p.glow);
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i <= 6; i++) {
        const a = (i / 6) * Math.PI * 2;
        const rr = inner * 0.62;
        i === 0 ? ctx.moveTo(Math.cos(a) * rr, Math.sin(a) * rr) : ctx.lineTo(Math.cos(a) * rr, Math.sin(a) * rr);
      }
      ctx.stroke();
      ctx.restore();
    }
  }
}
