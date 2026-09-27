import { h, panel, slider, toggle, button, act, fmtDur } from "../ui.js";

function ring(color) {
  const R = 58, C = 2 * Math.PI * R;
  const el = h("div", { class: "gauge", html: `<svg viewBox="0 0 150 150">
    <circle cx="75" cy="75" r="${R}" fill="none" stroke="rgba(110,240,230,.1)" stroke-width="8"/>
    <circle class="arc" cx="75" cy="75" r="${R}" fill="none" stroke="${color}" stroke-width="8" stroke-dasharray="${C}" stroke-dashoffset="${C}"
      transform="rotate(-90 75 75)" style="filter:drop-shadow(0 0 6px ${color});transition:stroke-dashoffset .6s"/>
    <text class="g-t" x="75" y="80" text-anchor="middle">—</text>
    <text class="g-s" x="75" y="100" text-anchor="middle"></text></svg>` });
  const arc = el.querySelector(".arc"), t = el.querySelector(".g-t"), s = el.querySelector(".g-s");
  return { el, set(frac, text, sub, col) { arc.setAttribute("stroke-dashoffset", C * (1 - Math.max(0, Math.min(1, frac)))); t.textContent = text; s.textContent = sub; if (col) arc.setAttribute("stroke", col); } };
}

export function buildInsights(root, ctx) {
  const view = h("div", { class: "view wide", id: "view-insights" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  const heatP = panel({ title: "GAZE HEATMAP", code: "//I1", hot: true, desc: "Where your eyes spent this session (cursor position in head modes)." });
  const cv = h("canvas");
  heatP.add(h("div", { class: "heat-wrap" }, cv), h("div", { class: "btn-row", style: { marginTop: "10px" } },
    button("RESET SESSION MAP", () => act("reset_heatmap").catch(() => {}), "small")));
  left.append(heatP.el);

  const vis = panel({ title: "VISUAL BEHAVIOUR", code: "//I2" });
  const tile = (label, unit = "") => { const v = h("div", { class: "t-val" }, "—"); return { el: h("div", { class: "tile" }, v, h("div", { class: "t-lbl" }, label)), v, unit }; };
  const tFix = tile("FIXATIONS / MIN"), tDur = tile("MEAN FIXATION"), tSac = tile("SACCADES / MIN"),
    tRead = tile("READING TIME"), tFace = tile("EYES ON SCREEN"), tBl = tile("BLINKS");
  vis.add(h("div", { class: "tiles" }, tFix.el, tDur.el, tSac.el, tRead.el, tFace.el, tBl.el));
  left.append(vis.el);

  const well = panel({ title: "EYE HEALTH", code: "//I3",
    desc: "Staring at screens cuts blinking from ~15–20/min to ~5–7/min. Gazer watches your blink rate and reminds you to follow 20-20-20." });
  const gBlink = ring("#4dffa6");
  const gBreak = ring("#3ee8d8");
  well.add(h("div", { class: "gauge-row" }, gBlink.el, gBreak.el),
    toggle({ label: "Wellness reminders", path: "wellness.enabled" }).el,
    slider({ label: "Break every", path: "wellness.break_interval_min", min: 5, max: 60, step: 1, fmt: (v) => `${v} min` }).el,
    slider({ label: "Low-blink alert below", path: "wellness.low_blink_per_min", min: 3, max: 15, step: 1, fmt: (v) => `${v}/min` }).el);
  right.append(well.el);

  const usage = panel({ title: "PILOT RECORD", code: "//I4" });
  const tiles2 = h("div", { class: "tiles" });
  usage.add(tiles2);
  right.append(usage.el);

  function drawHeat(heat) {
    const w = cv.clientWidth, hh = cv.clientHeight;
    if (!w || !heat) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = hh * dpr;
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#02060a"; g.fillRect(0, 0, w, hh);
    g.strokeStyle = "rgba(110,240,230,0.05)";
    for (let i = 1; i < 16; i++) { g.beginPath(); g.moveTo((w * i) / 16, 0); g.lineTo((w * i) / 16, hh); g.stroke(); }
    for (let i = 1; i < 9; i++) { g.beginPath(); g.moveTo(0, (hh * i) / 9); g.lineTo(w, (hh * i) / 9); g.stroke(); }
    const cw = w / heat.w, ch = hh / heat.h;
    g.globalCompositeOperation = "lighter";
    for (let y = 0; y < heat.h; y++) for (let x = 0; x < heat.w; x++) {
      const v = heat.data[y * heat.w + x] / 255;
      if (v < 0.03) continue;
      const cx = (x + 0.5) * cw, cy = (y + 0.5) * ch, r = cw * 2.2;
      const col = v < 0.5 ? `62,232,216` : v < 0.8 ? `155,124,255` : `255,181,71`;
      const gr = g.createRadialGradient(cx, cy, 0, cx, cy, r);
      gr.addColorStop(0, `rgba(${col},${0.55 * v})`);
      gr.addColorStop(1, `rgba(${col},0)`);
      g.fillStyle = gr;
      g.fillRect(cx - r, cy - r, 2 * r, 2 * r);
    }
    g.globalCompositeOperation = "source-over";
  }

  let lastIns = null;
  return {
    el: view,
    enter() { drawHeat(lastIns?.heat); },
    onInsights(ins) {
      lastIns = ins;
      if (view.classList.contains("active")) drawHeat(ins.heat);
      tFix.v.textContent = ins.fix_per_min.toFixed(0);
      tDur.v.innerHTML = `${ins.fix_mean_ms.toFixed(0)}<small>ms</small>`;
      tSac.v.textContent = ins.saccades_per_min.toFixed(0);
      tRead.v.textContent = fmtDur(ins.reading_s);
      tFace.v.textContent = fmtDur(ins.face_s);
      tBl.v.textContent = ins.blinks;
      const br = ins.blink_rate;
      gBlink.set(br == null ? 0 : br / 25, br == null ? "—" : br.toFixed(0), "BLINKS / MIN",
        br == null ? null : br < 8 ? "#ffb547" : "#4dffa6");
      const frac = ins.screen_s / Math.max(ins.break_interval_s, 1);
      gBreak.set(frac, fmtDur(Math.max(0, ins.break_interval_s - ins.screen_s)), "UNTIL 20-20-20", frac >= 1 ? "#ffb547" : "#3ee8d8");
    },
    onState(s) {
      const st = s.profile.stats;
      tiles2.innerHTML = "";
      for (const [label, val] of [["CLICKS", st.clicks], ["GESTURES", st.gestures], ["KEYS TYPED", st.keys_typed],
        ["MINUTES ACTIVE", Math.round(st.seconds_active / 60)], ["SESSIONS", st.sessions], ["LEARNED SAMPLES", st.implicit_samples]]) {
        tiles2.append(h("div", { class: "tile" }, h("div", { class: "t-val" }, String(val)), h("div", { class: "t-lbl" }, label)));
      }
      void ctx;
    },
  };
}
