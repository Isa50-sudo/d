// Wake-Word-Erkennung – austauschbare Engines.
//
// Vertrag (WakeWordEngine):
//   supported: boolean       – ist die Engine in diesem Browser nutzbar?
//   start() / stop()         – Erkennung starten/stoppen
//   onWake(info)             – Callback bei erkanntem Wake Word
//   onError(message, fatal)  – Callback bei Problemen
//
// Aktuelle Implementierung: WebSpeechWakeWord (Web Speech API, Chrome/Edge).
// Hinweis Datenschutz: Chrome verarbeitet Web-Speech-Audio auf Google-Servern.
// Später austauschbar gegen eine lokale Engine (z. B. Picovoice Porcupine oder
// openWakeWord als WASM/Backend-Dienst), die denselben Vertrag erfüllt und die
// 16-kHz-PCM-Frames über feedAudio(pcm) erhält.

const VARIANTS = {
  jarvis: ["jarvis", "jarvi", "jarwis", "javis", "jervis", "dschavis", "dschawis", "tschawis", "charvis", "garvis", "jarves", "service"],
};

export class WakeWordEngine {
  constructor() {
    this.onWake = null;
    this.onError = null;
  }
  get supported() { return false; }
  start() {}
  stop() {}
  feedAudio(_pcm) {}
}

export class WebSpeechWakeWord extends WakeWordEngine {
  constructor({ word = "jarvis", lang = "de-DE" } = {}) {
    super();
    this.word = word.toLowerCase().trim();
    this.lang = lang;
    this.running = false;
    this.recognition = null;
    this.lastWake = 0;
    this.failures = 0;
    const variants = VARIANTS[this.word] || [this.word];
    // "service" nur als Variante von "jarvis" (häufige Fehlerkennung) – aber nur am Satzanfang
    this.pattern = new RegExp(`(^|\\s)(${variants.filter((v) => v !== "service").map(escapeRe).join("|")})(\\s|[,.!?]|$)`, "i");
    this.patternStart = variants.includes("service") ? /^\s*service[,\s]/i : null;
  }

  get supported() {
    return typeof window !== "undefined" && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  start() {
    if (!this.supported || this.running) return;
    this.running = true;
    this.failures = 0;
    this._spawn();
  }

  stop() {
    this.running = false;
    try { this.recognition?.abort(); } catch { /* ignorieren */ }
    this.recognition = null;
  }

  _spawn() {
    if (!this.running) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 3;
    this.recognition = rec;

    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        for (let a = 0; a < result.length; a++) {
          const text = result[a].transcript.toLowerCase();
          if (this.pattern.test(text) || (this.patternStart && this.patternStart.test(text))) {
            this._fire(text);
            return;
          }
        }
      }
    };
    rec.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        this.running = false;
        this.onError?.("Wake-Word-Erkennung wurde vom Browser blockiert.", true);
        return;
      }
      this.failures++;
      if (this.failures > 5) {
        this.running = false;
        this.onError?.(`Wake-Word-Erkennung nicht verfügbar (${event.error}).`, true);
      }
    };
    rec.onend = () => {
      if (this.running) setTimeout(() => this._spawn(), 250);
    };
    try {
      rec.start();
    } catch {
      setTimeout(() => this._spawn(), 1000);
    }
  }

  _fire(text) {
    const now = Date.now();
    if (now - this.lastWake < 2000) return;
    this.lastWake = now;
    this.onWake?.({ transcript: text });
  }
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function createWakeWordEngine(settings) {
  const engine = new WebSpeechWakeWord({ word: settings?.conversation?.wake_word || "jarvis", lang: settings?.voice?.language || "de-DE" });
  return engine.supported ? engine : new WakeWordEngine();
}
