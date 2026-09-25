// Mikrofonaufnahme mit Echo-Unterdrückung, Pegelmessung und PCM16-16-kHz-Ausgabe.
export class MicrophoneError extends Error {
  constructor(message, reason) {
    super(message);
    this.reason = reason;
  }
}

export class Microphone {
  constructor({ targetRate = 16000, onChunk, onLevel } = {}) {
    this.targetRate = targetRate;
    this.onChunk = onChunk;
    this.onLevel = onLevel;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.analyser = null;
    this.deviceId = null;
  }

  get active() {
    return !!this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live");
  }

  async start(deviceId = null) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new MicrophoneError("Das Mikrofon ist momentan nicht verfügbar (Browser unterstützt keinen Mikrofonzugriff).", "unsupported");
    }
    await this.stop();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      if (deviceId && err.name === "OverconstrainedError") return this.start(null);
      const reason = err.name === "NotAllowedError" ? "denied" : err.name === "NotFoundError" ? "missing" : "error";
      const text = {
        denied: "Das Mikrofon ist momentan nicht verfügbar – der Zugriff wurde im Browser verweigert.",
        missing: "Das Mikrofon ist momentan nicht verfügbar – es wurde kein Mikrofon gefunden.",
        error: "Das Mikrofon ist momentan nicht verfügbar.",
      }[reason];
      throw new MicrophoneError(text, reason);
    }
    this.deviceId = deviceId;
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    await this.ctx.audioWorklet.addModule("js/audio/worklets/capture-worklet.js");
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture-processor", {
      processorOptions: { targetRate: this.targetRate, chunkMs: 40 },
    });
    this.node.port.onmessage = (e) => {
      this.onLevel?.(e.data.rms);
      this.onChunk?.(e.data.pcm, e.data.rms);
    };
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.smoothingTimeConstant = 0.7;
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    source.connect(this.analyser);
    source.connect(this.node);
    this.node.connect(sink).connect(this.ctx.destination);
    this.stream.getAudioTracks()[0].addEventListener("ended", () => this.onEnded?.());
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  async stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.ctx) {
      try { await this.ctx.close(); } catch { /* bereits geschlossen */ }
    }
    this.ctx = null;
    this.node = null;
    this.analyser = null;
  }

  static async listDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === "audioinput");
  }
}
