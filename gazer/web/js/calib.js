// Fullscreen calibration display. All logic runs in the engine; this renders
// the session view (sent with every frame) with its own 60 fps clock.

import { request } from "./net.js";
import { sfx } from "./sound.js";

const layer = () => document.getElementById("calib-layer");

let active = false;
let nativeWindow = false;
let canvas, g, ui;
let view = null;
let recvAt = 0;
let lastIndex = null;
let lastState = null;
let lastCaptured = 0;
let fx = [];
let orbit = [];
let W = 0, H = 0;
let raf = 0;
let readySent = false;
let onClose = null;

export function isCalibrating() { return active; }

export function openCalibration({ native = false, closeCb = null } = {}) {
  if (active) return;
  active = true;
  nativeWindow = native;
  onClose = closeCb;
  readySent = false;
  view = null;
  lastIndex = null;
  lastState = null;
  fx = [];
  orbit = Array.from({ length: 46 }, (_, i) => ({ a: (i / 46) * Math.PI * 2, r: 1 + Math.random() * 0.8, s: 0.6 + Math.random() }));
  const L = layer();
  L.hidden = false;
  L.innerHTML = "";
  canvas = document.createElement("canvas");
  ui = document.createElement("div");
  ui.className = "calib-ui";
  L.append(canvas, ui);
  g = canvas.getContext("2d");
  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("keydown", onKey, true);
  const sendReady = () => {
    if (readySent) return;
    readySent = true;
    setTimeout(() => request("calib", { what: "ready" }).catch(() => {}), 350);
  };
  if (native || document.fullscreenElement) {
    sendReady();
  } else {
    L.requestFullscreen?.().then(sendReady).catch(sendReady);
    setTimeout(sendReady, 1500);
  }
  raf = requestAnimationFrame(draw);
}

export function closeCalibration() {
  if (!active) return;
  active = false;
  cancelAnimationFrame(raf);
  window.removeEventListener("resize", resize);
  window.removeEventListener("keydown", onKey, true);
  const L = layer();
  L.hidden = true;
  L.innerHTML = "";
  if (document.fullscreenElement && !nativeWindow) document.exitFullscreen().catch(() => {});
  if (onClose) onClose();
}

export function updateCalibration(v) {
  if (!active) return;
  const prev = view;
  view = v;
  recvAt = performance.now();
  if (v.state !== lastState) {
    if (v.state === "results") (v.good ? sfx.success : sfx.fail)();
    if (v.state === "run" || v.state === "validate") sfx.nav();
    lastState = v.state;
    renderUI(true);
  } else {
    renderUI(false);
  }
  if ((v.state === "run" || v.state === "validate") && v.index !== lastIndex) {
    if (lastIndex != null && prev && prev.target) {
      fx.push({ kind: "shock", x: prev.target[0], y: prev.target[1], t0: performance.now() });
      if (v.target) fx.push({ kind: "streak", x: prev.target[0], y: prev.target[1], x2: v.target[0], y2: v.target[1], t0: performance.now() });
      sfx.point();
    }
    lastIndex = v.index;
    lastCaptured = 0;
  }
  if (v.captured > lastCaptured && v.target) {
    if (v.captured % 3 === 0) {
      fx.push({ kind: "spark", x: v.target[0], y: v.target[1], t0: performance.now(), a: Math.random() * 6.28 });
      sfx.capture();
    }
    lastCaptured = v.captured;
  }
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  W = window.innerWidth; H = window.innerHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function onKey(e) {
  if (!active) return;
  const k = e.key;
  if (k === "Escape") { request("calib", { what: "cancel" }).catch(() => {}); e.preventDefault(); }
  else if (k === " " || k === "Enter") {
    if (view?.state === "intro" || view?.state === "waiting") request("calib", { what: "skip" }).catch(() => {});
    else if (view?.state === "results" && view.good) request("calib", { what: "accept" }).catch(() => {});
    e.preventDefault();
  } else if ((k === "r" || k === "R") && view?.state === "results") request("calib", { what: "retry" }).catch(() => {});
}

// ------------------------------------------------------------------ DOM

function el(html) { const d = document.createElement("div"); d.innerHTML = html; return d.firstElementChild; }

function renderUI(structural) {
  const v = view;
  if (!v) return;
  if (structural) {
    ui.innerHTML = "";
    ui.dataset.state = v.state;
  }
  if (v.state === "waiting") {
    if (structural) ui.append(el(`<div class="calib-center"><div class="calib-title">PREPARING OPTICS</div>
      <div class="calib-sub">Entering fullscreen on this display…</div></div>`));
  } else if (v.state === "intro") {
    if (structural) {
      const head = v.kind === "head";
      ui.append(el(`<div class="calib-center">
        <div class="calib-title">${head ? "HEAD RANGE" : "EYE CALIBRATION"}</div>
        <div class="calib-sub">${head
          ? "Turn your head so your <b>nose</b> points at each target.<br>Move comfortably — this sets your range."
          : "Sit as you normally do. Look <b>straight at the centre</b> of each target until it moves on.<br>Blink normally — blinks are filtered out. When asked, keep looking while gently moving your head."}</div>
        <div class="calib-count" data-k="count">3</div>
        <div class="calib-face" data-k="face"></div></div>`));
      ui.append(el(`<div class="calib-keys">SPACE · START NOW &nbsp;&nbsp;·&nbsp;&nbsp; ESC · CANCEL</div>`));
    }
    const c = ui.querySelector('[data-k="count"]');
    if (c) c.textContent = Math.max(1, Math.ceil(v.countdown ?? 3));
    const f = ui.querySelector('[data-k="face"]');
    if (f) { f.textContent = v.face ? "● FACE LOCKED" : "○ NO FACE — LOOK AT THE CAMERA"; f.style.color = v.face ? "var(--green)" : "var(--amber)"; }
  } else if (v.state === "run" || v.state === "validate") {
    if (structural) {
      ui.append(el(`<div class="calib-top"><div class="ct-phase" data-k="phase"></div><div class="ct-bar"><i data-k="bar"></i></div></div>`));
      ui.append(el(`<div class="calib-hint" data-k="hint"></div>`));
      ui.append(el(`<div class="calib-noface" data-k="noface" hidden>SIGNAL LOST — PAUSED UNTIL YOUR FACE IS BACK</div>`));
      ui.append(el(`<div class="calib-keys">ESC · CANCEL</div>`));
    }
    const phase = v.state === "validate" ? "VALIDATING ON UNSEEN POINTS" : v.kind === "head" ? "POINT YOUR NOSE" : "CALIBRATING";
    ui.querySelector('[data-k="phase"]').textContent = `${phase} · ${(v.index ?? 0) + 1} / ${v.total}`;
    ui.querySelector('[data-k="bar"]').style.width = `${(v.progress * 100).toFixed(1)}%`;
    ui.querySelector('[data-k="hint"]').textContent = v.kind === "gaze" && v.hint ? v.hint.toUpperCase() : "";
    ui.querySelector('[data-k="noface"]').hidden = !!v.face;
  } else if (v.state === "training") {
    if (structural) ui.append(el(`<div class="calib-center" style="top:78%"><div class="calib-title" style="font-size:20px">SYNTHESIZING GAZE MODEL</div>
      <div class="calib-sub">Polynomial ridge regression · cross-validated · outliers rejected</div></div>`));
  } else if (v.state === "results") {
    if (structural) buildResults(v);
    updateResults(v);
  }
}

function buildResults(v) {
  const rep = v.report;
  let title, stats, grade = "", cls = "";
  if (v.kind === "head") {
    title = v.head_ok ? "HEAD RANGE LOCKED" : "NOT ENOUGH MOVEMENT";
    stats = v.head_ok ? "Head pointer mode now maps your comfortable range to the whole screen." : (v.error || "");
    cls = v.head_ok ? "grade-Excellent" : "grade-Poor";
  } else if (rep) {
    grade = rep.grade;
    title = rep.grade.toUpperCase();
    cls = `grade-${rep.grade}`;
    stats = `AVG ${rep.mean_px.toFixed(0)} px · ${rep.percent.toFixed(1)}% of width · median ${rep.median_px.toFixed(0)} · p90 ${rep.p90_px.toFixed(0)} · ${v.samples} samples`;
  } else {
    title = "CALIBRATION FAILED";
    stats = v.error || "No usable samples.";
    cls = "grade-Poor";
  }
  ui.append(el(`<div class="calib-center" style="top:36%">
    <div class="calib-grade ${cls}">${title}</div>
    <div class="calib-stats">${stats}</div>
    <div class="calib-sub" data-k="auto"></div></div>`));
  for (const [name, b] of Object.entries(v.buttons || {})) {
    const [x, y, w, h] = b.rect;
    const label = { accept: "SAVE", retry: "RETRY", cancel: "CANCEL" }[name];
    const btn = el(`<div class="calib-btn interactive ${name}" data-b="${name}"><i class="fill"></i><span>${label}</span></div>`);
    Object.assign(btn.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
    btn.addEventListener("click", () => {
      if (name === "accept" && !b.enabled) return;
      request("calib", { what: name }).catch(() => {});
    });
    ui.append(btn);
  }
  ui.append(el(`<div class="calib-keys">LOOK AT A BUTTON TO CHOOSE · ENTER SAVE · R RETRY · ESC CANCEL</div>`));
  void grade;
}

function updateResults(v) {
  const auto = ui.querySelector('[data-k="auto"]');
  if (auto) auto.textContent = v.accept_in != null ? `Saving automatically in ${Math.ceil(v.accept_in)} s` : "";
  for (const [name, b] of Object.entries(v.buttons || {})) {
    const btn = ui.querySelector(`[data-b="${name}"]`);
    if (!btn) continue;
    btn.classList.toggle("hover", b.progress > 0);
    btn.classList.toggle("disabled", !b.enabled);
    btn.querySelector(".fill").style.width = `${b.progress * 100}%`;
  }
}

// --------------------------------------------------------------- canvas

function draw(now) {
  if (!active) return;
  raf = requestAnimationFrame(draw);
  g.fillStyle = "#05080c";
  g.fillRect(0, 0, W, H);
  // faint grid + vignette
  g.strokeStyle = "rgba(62,232,216,0.035)";
  g.lineWidth = 1;
  for (let x = 0; x < W; x += 60) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
  for (let y = 0; y < H; y += 60) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  const vg = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.3, W / 2, H / 2, Math.max(W, H) * 0.75);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(0,0,0,0.6)");
  g.fillStyle = vg;
  g.fillRect(0, 0, W, H);
  const v = view;
  if (!v) return;
  if (v.state === "run" || v.state === "validate") drawTarget(v, now);
  else if (v.state === "training") drawTraining(now);
  else if (v.state === "results") drawResults(v, now);
  else if (v.state === "intro") drawIntro(now);
  drawFx(now);
}

function drawIntro(now) {
  const t = now / 1000;
  g.save();
  g.translate(W / 2, H / 2);
  for (let i = 0; i < 3; i++) {
    g.strokeStyle = `rgba(62,232,216,${0.12 - i * 0.03})`;
    g.lineWidth = 1;
    g.beginPath();
    g.arc(0, 0, 260 + i * 60 + Math.sin(t + i) * 8, t * (0.2 + i * 0.1), t * (0.2 + i * 0.1) + Math.PI * 1.4);
    g.stroke();
  }
  g.restore();
}

function drawTarget(v, now) {
  if (!v.target) return;
  const tIn = Math.min(v.duration, v.t_in + (v.face ? (now - recvAt) / 1000 : 0));
  const x = v.target[0] * W, y = v.target[1] * H;
  const col = v.state === "validate" ? "155,124,255" : "62,232,216";
  // constellation of finished points
  const pts = v.points || [];
  g.strokeStyle = `rgba(${col},0.10)`;
  g.lineWidth = 1;
  g.beginPath();
  for (let i = 0; i < v.index && i < pts.length; i++) {
    const px = pts[i][0] * W, py = pts[i][1] * H;
    i ? g.lineTo(px, py) : g.moveTo(px, py);
  }
  g.stroke();
  for (let i = 0; i < v.index && i < pts.length; i++) {
    g.fillStyle = `rgba(${col},0.45)`;
    g.beginPath();
    g.arc(pts[i][0] * W, pts[i][1] * H, 2.5, 0, Math.PI * 2);
    g.fill();
  }
  const settle = v.settle || 0.45;
  const k = Math.min(tIn / settle, 1);
  const e = 1 - Math.pow(1 - k, 3);
  // imploding ring
  const ring = 64 - 50 * e;
  g.shadowColor = `rgb(${col})`;
  g.shadowBlur = 18;
  g.strokeStyle = `rgba(${col},${0.5 + 0.5 * e})`;
  g.lineWidth = 2.5;
  g.beginPath();
  g.arc(x, y, ring, 0, Math.PI * 2);
  g.stroke();
  // orbiting particles spiralling in
  const t = now / 1000;
  for (const p of orbit) {
    const rr = (ring + 22 * (1 - e)) * p.r * (1 - e * 0.55);
    const a = p.a + t * p.s * (1.5 + e * 3);
    g.fillStyle = `rgba(${col},${0.3 + 0.5 * (1 - e)})`;
    g.fillRect(x + Math.cos(a) * rr - 1, y + Math.sin(a) * rr - 1, 2, 2);
  }
  // capture arc
  if (k >= 1) {
    const prog = Math.min((tIn - settle) / Math.max(v.duration - settle, 1e-3), 1);
    g.strokeStyle = "rgba(240,255,253,0.95)";
    g.lineWidth = 3;
    g.beginPath();
    g.arc(x, y, 22, -Math.PI / 2, -Math.PI / 2 + prog * Math.PI * 2);
    g.stroke();
  }
  g.shadowBlur = 0;
  // precise fixation core
  g.fillStyle = "#f2fffd";
  g.beginPath();
  g.arc(x, y, 5.5, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#000";
  g.beginPath();
  g.arc(x, y, 1.8, 0, Math.PI * 2);
  g.fill();
}

function drawTraining(now) {
  const t = now / 1000;
  const layers = [6, 10, 10, 2];
  const cx = W / 2, cy = H * 0.45, spanX = Math.min(W * 0.5, 620), spanY = 260;
  const nodes = layers.map((n, li) => Array.from({ length: n }, (_, i) => [
    cx - spanX / 2 + (spanX * li) / (layers.length - 1), cy - spanY / 2 + (spanY * (i + 0.5)) / n]));
  for (let li = 0; li < nodes.length - 1; li++) {
    for (const a of nodes[li]) for (const b of nodes[li + 1]) {
      const pulse = (Math.sin(t * 6 + a[1] * 0.05 + b[1] * 0.03 + li) + 1) / 2;
      g.strokeStyle = `rgba(62,232,216,${0.04 + pulse * 0.18})`;
      g.lineWidth = 1;
      g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
    }
  }
  nodes.flat().forEach(([x, y], i) => {
    const p = (Math.sin(t * 5 + i) + 1) / 2;
    g.fillStyle = `rgba(${i % 3 ? "62,232,216" : "155,124,255"},${0.5 + p * 0.5})`;
    g.shadowColor = "#3ee8d8";
    g.shadowBlur = 10 * p;
    g.beginPath(); g.arc(x, y, 4 + p * 2, 0, Math.PI * 2); g.fill();
  });
  g.shadowBlur = 0;
}

function drawResults(v, now) {
  const rep = v.report;
  if (rep) {
    const r = (rep.mean_px / 1920) * W;
    for (const p of rep.points) {
      const tx = p.target[0] * W, ty = p.target[1] * H, mx = p.pred[0] * W, my = p.pred[1] * H;
      g.strokeStyle = "rgba(255,255,255,0.08)";
      g.lineWidth = 1;
      g.beginPath(); g.arc(tx, ty, Math.max(r, 6), 0, Math.PI * 2); g.stroke();
      g.strokeStyle = "rgba(255,181,71,0.9)";
      g.lineWidth = 2;
      g.beginPath(); g.moveTo(tx, ty); g.lineTo(mx, my); g.stroke();
      g.fillStyle = "#f2fffd";
      g.beginPath(); g.arc(tx, ty, 4.5, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#ffb547";
      g.beginPath(); g.arc(mx, my, 3.5, 0, Math.PI * 2); g.fill();
    }
  }
  if (v.gaze) {
    const x = v.gaze[0] * W, y = v.gaze[1] * H;
    const pulse = (Math.sin(now / 150) + 1) / 2;
    const gr = g.createRadialGradient(x, y, 0, x, y, 26);
    gr.addColorStop(0, "rgba(200,185,255,0.95)");
    gr.addColorStop(1, "rgba(155,124,255,0)");
    g.fillStyle = gr;
    g.beginPath(); g.arc(x, y, 26 + pulse * 4, 0, Math.PI * 2); g.fill();
  }
}

function drawFx(now) {
  fx = fx.filter((f) => now - f.t0 < 700);
  for (const f of fx) {
    const k = (now - f.t0) / 700;
    if (f.kind === "shock") {
      g.strokeStyle = `rgba(143,248,238,${1 - k})`;
      g.lineWidth = 2 * (1 - k) + 0.5;
      g.beginPath(); g.arc(f.x * W, f.y * H, 12 + k * 90, 0, Math.PI * 2); g.stroke();
    } else if (f.kind === "streak") {
      const kk = Math.min(1, k * 2.2);
      const x1 = f.x * W, y1 = f.y * H, x2 = f.x2 * W, y2 = f.y2 * H;
      const hx = x1 + (x2 - x1) * kk, hy = y1 + (y2 - y1) * kk;
      const tail = Math.max(0, kk - 0.35);
      const gr = g.createLinearGradient(x1 + (x2 - x1) * tail, y1 + (y2 - y1) * tail, hx, hy);
      gr.addColorStop(0, "rgba(62,232,216,0)");
      gr.addColorStop(1, `rgba(143,248,238,${0.9 * (1 - k)})`);
      g.strokeStyle = gr;
      g.lineWidth = 2;
      g.beginPath(); g.moveTo(x1 + (x2 - x1) * tail, y1 + (y2 - y1) * tail); g.lineTo(hx, hy); g.stroke();
    } else if (f.kind === "spark") {
      const d = 18 + k * 30;
      g.fillStyle = `rgba(240,255,253,${1 - k})`;
      for (let i = 0; i < 3; i++) {
        const a = f.a + i * 2.1;
        g.fillRect(f.x * W + Math.cos(a) * d - 1, f.y * H + Math.sin(a) * d - 1, 2, 2);
      }
    }
  }
}
