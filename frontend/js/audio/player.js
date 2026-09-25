// Wiedergabe der JARVIS-Stimme (PCM16 mono, Sample-Rate vom Backend) mit Analyser für die Visualisierung.
export class VoicePlayer {
  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
    this.ctx = null;
    this.node = null;
    this.analyser = null;
    this.gain = null;
    this.playing = false;
    this.onPlayingChange = null;
  }

  async init() {
    if (this.ctx) return;
    this.ctx = new AudioContext({ sampleRate: this.sampleRate, latencyHint: "interactive" });
    await this.ctx.audioWorklet.addModule("js/audio/worklets/playback-worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "playback-processor", { outputChannelCount: [1] });
    this.node.port.onmessage = (e) => {
      if (e.data.type === "state") {
        this.playing = e.data.playing;
        this.onPlayingChange?.(e.data.playing, e.data);
      }
    };
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.smoothingTimeConstant = 0.75;
    this.gain = this.ctx.createGain();
    this.node.connect(this.gain).connect(this.analyser).connect(this.ctx.destination);
  }

  async resume() {
    await this.init();
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  push(arrayBuffer) {
    if (!this.node) return;
    this.node.port.postMessage({ type: "push", pcm: arrayBuffer }, [arrayBuffer]);
  }

  clear() {
    this.node?.port.postMessage({ type: "clear" });
  }

  setVolume(value) {
    if (this.gain) this.gain.gain.value = value;
  }
}
