// AudioWorklet: 16-bit PCM from Gemini Live (24 kHz) -> speakers, via a ring buffer.
// Post an ArrayBuffer to enqueue audio, or {cmd: "flush"} to stop instantly on barge-in.
class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = 24000 * 180;
    this.ring = new Float32Array(this.size);
    this.w = 0;
    this.r = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.cmd === "flush") { this.r = this.w; return; }
      const pcm = new Int16Array(e.data);
      for (let i = 0; i < pcm.length; i++) {
        this.ring[this.w] = pcm[i] / 32768;
        this.w = (this.w + 1) % this.size;
        if (this.w === this.r) this.r = (this.r + 1) % this.size;
      }
    };
  }
  process(_, outputs) {
    const out = outputs[0][0];
    let level = 0;
    for (let i = 0; i < out.length; i++) {
      if (this.r !== this.w) {
        out[i] = this.ring[this.r];
        this.r = (this.r + 1) % this.size;
        level = Math.max(level, Math.abs(out[i]));
      } else out[i] = 0;
    }
    if (outputs[0][1]) outputs[0][1].set(out);
    this.port.postMessage({ level, queued: (this.w - this.r + this.size) % this.size });
    return true;
  }
}
registerProcessor("pcm-player", PcmPlayer);
