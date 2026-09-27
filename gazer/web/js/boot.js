// Cinematic boot: hex rain, iris assembling from particles, systems check, iris-wipe reveal.

import { sfx } from "./sound.js";

export function runBoot({ lines, skip = false }) {
  return new Promise((resolve) => {
    const root = document.getElementById("boot");
    if (skip) { resolve(); return; }
    root.hidden = false;
    root.innerHTML = "";
    const canvas = document.createElement("canvas");
    const log = document.createElement("div");
    log.className = "boot-log";
    const title = document.createElement("div");
    title.className = "boot-title";
    title.innerHTML = '<div class="bt-main">GAZER</div><div class="bt-sub">NEURAL OCULAR INTERFACE</div>';
    const skipEl = document.createElement("div");
    skipEl.className = "boot-skip";
    skipEl.textContent = "CLICK OR PRESS ANY KEY TO SKIP";
    root.append(canvas, title, log, skipEl);
    const g = canvas.getContext("2d");
    let W = 0, H = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      W = window.innerWidth; H = window.innerHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    // particles that fly in and settle on iris rings
    const N = 1400;
    const parts = [];
    for (let i = 0; i < N; i++) {
      const ring = i % 7;
      const a = Math.random() * Math.PI * 2;
      const r = ring < 3 ? 60 + ring * 22 + Math.random() * 6 : ring < 6 ? 150 + Math.random() * 40 : 20 + Math.random() * 20;
      parts.push({
        tx: Math.cos(a) * r, ty: Math.sin(a) * r * (ring === 6 ? 1 : 1),
        x: (Math.random() - 0.5) * W * 1.6, y: (Math.random() - 0.5) * H * 1.6,
        d: 0.2 + Math.random() * 0.9, hue: ring < 3 ? "155,124,255" : ring < 6 ? "62,232,216" : "230,255,252",
        s: Math.random() * 1.6 + 0.6,
      });
    }
    const cols = Math.ceil(W / 18);
    const drops = Array.from({ length: cols }, () => Math.random() * -H);
    const hex = "0123456789ABCDEF";
    let t = 0;
    let last = null;
    let done = false;
    const lineAt = [0.9, 1.25, 1.6, 1.95, 2.3, 2.65, 3.0];
    let shown = 0;

    sfx.boot();

    const finish = () => {
      if (done) return;
      done = true;
      root.classList.add("out");
      window.removeEventListener("keydown", finish);
      root.removeEventListener("pointerdown", finish);
      setTimeout(() => { root.hidden = true; root.classList.remove("out"); root.innerHTML = ""; }, 900);
      resolve();
    };
    window.addEventListener("keydown", finish);
    root.addEventListener("pointerdown", finish);

    const step = (now) => {
      if (done) return;
      // frame-clamped clock: a slow first frame can't skip the whole sequence
      t += last == null ? 0 : Math.min((now - last) / 1000, 1 / 20);
      last = now;
      g.fillStyle = "rgba(0,0,0,0.22)";
      g.fillRect(0, 0, W, H);
      // hex rain (fades out as the iris forms)
      g.font = "12px JetBrains Mono, monospace";
      const rainA = Math.max(0, 0.35 - t * 0.08);
      for (let i = 0; i < cols; i++) {
        const y = drops[i];
        g.fillStyle = `rgba(62,232,216,${rainA * (0.4 + Math.random() * 0.6)})`;
        g.fillText(hex[(Math.random() * 16) | 0], i * 18, y);
        drops[i] = y > H + Math.random() * 400 ? 0 : y + 16;
      }
      // iris
      const cx = W / 2, cy = H / 2;
      const k = Math.min(1, t / 1.6);
      const e = 1 - Math.pow(1 - k, 3);
      const spin = t * 0.35;
      for (const p of parts) {
        const pk = Math.min(1, e * (1.2 - p.d * 0.3));
        const ca = Math.cos(spin * (p.tx > 0 ? 1 : -1) * 0.2), sa = Math.sin(spin * 0.2);
        const tx = p.tx * ca - p.ty * sa, ty = p.tx * sa + p.ty * ca;
        const x = cx + p.x + (tx - p.x) * pk, y = cy + p.y + (ty - p.y) * pk;
        g.fillStyle = `rgba(${p.hue},${0.25 + pk * 0.6})`;
        g.fillRect(x, y, p.s, p.s);
      }
      if (t > 1.3) {
        const a = Math.min(1, (t - 1.3) / 0.6);
        g.strokeStyle = `rgba(62,232,216,${a * 0.8})`;
        g.lineWidth = 2;
        g.shadowColor = "#3ee8d8";
        g.shadowBlur = 20;
        g.beginPath();
        const eyeW = 260 + a * 60;
        g.moveTo(cx - eyeW, cy);
        g.quadraticCurveTo(cx, cy - 210 * a, cx + eyeW, cy);
        g.quadraticCurveTo(cx, cy + 210 * a, cx - eyeW, cy);
        g.stroke();
        g.shadowBlur = 0;
      }
      // scan line
      const sy = ((t * 0.9) % 1) * H;
      const grad = g.createLinearGradient(0, sy - 40, 0, sy + 2);
      grad.addColorStop(0, "rgba(62,232,216,0)");
      grad.addColorStop(1, "rgba(62,232,216,0.1)");
      g.fillStyle = grad;
      g.fillRect(0, sy - 40, W, 42);

      if (t > 1.7 && !title.classList.contains("show")) title.classList.add("show");
      while (shown < lines.length && t > lineAt[Math.min(shown, lineAt.length - 1)]) {
        const div = document.createElement("div");
        div.innerHTML = lines[shown];
        log.append(div);
        shown++;
        sfx.blip();
      }
      if (t > 4.2) { finish(); return; }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
