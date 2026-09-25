// Gesprächssteuerung: verbindet Mikrofon, Wake Word, Backend-Session und Wiedergabe.
//
// Modi:
//   wake_word    – Standby, bis "Jarvis" erkannt wird. Dann fließt Audio (inkl. ~1,5 s
//                  Pre-Roll, damit "Jarvis, wie ist das Wetter" vollständig ankommt) zu
//                  JARVIS. Nach der Antwort bleibt ein Follow-up-Fenster offen; danach
//                  wieder Standby.
//   continuous   – Mikrofon dauerhaft offen (JARVIS reagiert nur, wenn er angesprochen wird).
//   push_to_talk – Leertaste bzw. Kern gedrückt halten.
import { bus } from "../core/bus.js";
import { sendBinary, sendJson } from "../core/ws.js";
import { setStore, store } from "../core/store.js";
import { getFlags, setFlags, setHint } from "../core/state.js";
import { Microphone } from "./mic.js";
import { VoicePlayer } from "./player.js";
import { createWakeWordEngine } from "./wakeword.js";

const PREROLL_CHUNKS = 38; // 38 × 40 ms ≈ 1,5 s

class ConversationController {
  constructor() {
    this.mode = "wake_word";
    this.muted = false;
    this.streaming = false;
    this.engaged = false; // Benutzer hat JARVIS aktiviert (Audio freigeschaltet)
    this.preroll = [];
    this.followUpTimer = null;
    this.wake = null;
    this.wakeAvailable = false;
    this.pttDown = false;
    this.noiseFloor = 0.01;
    this.localSpeech = false;
    this.lastVoiceAt = 0;
    this.sawUserSpeech = false;

    this.player = new VoicePlayer(22050);
    this.mic = new Microphone({ targetRate: 16000, onChunk: (pcm, rms) => this._onChunk(pcm, rms) });
    this.mic.onEnded = () => this._micFailed("Das Mikrofon ist momentan nicht verfügbar.");
  }

  get micAnalyser() { return this.mic.analyser; }
  get playerAnalyser() { return this.player.analyser; }

  /** Muss aus einer Benutzergeste heraus aufgerufen werden (Autoplay-Richtlinie). */
  async engage() {
    this.player.sampleRate = store.audio.output_sample_rate || 22050;
    await this.player.resume();
    this.player.onPlayingChange = (playing) => {
      setFlags({ speaking: playing });
      if (playing) {
        setFlags({ thinking: false });
        this._clearFollowUp();
      } else {
        this._armFollowUp();
      }
    };
    bus.on("audio:chunk", (buf) => this.player.push(buf));
    this.engaged = true;
    await this.startMic();
    this.setMode(store.settings?.conversation?.activation_mode || "wake_word", { persist: false });
  }

  async startMic() {
    try {
      await this.mic.start(store.settings?.conversation?.microphone_device_id || null);
      setStore({ mic: this.muted ? "muted" : "ready" });
      bus.emit("voice:mic", { ok: true });
    } catch (err) {
      this._micFailed(err.message);
    }
  }

  _micFailed(message) {
    setStore({ mic: "error" });
    this._stopStreaming(false);
    this.wake?.stop();
    setHint("Mikrofon nicht verfügbar – Textchat ist weiterhin möglich.");
    bus.emit("voice:error", { message });
  }

  setMode(mode, { persist = true } = {}) {
    this.mode = mode;
    this._stopStreaming(true);
    this.wake?.stop();
    this.wake = null;
    if (persist) bus.emit("voice:mode-persist", mode);
    bus.emit("voice:mode", mode);
    if (!this.engaged || this.muted || !this.mic.active) {
      this._applyIdleHint();
      return;
    }

    if (mode === "continuous") {
      this._startStreaming();
    } else if (mode === "wake_word") {
      this.wake = createWakeWordEngine(store.settings);
      this.wakeAvailable = this.wake.supported;
      if (this.wakeAvailable) {
        this.wake.onWake = () => this.activate("wake");
        this.wake.onError = (msg, fatal) => {
          if (fatal) {
            this.wakeAvailable = false;
            bus.emit("voice:notice", { message: `${msg} Tippe auf den Kern oder drücke die Leertaste, um zu sprechen.` });
            this._applyIdleHint();
          }
        };
        this.wake.start();
      }
      setFlags({ standby: true });
      // Session im Hintergrund vorwärmen, damit die erste Antwort schnell kommt
      sendJson({ type: "voice.start" });
    } else {
      setFlags({ standby: false });
      sendJson({ type: "voice.start" });
    }
    this._applyIdleHint();
  }

  _applyIdleHint() {
    const word = (store.settings?.conversation?.wake_word || "jarvis").replace(/^./, (c) => c.toUpperCase());
    if (!this.engaged) return setHint("");
    if (["error", "missing_model"].includes(store.ai.state)) return setHint(store.ai.message || "Ollama ist nicht bereit.");
    if (store.mic === "error") return setHint("Mikrofon nicht verfügbar – nutze den Textchat.");
    if (this.muted) return setHint("Mikrofon stummgeschaltet (M)");
    if (this.streaming && this.mode !== "push_to_talk") return setHint(this.mode === "continuous" ? "Mikrofon offen – sprich einfach." : "Ich höre zu …");
    if (this.mode === "wake_word") return setHint(this.wakeAvailable ? `Sag „${word}“ – oder tippe auf den Kern.` : "Tippe auf den Kern oder drücke die Leertaste, um zu sprechen.");
    if (this.mode === "push_to_talk") return setHint("Leertaste oder Kern gedrückt halten zum Sprechen.");
    return setHint("");
  }

  /** Wake Word erkannt / Kern angetippt. */
  activate(source = "tap") {
    if (!this.engaged || this.muted || !this.mic.active) return;
    this.wake?.stop();
    setFlags({ standby: false });
    sendJson({ type: "voice.start" });
    // Pre-Roll senden: enthält den Satzanfang, der während der Erkennung gesprochen wurde
    if (source === "wake") this.preroll.forEach((chunk) => sendBinary(chunk));
    this.preroll = [];
    this._startStreaming();
    this._armFollowUp(true);
    bus.emit("voice:activated", { source });
  }

  /** Zurück in den Standby (nur Wake-Word-Modus). */
  deactivate() {
    if (this.mode !== "wake_word") return;
    this._stopStreaming(true);
    setFlags({ standby: true, thinking: false });
    if (this.wakeAvailable && !this.muted) this.wake?.start();
    this._applyIdleHint();
  }

  toggleFromCore() {
    if (!this.engaged) return;
    if (["error", "closed", "missing_model"].includes(store.ai.state)) {
      sendJson({ type: "voice.start" }); // nach Fehler: Verbindung neu aufbauen
    }
    if (this.mode === "wake_word") {
      if (this.streaming) this.deactivate();
      else this.activate("tap");
    } else if (this.mode === "continuous") {
      if (getFlags().speaking) this.player.clear();
    }
  }

  pttStart() {
    if (this.mode !== "push_to_talk" || this.pttDown || this.muted || !this.mic.active) return;
    this.pttDown = true;
    if (getFlags().speaking) this.player.clear();
    this._startStreaming();
  }

  pttEnd() {
    if (!this.pttDown) return;
    this.pttDown = false;
    this._stopStreaming(true);
    if (this.sawUserSpeech) setFlags({ thinking: true });
    this.sawUserSpeech = false;
  }

  setMuted(muted) {
    this.muted = muted;
    setStore({ mic: muted ? "muted" : this.mic.active ? "ready" : store.mic });
    if (muted) {
      this._stopStreaming(true);
      this.wake?.stop();
      setFlags({ standby: false });
    } else {
      this.setMode(this.mode, { persist: false });
    }
    this._applyIdleHint();
  }

  async changeDevice(deviceId) {
    const wasStreaming = this.streaming;
    await this.mic.start(deviceId || null).catch((err) => this._micFailed(err.message));
    if (wasStreaming) this._startStreaming();
  }

  sendText(text) {
    if (getFlags().speaking) this.player.clear();
    const ok = sendJson({ type: "text", text });
    if (ok) setFlags({ thinking: true });
    return ok;
  }

  /** Backend meldet geschlossene/pausierte Session: bei aktivem Stream neu verbinden. */
  onAiStatus(state) {
    if (this.streaming && ["standby", "closed"].includes(state)) sendJson({ type: "voice.start" });
  }

  onInterrupted() {
    this.player.clear();
  }

  onTurnComplete() {
    setFlags({ thinking: false });
    this._armFollowUp();
  }

  onUserActivity(active) {
    if (active) {
      this._clearFollowUp();
      this.sawUserSpeech = true;
    } else if (this.streaming) {
      setFlags({ thinking: true });
    }
  }

  // --------------------------------------------------------------------
  _startStreaming() {
    if (this.streaming) return;
    this.streaming = true;
    this.sawUserSpeech = false;
    setStore({ mic: "live" });
    setFlags({ listening: true });
    this._applyIdleHint();
  }

  _stopStreaming(sendPause) {
    if (!this.streaming) return;
    this.streaming = false;
    this._clearFollowUp();
    if (sendPause) sendJson({ type: "voice.pause" });
    setStore({ mic: this.muted ? "muted" : this.mic.active ? "ready" : store.mic });
    setFlags({ listening: false });
    this._applyIdleHint();
  }

  _onChunk(pcm, rms) {
    // Adaptive Sprach-Erkennung (lokal) – für Follow-up-Fenster und Visualisierung
    this.noiseFloor = rms < this.noiseFloor ? this.noiseFloor * 0.9 + rms * 0.1 : this.noiseFloor * 0.999 + rms * 0.001;
    const threshold = Math.max(0.015, this.noiseFloor * 3.2);
    const now = performance.now();
    if (rms > threshold) {
      this.lastVoiceAt = now;
      if (!this.localSpeech) {
        this.localSpeech = true;
        if (this.streaming) this._clearFollowUp();
      }
    } else if (this.localSpeech && now - this.lastVoiceAt > 500) {
      this.localSpeech = false;
      if (this.streaming) this._armFollowUp();
    }
    bus.emit("voice:level", rms);

    if (this.muted) return;
    if (this.streaming) {
      sendBinary(pcm);
    } else {
      this.preroll.push(pcm);
      if (this.preroll.length > PREROLL_CHUNKS) this.preroll.shift();
    }
  }

  _armFollowUp(initial = false) {
    if (this.mode !== "wake_word" || !this.streaming) return;
    this._clearFollowUp();
    const f = getFlags();
    if (!initial && (f.speaking || f.toolsRunning || f.confirmPending || f.thinking)) return;
    const seconds = store.settings?.conversation?.follow_up_window_s ?? 12;
    this.followUpTimer = setTimeout(() => {
      const g = getFlags();
      if (g.speaking || g.toolsRunning || g.confirmPending || g.thinking || this.localSpeech) {
        this._armFollowUp();
        return;
      }
      this.deactivate();
    }, (initial ? seconds + 4 : seconds) * 1000);
  }

  _clearFollowUp() {
    clearTimeout(this.followUpTimer);
    this.followUpTimer = null;
  }
}

export const conversation = new ConversationController();
