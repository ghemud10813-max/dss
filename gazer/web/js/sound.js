// Synthesized UI sound design (WebAudio, no assets). Quiet by default.

let ctx = null;
let master = null;
let enabled = true;
let unlocked = false; // browsers only allow audio after a user gesture

function ac() {
  if (!enabled || !unlocked) return null;
  if (!ctx) {
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain();
      master.gain.value = 0.18;
      const comp = ctx.createDynamicsCompressor();
      master.connect(comp).connect(ctx.destination);
    } catch { return null; }
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

export function setEnabled(v) { enabled = v; if (!v && ctx) ctx.suspend().catch(() => {}); }
export function isEnabled() { return enabled; }
export function unlock() { unlocked = true; ac(); }

function tone({ f = 880, f2 = null, dur = 0.08, type = "sine", gain = 0.5, delay = 0, attack = 0.004 }) {
  const c = ac();
  if (!c) return;
  const t = c.currentTime + delay;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f, t);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, t + dur);
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(gain, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g).connect(master);
  o.start(t);
  o.stop(t + dur + 0.02);
}

function noise({ dur = 0.4, from = 400, to = 4000, gain = 0.3, q = 1.2, delay = 0 }) {
  const c = ac();
  if (!c) return;
  const t = c.currentTime + delay;
  const buf = c.createBuffer(1, Math.ceil(c.sampleRate * dur), c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  const src = c.createBufferSource();
  src.buffer = buf;
  const bp = c.createBiquadFilter();
  bp.type = "bandpass";
  bp.Q.value = q;
  bp.frequency.setValueAtTime(from, t);
  bp.frequency.exponentialRampToValueAtTime(to, t + dur);
  const g = c.createGain();
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(gain, t + dur * 0.3);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(bp).connect(g).connect(master);
  src.start(t);
}

export const sfx = {
  hover: () => tone({ f: 2400, dur: 0.03, gain: 0.08, type: "triangle" }),
  tap: () => tone({ f: 1400, f2: 900, dur: 0.06, gain: 0.22, type: "triangle" }),
  nav: () => { tone({ f: 520, f2: 1040, dur: 0.12, gain: 0.18, type: "sine" }); noise({ dur: 0.35, from: 300, to: 3000, gain: 0.05 }); },
  toggleOn: () => { tone({ f: 660, dur: 0.06, gain: 0.2 }); tone({ f: 990, dur: 0.08, gain: 0.2, delay: 0.05 }); },
  toggleOff: () => { tone({ f: 990, dur: 0.06, gain: 0.18 }); tone({ f: 560, dur: 0.08, gain: 0.18, delay: 0.05 }); },
  click: () => { tone({ f: 1800, f2: 600, dur: 0.07, gain: 0.25, type: "square" }); },
  engage: () => {
    noise({ dur: 1.0, from: 120, to: 2400, gain: 0.18, q: 2 });
    [220, 330, 440, 660].forEach((f, i) => tone({ f, dur: 0.5, gain: 0.14, delay: i * 0.08, type: "sawtooth", attack: 0.02 }));
    tone({ f: 55, f2: 110, dur: 1.2, gain: 0.35, type: "sine", attack: 0.05 });
  },
  disengage: () => { tone({ f: 440, f2: 110, dur: 0.6, gain: 0.25, type: "sawtooth", attack: 0.01 }); noise({ dur: 0.5, from: 2000, to: 150, gain: 0.1 }); },
  capture: () => tone({ f: 1760, dur: 0.05, gain: 0.12, type: "sine" }),
  point: () => { tone({ f: 880, f2: 1320, dur: 0.12, gain: 0.2 }); },
  success: () => [523, 659, 784, 1047].forEach((f, i) => tone({ f, dur: 0.35, gain: 0.18, delay: i * 0.09, type: "triangle" })),
  fail: () => [392, 330, 262].forEach((f, i) => tone({ f, dur: 0.3, gain: 0.18, delay: i * 0.12, type: "square" })),
  boot: () => {
    tone({ f: 40, f2: 80, dur: 2.4, gain: 0.35, attack: 0.3 });
    noise({ dur: 1.6, from: 80, to: 5000, gain: 0.12, q: 3 });
    [0.8, 1.1, 1.4, 1.7, 2.0].forEach((d, i) => tone({ f: 1200 + i * 180, dur: 0.05, gain: 0.12, delay: d, type: "square" }));
    tone({ f: 220, dur: 1.2, gain: 0.2, delay: 2.4, type: "sawtooth", attack: 0.05 });
    tone({ f: 330, dur: 1.2, gain: 0.16, delay: 2.4, type: "sawtooth", attack: 0.05 });
  },
  blip: () => tone({ f: 3000, dur: 0.02, gain: 0.05, type: "square" }),
  toast: () => { tone({ f: 1320, dur: 0.05, gain: 0.1 }); tone({ f: 1760, dur: 0.07, gain: 0.1, delay: 0.06 }); },
};
