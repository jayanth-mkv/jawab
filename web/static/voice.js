// Voice visuals.
//   const tap = JawabVoice.tap(audioCtx, node);      // live frequency bands from any audio node
//   const o = JawabVoice.orb(canvas);                // particle sphere that swells and ripples with the voice
//   o.set({ mode: "live", source: () => level0to1, tint: "agent" | "caller" });   // modes: idle | wave | live
//   const b = JawabVoice.bars(el, { count: 32 });    // rounded bars that stretch with the voice
//   b.set({ mode: "live", source: (n) => tap.read(n) });
const JawabVoice = (() => {
  function tap(ctx, node) {
    const a = ctx.createAnalyser();
    a.fftSize = 512; a.smoothingTimeConstant = 0.55; a.minDecibels = -88; a.maxDecibels = -22;
    node.connect(a);
    const buf = new Uint8Array(a.frequencyBinCount), hz = ctx.sampleRate / a.fftSize;
    const lo = 90, hi = Math.min(5200, ctx.sampleRate / 2 - hz);
    // n mirrored bands (low frequencies in the middle), each 0..1; out.level is their mean.
    function read(n) {
      a.getByteFrequencyData(buf);
      const half = Math.ceil(n / 2), out = new Array(n);
      let sum = 0;
      for (let k = 0; k < half; k++) {
        const f0 = lo * Math.pow(hi / lo, k / half), f1 = lo * Math.pow(hi / lo, (k + 1) / half);
        const i0 = Math.floor(f0 / hz), i1 = Math.max(i0 + 1, Math.ceil(f1 / hz));
        let m = 0;
        for (let i = i0; i < i1 && i < buf.length; i++) m = Math.max(m, buf[i]);
        const v = Math.min(1, (m / 255) * (1 + (k / half) * 0.9)); // speech is quieter in the highs
        sum += v;
        out[half - 1 - k] = v; out[n - half + k] = v;
      }
      out.level = sum / half;
      return out;
    }
    return { read, level: () => Math.min(1, read(16).level * 1.7) };
  }

  function bars(el, { count = 32, min = 0.08 } = {}) {
    el.classList.add("vbars");
    el.innerHTML = "";
    const spans = Array.from({ length: count }, () => el.appendChild(document.createElement("span")));
    const cur = new Float32Array(count), center = (count - 1) / 2;
    let mode = "idle", source = null, raf = 0;
    function frame(ts) {
      if (!el.isConnected) { raf = 0; return; }
      const t = ts / 1000, live = mode === "live" && source ? source(count) : null;
      for (let i = 0; i < count; i++) {
        const d = Math.abs(i - center) / center;
        let target;
        if (live) target = min + live[i] * (1 - min);
        else if (mode === "wave") target = min + 0.42 * (0.5 + 0.5 * Math.sin(t * 5 - i * 0.55)) * (1 - d * 0.55);
        else target = min + 0.06 * (0.5 + 0.5 * Math.sin(t * 1.6 - i * 0.35)) * (1 - d);
        cur[i] += (target - cur[i]) * (target > cur[i] ? 0.55 : 0.16);
        spans[i].style.transform = `scaleY(${cur[i].toFixed(3)})`;
      }
      raf = requestAnimationFrame(frame);
    }
    const start = () => { if (!raf) raf = requestAnimationFrame(frame); };
    start();
    return {
      set(o) { if (o.mode) mode = o.mode; if ("source" in o) source = o.source; el.dataset.mode = mode; start(); },
      destroy() { cancelAnimationFrame(raf); raf = 0; el.innerHTML = ""; },
    };
  }

  // A sphere of glowing points (Fibonacci lattice) that rotates, breathes, and ripples with the level.
  function orb(canvas, { points = 2200, onLevel } = {}) {
    const c = canvas.getContext("2d"), P = [];
    const ga = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < points; i++) {
      const y = 1 - (i / (points - 1)) * 2, r = Math.sqrt(1 - y * y), th = ga * i;
      P.push([Math.cos(th) * r, y, Math.sin(th) * r, Math.random() * 6.283]);
    }
    // Jawab palette: amber highlights over a red core; the caller's voice cools it to white.
    const AGENT = [[255, 236, 210], [255, 120, 70], [255, 40, 70]];
    const CALLER = [[255, 255, 255], [235, 225, 255], [190, 170, 255]];
    let mode = "idle", source = null, tint = 0, tintTarget = 0, level = 0, rot = 0, raf = 0, dpr = 1, W = 0, H = 0;
    const mix = (a, b, t) => a + (b - a) * t;
    function pal(y, t) {
      const s = (1 - y) / 2, A = s < 0.5 ? [AGENT[0], AGENT[1], s * 2] : [AGENT[1], AGENT[2], (s - 0.5) * 2];
      const C = s < 0.5 ? [CALLER[0], CALLER[1], s * 2] : [CALLER[1], CALLER[2], (s - 0.5) * 2];
      return [0, 1, 2].map((k) => Math.round(mix(mix(A[0][k], A[1][k], A[2]), mix(C[0][k], C[1][k], C[2]), t)));
    }
    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    new ResizeObserver(resize).observe(canvas);
    resize();
    function frame(ts) {
      if (!canvas.isConnected) { raf = 0; return; }
      const t = ts / 1000;
      let target;
      if (mode === "live") target = Math.max(0.05, source ? source() : 0);
      else if (mode === "wave") target = 0.22 + 0.16 * Math.sin(t * 5);
      else target = 0.05 + 0.03 * Math.sin(t * 1.4);
      level += (target - level) * (target > level ? 0.3 : 0.07);
      tint += (tintTarget - tint) * 0.06;
      rot += 0.0025 + level * 0.018;
      onLevel && onLevel(level);
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, W, H);
      const cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.36 * (1 + level * 0.14);
      // soft inner glow so the sphere reads as lit from within
      const core = c.createRadialGradient(cx, cy, 0, cx, cy, R * 1.25);
      core.addColorStop(0, `rgba(255,${Math.round(mix(90, 200, tint))},${Math.round(mix(80, 255, tint))},${(0.22 + level * 0.35).toFixed(2)})`);
      core.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = core; c.fillRect(0, 0, W, H);
      c.globalCompositeOperation = "lighter";
      const cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(0.42), st = Math.sin(0.42);
      const amp = 0.03 + level * 0.2;
      for (let i = 0; i < P.length; i++) {
        const [a, b, d, ph] = P[i];
        const x = a * cr - d * sr, z = a * sr + d * cr;
        const y = b * ct - z * st, z2 = b * st + z * ct;
        const n = Math.sin(a * 3.1 + t * 1.7) * Math.cos(b * 2.7 - t * 1.3) * Math.sin(d * 3.3 + t * 1.1);
        const k = 1 + n * amp + Math.sin(ph + t * 3) * 0.012;
        const depth = (z2 + 1) / 2;
        const [r, g, bl] = pal(y, tint);
        c.fillStyle = `rgba(${r},${g},${bl},${(0.12 + depth * 0.85).toFixed(2)})`;
        const s = 0.7 + depth * 1.6;
        c.fillRect(cx + x * R * k - s / 2, cy + y * R * k - s / 2, s, s);
      }
      raf = requestAnimationFrame(frame);
    }
    const start = () => { if (!raf) raf = requestAnimationFrame(frame); };
    start();
    return {
      set(o) {
        if (o.mode) mode = o.mode;
        if ("source" in o) source = o.source;
        if (o.tint) tintTarget = o.tint === "caller" ? 1 : 0;
        start();
      },
    };
  }

  return { tap, bars, orb };
})();
