// AudioWorklet: Mikrofon -> Tiefpass -> Resampling auf 16 kHz -> PCM16-Chunks (40 ms).
// Läuft im Audio-Thread; unabhängig von der Sample-Rate des Geräts (44.1/48 kHz …).
class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = options.processorOptions || {};
    this.targetRate = opts.targetRate || 16000;
    this.ratio = sampleRate / this.targetRate;
    this.chunk = new Int16Array(Math.round(this.targetRate * (opts.chunkMs || 40) / 1000));
    this.idx = 0;
    this.t = 0; // fraktionale Leseposition
    this.prev = 0;
    // Zweistufiger Tiefpass (~7 kHz) gegen Aliasing beim Heruntertakten
    this.alpha = 1 - Math.exp((-2 * Math.PI * Math.min(7000, this.targetRate * 0.45)) / sampleRate);
    this.lp1 = 0;
    this.lp2 = 0;
    this.sumSq = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const src = input[0];
    const n = src.length;
    const filtered = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      this.lp1 += this.alpha * (src[i] - this.lp1);
      this.lp2 += this.alpha * (this.lp1 - this.lp2);
      filtered[i] = this.ratio > 1.05 ? this.lp2 : src[i];
    }
    const at = (i) => (i < 0 ? this.prev : filtered[i]);
    while (this.t < n - 1) {
      const i = Math.floor(this.t);
      const frac = this.t - i;
      let s = at(i) * (1 - frac) + at(i + 1) * frac;
      s = Math.max(-1, Math.min(1, s));
      this.sumSq += s * s;
      this.chunk[this.idx++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.idx === this.chunk.length) {
        const rms = Math.sqrt(this.sumSq / this.chunk.length);
        const out = this.chunk.slice(0);
        this.port.postMessage({ pcm: out.buffer, rms }, [out.buffer]);
        this.idx = 0;
        this.sumSq = 0;
      }
      this.t += this.ratio;
    }
    this.t -= n;
    this.prev = filtered[n - 1];
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
