// AudioWorklet: Wiedergabe der JARVIS-Stimme (PCM16 24 kHz) über einen Ringpuffer.
// Unterstützt sofortiges Leeren bei Unterbrechung (Barge-in) und meldet Start/Ende.
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = sampleRate * 120; // 2 Minuten Puffer
    this.buf = new Float32Array(this.size);
    this.read = 0;
    this.write = 0;
    this.count = 0;
    this.playing = false;
    this.primed = false;
    this.emptyFrames = 0;
    this.prebuffer = Math.round(sampleRate * 0.06); // 60 ms Jitterpuffer
    this.endGrace = Math.round(sampleRate * 0.35); // Hysterese gegen Flackern
    this.port.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === "push") this.push(new Int16Array(msg.pcm));
      else if (msg.type === "clear") this.clear();
    };
  }

  push(int16) {
    for (let i = 0; i < int16.length; i++) {
      if (this.count >= this.size) break; // Überlauf: verwerfen
      this.buf[this.write] = int16[i] / 0x8000;
      this.write = (this.write + 1) % this.size;
      this.count++;
    }
  }

  clear() {
    this.read = this.write = this.count = 0;
    this.primed = false;
    if (this.playing) {
      this.playing = false;
      this.port.postMessage({ type: "state", playing: false, cleared: true });
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    if (!this.primed && this.count >= this.prebuffer) this.primed = true;
    let wrote = 0;
    if (this.primed) {
      for (let i = 0; i < out.length && this.count > 0; i++) {
        out[i] = this.buf[this.read];
        this.read = (this.read + 1) % this.size;
        this.count--;
        wrote++;
      }
    }
    for (let i = wrote; i < out.length; i++) out[i] = 0;
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);

    if (wrote > 0) {
      this.emptyFrames = 0;
      if (!this.playing) {
        this.playing = true;
        this.port.postMessage({ type: "state", playing: true });
      }
    } else if (this.playing) {
      this.emptyFrames += out.length;
      if (this.emptyFrames > this.endGrace) {
        this.playing = false;
        this.primed = false;
        this.port.postMessage({ type: "state", playing: false });
      }
    }
    return true;
  }
}

registerProcessor("playback-processor", PlaybackProcessor);
