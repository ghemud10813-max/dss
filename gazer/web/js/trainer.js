// Gaze Trainer: fullscreen target practice driven by the real engine.
// Targets appear; hit them with any Gazer click (dwell, double blink, wink…).
// Scores accuracy, time-to-hit and Fitts' throughput (bits/s).

import { request } from "./net.js";
import { sfx } from "./sound.js";
import { store } from "./ui.js";

export const LEVELS = {
  warmup: { label: "WARM-UP", size: 150, count: 10, time: 7 },
  precision: { label: "PRECISION", size: 80, count: 12, time: 7 },
  sniper: { label: "SNIPER", size: 40, count: 12, time: 8 },
};

const layerEl = () => document.getElementById("trainer-layer");
let active = false;
let canvas, g, W, H, raf;
let game = null;
let onDone = null;
let frame = null;
let startedControl = false;

export function isTraining() { return active; }

function best(level) {
  try { return JSON.parse(localStorage.getItem(`gazer-trainer-${level}`) || "null"); } catch { return null; }
}
function saveBest(level, res) {
  try {
    const b = best(level);
    if (!b || res.score > b.score) localStorage.setItem(`gazer-trainer-${level}`, JSON.stringify(res));
  } catch { /* storage unavailable */ }
}
export const bestScore = best;

// physical screen px (engine space) → page px, assuming the layer is fullscreen on the controlled monitor
function toPage(nx, ny) { return [nx * W, ny * H]; }
function sizeToPage(px) { return (px / (store.state?.screen.w || 1920)) * W; }

export async function openTrainer(level, done) {
  if (active) return;
  active = true;
  onDone = done;
  const L = layerEl();
  L.hidden = false;
  L.innerHTML = "";
  canvas = document.createElement("canvas");
  L.append(canvas);
  g = canvas.getContext("2d");
  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("keydown", onKey, true);
  L.requestFullscreen?.().catch(() => {});
  startedControl = false;
  if (!store.frame?.control) {
    startedControl = true;
    request("control", { on: true }).catch(() => {});
  }
  const cfg = LEVELS[level];
  game = { level, cfg, state: "countdown", t0: performance.now(), i: -1, targets: [], hits: [], misses: 0,
    score: 0, combo: 0, fx: [], last: [0.5, 0.5] };
  raf = requestAnimationFrame(draw);
}

export function closeTrainer() {
  if (!active) return;
  active = false;
  cancelAnimationFrame(raf);
  window.removeEventListener("resize", resize);
  window.removeEventListener("keydown", onKey, true);
  const L = layerEl();
  L.hidden = true;
  L.innerHTML = "";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  if (startedControl) request("control", { on: false }).catch(() => {});
  if (store.state?.app.demo) request("demo_look", { p: null, hold: 0 }).catch(() => {});
  const cb = onDone;
  onDone = null;
  game = null;
  cb && cb();
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  W = window.innerWidth; H = window.innerHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function onKey(e) {
  if (e.key === "Escape") { closeTrainer(); e.preventDefault(); }
  if (game?.state === "results" && (e.key === "Enter" || e.key === " ")) { restart(); e.preventDefault(); }
}

function restart() {
  const { level } = game;
  Object.assign(game, { state: "countdown", t0: performance.now(), i: -1, targets: [], hits: [], misses: 0, score: 0,
    combo: 0, fx: [], last: [0.5, 0.5] });
  sfx.nav();
  void level;
}

function nextTarget(now) {
  const { cfg } = game;
  game.i += 1;
  if (game.i >= cfg.count) { finish(); return; }
  let nx, ny, tries = 0;
  do {
    nx = 0.1 + Math.random() * 0.8;
    ny = 0.14 + Math.random() * 0.74;
    tries++;
  } while (Math.hypot(nx - game.last[0], (ny - game.last[1]) * 0.56) < 0.2 && tries < 20);
  const tgt = { nx, ny, born: now, from: game.last.slice() };
  game.targets.push(tgt);
  game.cur = tgt;
  if (store.state?.app.demo) request("demo_look", { p: [nx, ny], hold: cfg.time }).catch(() => {});
}

function finish() {
  const { cfg, hits } = game;
  const n = cfg.count;
  const times = hits.map((h) => h.mt);
  const tp = hits.length ? hits.reduce((s, h) => s + h.id / Math.max(h.mt, 0.15), 0) / hits.length : 0;
  game.result = {
    score: Math.round(game.score), accuracy: hits.length / n, mean: times.length ? times.reduce((a, b) => a + b, 0) / times.length : 0,
    tp, hits: hits.length, n, when: Date.now(),
  };
  game.prevBest = best(game.level);
  saveBest(game.level, game.result);
  game.state = "results";
  game.resultsAt = performance.now();
  (game.result.accuracy >= 0.7 ? sfx.success : sfx.fail)();
  if (store.state?.app.demo) request("demo_look", { p: null, hold: 0 }).catch(() => {});
}

// Engine clicks arrive as frame events (works for real and virtual cursors alike).
export function trainerFrame(f) {
  if (!active || !game) return;
  frame = f;
  for (const ev of f.events || []) {
    if (ev.k !== "click" || !ev.p) continue;
    if (game.state === "results") { handleResultsClick(ev.p); continue; }
    if (game.state !== "play" || !game.cur) continue;
    const [x, y] = toPage(ev.p[0], ev.p[1]);
    const [tx, ty] = toPage(game.cur.nx, game.cur.ny);
    const r = sizeToPage(game.cfg.size) / 2;
    const now = performance.now();
    if (Math.hypot(x - tx, y - ty) <= r * 1.15) {
      const mt = (now - game.cur.born) / 1000;
      const d = Math.hypot((game.cur.nx - game.cur.from[0]) * W, (game.cur.ny - game.cur.from[1]) * H);
      const id = Math.log2(d / (2 * r) + 1);
      game.combo += 1;
      const pts = 1000 * (180 / game.cfg.size) ** 0.5 * Math.max(0.25, 1 - (mt - 0.5) / 6) * (1 + 0.1 * Math.min(game.combo - 1, 10));
      game.score += pts;
      game.hits.push({ mt, id });
      burst(tx, ty, "62,232,216", 36);
      game.fx.push({ kind: "text", x: tx, y: ty - r - 10, text: `+${Math.round(pts)}`, t0: now });
      sfx.success();
      game.last = [game.cur.nx, game.cur.ny];
      game.cur = null;
      game.gap = now + 350;
    } else {
      game.combo = 0;
      burst(x, y, "255,77,106", 10);
      sfx.fail();
    }
  }
}

function handleResultsClick(p) {
  const [x, y] = toPage(p[0], p[1]);
  for (const b of game.buttons || []) {
    if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
      b.id === "retry" ? restart() : closeTrainer();
    }
  }
}

function burst(x, y, col, n) {
  const t0 = performance.now();
  for (let i = 0; i < n; i++) {
    const a = Math.random() * Math.PI * 2, v = 80 + Math.random() * 320;
    game.fx.push({ kind: "p", x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, col, t0 });
  }
  game.fx.push({ kind: "ring", x, y, col, t0 });
}

// ------------------------------------------------------------------ draw

function draw(now) {
  if (!active || !game) return;
  raf = requestAnimationFrame(draw);
  g.fillStyle = "#04070b";
  g.fillRect(0, 0, W, H);
  // perspective grid floor
  g.strokeStyle = "rgba(62,232,216,0.05)";
  g.lineWidth = 1;
  for (let x = 0; x < W; x += 80) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
  for (let y = 0; y < H; y += 80) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  const { cfg } = game;
  if (game.state === "countdown") {
    const k = (now - game.t0) / 1000;
    const n = 3 - Math.floor(k);
    text(W / 2, H * 0.36, cfg.label, 22, "#8ff8ee", 900, ".5em");
    text(W / 2, H * 0.52, n > 0 ? String(n) : "GO", 140, "#eafffd", 900);
    text(W / 2, H * 0.66, "Hit each target with any Gazer click — dwell, double blink, wink", 16, "#a9c2c9", 500, "0", "Rajdhani");
    text(W / 2, H * 0.7, "ESC to quit", 11, "#6f8796", 700, ".3em");
    if (k >= 3.4) { game.state = "play"; nextTarget(now); }
  } else if (game.state === "play") {
    if (!game.cur && (!game.gap || now >= game.gap)) nextTarget(now);
    if (game.cur) {
      const age = (now - game.cur.born) / 1000;
      if (age > cfg.time) {
        game.misses += 1;
        game.combo = 0;
        const [tx, ty] = toPage(game.cur.nx, game.cur.ny);
        burst(tx, ty, "255,181,71", 12);
        game.fx.push({ kind: "text", x: tx, y: ty, text: "MISS", t0: now, col: "#ffb547" });
        game.last = [game.cur.nx, game.cur.ny];
        game.cur = null;
        game.gap = now + 350;
      } else drawTarget(game.cur, age, now);
    }
    hud();
  } else if (game.state === "results") {
    results(now);
  }
  drawGaze(now);
  drawFx(now);
}

function drawTarget(t, age, now) {
  const [x, y] = toPage(t.nx, t.ny);
  const r = sizeToPage(game.cfg.size) / 2;
  const intro = Math.min(1, age / 0.25);
  const pulse = 1 + Math.sin(now / 160) * 0.04;
  g.save();
  g.translate(x, y);
  g.scale(intro * pulse, intro * pulse);
  const gr = g.createRadialGradient(0, 0, 0, 0, 0, r * 1.8);
  gr.addColorStop(0, "rgba(62,232,216,0.35)");
  gr.addColorStop(1, "rgba(62,232,216,0)");
  g.fillStyle = gr;
  g.beginPath(); g.arc(0, 0, r * 1.8, 0, Math.PI * 2); g.fill();
  for (let i = 0; i < 3; i++) {
    g.strokeStyle = `rgba(143,248,238,${0.9 - i * 0.28})`;
    g.lineWidth = 2.5 - i * 0.6;
    g.beginPath(); g.arc(0, 0, r * (1 - i * 0.3), 0, Math.PI * 2); g.stroke();
  }
  // time-left arc
  const left = 1 - age / game.cfg.time;
  g.strokeStyle = left < 0.3 ? "#ffb547" : "rgba(255,255,255,0.8)";
  g.lineWidth = 3;
  g.beginPath(); g.arc(0, 0, r + 8, -Math.PI / 2, -Math.PI / 2 + left * Math.PI * 2); g.stroke();
  g.fillStyle = "#f2fffd";
  g.beginPath(); g.arc(0, 0, Math.max(3, r * 0.08), 0, Math.PI * 2); g.fill();
  g.restore();
}

function drawGaze(now) {
  const f = frame;
  if (!f) return;
  if (f.gaze && f.face) {
    const [x, y] = toPage(f.gaze[0], f.gaze[1]);
    const gr = g.createRadialGradient(x, y, 0, x, y, 30);
    gr.addColorStop(0, "rgba(200,185,255,0.8)");
    gr.addColorStop(1, "rgba(155,124,255,0)");
    g.fillStyle = gr;
    g.beginPath(); g.arc(x, y, 30, 0, Math.PI * 2); g.fill();
  }
  if (f.pointer) {
    const [x, y] = toPage(f.pointer[0], f.pointer[1]);
    g.strokeStyle = "rgba(143,248,238,0.9)";
    g.lineWidth = 2;
    const a0 = now / 400;
    for (let i = 0; i < 4; i++) { g.beginPath(); g.arc(x, y, 16, a0 + i * Math.PI / 2 + 0.2, a0 + (i + 1) * Math.PI / 2 - 0.2); g.stroke(); }
    if (f.dwell_on && f.dwell > 0.02) {
      g.strokeStyle = "#f2fffd";
      g.lineWidth = 3;
      g.beginPath(); g.arc(x, y, 24, -Math.PI / 2, -Math.PI / 2 + f.dwell * Math.PI * 2); g.stroke();
    }
  }
}

function hud() {
  const { cfg } = game;
  text(W / 2, 34, `${cfg.label} · TARGET ${Math.min(game.i + 1, cfg.count)} / ${cfg.count}`, 11, "#6f8796", 700, ".3em");
  text(W / 2, 72, Math.round(game.score).toLocaleString(), 34, "#eafffd", 900);
  if (game.combo > 1) text(W / 2, 100, `COMBO ×${game.combo}`, 11, "#9b7cff", 800, ".3em");
}

function results(now) {
  const r = game.result;
  const k = Math.min(1, (now - game.resultsAt) / 600);
  const grade = r.accuracy >= 0.9 && r.mean < 2.2 ? "ACE" : r.accuracy >= 0.75 ? "SHARP" : r.accuracy >= 0.5 ? "STEADY" : "KEEP TRAINING";
  const col = grade === "ACE" ? "#4dffa6" : grade === "SHARP" ? "#3ee8d8" : grade === "STEADY" ? "#ffb547" : "#ff4d6a";
  g.globalAlpha = k;
  text(W / 2, H * 0.2, `${game.cfg.label} COMPLETE`, 12, "#6f8796", 700, ".4em");
  text(W / 2, H * 0.32, grade, 76, col, 900, ".08em");
  text(W / 2, H * 0.42, Math.round(r.score).toLocaleString() + " PTS", 30, "#eafffd", 900, ".1em");
  const stats = [["ACCURACY", `${Math.round(r.accuracy * 100)}%`], ["TIME TO HIT", `${r.mean.toFixed(2)} s`],
    ["THROUGHPUT", `${r.tp.toFixed(2)} bit/s`], ["BEST", game.prevBest ? Math.round(Math.max(game.prevBest.score, r.score)).toLocaleString() : "NEW"]];
  stats.forEach(([l, v], i) => {
    const x = W / 2 + (i - 1.5) * Math.min(220, W / 5);
    text(x, H * 0.52, v, 26, "#8ff8ee", 700, "0", "JetBrains Mono");
    text(x, H * 0.56, l, 9, "#6f8796", 700, ".25em");
  });
  const bw = Math.min(260, W * 0.2), bh = 70, y = H * 0.68;
  game.buttons = [{ id: "retry", x: W / 2 - bw - 20, y, w: bw, h: bh, label: "RETRY" }, { id: "done", x: W / 2 + 20, y, w: bw, h: bh, label: "DONE" }];
  for (const b of game.buttons) {
    const hover = frame?.pointer && (() => { const [px, py] = toPage(frame.pointer[0], frame.pointer[1]); return px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h; })();
    g.fillStyle = hover ? "rgba(62,232,216,0.25)" : "rgba(6,16,22,0.9)";
    g.fillRect(b.x, b.y, b.w, b.h);
    g.strokeStyle = hover ? "#8ff8ee" : "rgba(110,240,230,0.5)";
    g.lineWidth = 1.5;
    g.strokeRect(b.x, b.y, b.w, b.h);
    text(b.x + b.w / 2, b.y + bh / 2 + 5, b.label, 14, "#eafffd", 800, ".25em");
  }
  text(W / 2, H * 0.84, "CLICK A BUTTON WITH YOUR EYES · ENTER RETRY · ESC QUIT", 10, "#6f8796", 700, ".25em");
  g.globalAlpha = 1;
  if (store.state?.app.demo && now - game.resultsAt > 6000) closeTrainer();
}

function drawFx(now) {
  game.fx = game.fx.filter((f) => now - f.t0 < 900);
  for (const f of game.fx) {
    const k = (now - f.t0) / 900;
    if (f.kind === "p") {
      const t = (now - f.t0) / 1000;
      g.fillStyle = `rgba(${f.col},${1 - k})`;
      g.fillRect(f.x + f.vx * t, f.y + f.vy * t + 200 * t * t, 3, 3);
    } else if (f.kind === "ring") {
      g.strokeStyle = `rgba(${f.col},${1 - k})`;
      g.lineWidth = 3 * (1 - k) + 0.5;
      g.beginPath(); g.arc(f.x, f.y, 20 + k * 140, 0, Math.PI * 2); g.stroke();
    } else if (f.kind === "text") {
      g.globalAlpha = 1 - k;
      text(f.x, f.y - k * 50, f.text, 18, f.col || "#8ff8ee", 900);
      g.globalAlpha = 1;
    }
  }
}

function text(x, y, s, size, color, weight = 700, spacing = ".1em", family = "Orbitron") {
  g.font = `${weight} ${size}px ${family}, sans-serif`;
  g.fillStyle = color;
  g.textAlign = "center";
  if ("letterSpacing" in g) g.letterSpacing = spacing === "0" ? "0px" : spacing;
  g.fillText(s, x, y);
  if ("letterSpacing" in g) g.letterSpacing = "0px";
}
