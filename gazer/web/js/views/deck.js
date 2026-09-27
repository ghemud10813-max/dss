import { h, panel, toggle, button, act, store, Spark, onStore } from "../ui.js";
import { sfx } from "../sound.js";

const MODE_SHORT = { hybrid: "HYBRID", gaze: "GAZE", head_mouse: "HEAD", head_joystick: "JOYSTICK", head_absolute: "POINTER" };

function eyeGlyph(label) {
  const svg = `<svg viewBox="0 0 110 60">
    <path class="e-white" d="M5 30Q55 -2 105 30Q55 62 5 30Z"/>
    <g class="e-iris-g"><circle class="e-iris" cx="55" cy="30" r="15"/><circle class="e-pupil" cx="55" cy="30" r="6"/></g>
    <path class="e-lid" d="M5 30Q55 -2 105 30Q55 -2 5 30Z"/>
  </svg>`;
  const el = h("div", { class: "eye-glyph" }, h("div", { html: svg }), h("div", { class: "e-label" }, label), h("div", { class: "e-val" }, "—"));
  const iris = el.querySelector(".e-iris-g");
  const lid = el.querySelector(".e-lid");
  const val = el.querySelector(".e-val");
  return {
    el,
    set(closure, dx, dy) {
      const c = Math.max(0, Math.min(1, closure));
      // lid: top curve drops toward the bottom curve as the eye closes
      const top = -2 + 64 * c;
      lid.setAttribute("d", `M5 30Q55 -2 105 30Q55 ${top.toFixed(1)} 5 30Z`);
      iris.setAttribute("transform", `translate(${(dx * 22).toFixed(1)} ${(dy * 10).toFixed(1)})`);
      val.textContent = `${Math.round((1 - c) * 100)}% OPEN`;
    },
  };
}

export function buildDeck(root, ctx) {
  const view = h("div", { class: "view", id: "view-deck" });
  const left = h("div", { class: "col left" });
  const right = h("div", { class: "col right" });
  const center = h("div", { class: "center" });
  view.append(left, center, right);
  root.append(view);

  // ---------------------------------------------------------- optical feed
  const feedP = panel({ title: "OPTICAL FEED", code: "//01", tag: "LIVE", tagClass: "live" });
  const img = h("img", { alt: "" });
  const feedEmpty = h("div", { class: "feed-empty" }, "AWAITING SIGNAL");
  const feedInfo = h("div", { class: "feed-info" }, h("span"), h("span"));
  const feed = h("div", { class: "feed" }, feedEmpty, img, h("div", { class: "feed-sweep" }), h("div", { class: "feed-rec" }, "REC"),
    h("i", { class: "corner c1" }), h("i", { class: "corner c2" }), h("i", { class: "corner c3" }), feedInfo);
  feedP.add(feed);
  left.append(feedP.el);
  let lastUrl = null;
  ctx.onPreview((blob) => {
    const url = URL.createObjectURL(blob);
    img.onload = () => { if (lastUrl) URL.revokeObjectURL(lastUrl); lastUrl = url; };
    img.src = url;
    feedEmpty.style.display = "none";
  });

  // ------------------------------------------------------------- telemetry
  const telP = panel({ title: "TELEMETRY", code: "//02" });
  const mk = (label, unit, color, max) => {
    const val = h("div", { class: "m-val" }, "—");
    const cv = h("canvas");
    const el = h("div", { class: "metric" }, val, h("div", { class: "m-label" }, label), cv);
    return { el, val, unit, spark: new Spark(cv, { color, max }) };
  };
  const mFps = mk("TRACK FPS", "", "#3ee8d8", 40);
  const mLat = mk("LATENCY", "ms", "#9b7cff", null);
  const mQ = mk("SIGNAL", "%", "#4dffa6", 100);
  telP.add(h("div", { class: "telemetry" }, mFps.el, mLat.el, mQ.el));
  left.append(telP.el);

  // ---------------------------------------------------------------- ocular
  const ocP = panel({ title: "OCULAR", code: "//03" });
  const eyeL = eyeGlyph("LEFT");
  const eyeR = eyeGlyph("RIGHT");
  const blinkRate = h("span", { class: "big" }, "—");
  const blinkCount = h("span", { class: "mono muted" }, "");
  ocP.add(h("div", { class: "eyes-duo" }, eyeL.el, eyeR.el),
    h("div", { class: "blink-row" }, h("span", { class: "muted" }, "BLINKS / MIN"), blinkRate),
    h("div", { class: "blink-row" }, h("span", { class: "muted" }, "HEALTHY 12–20 · SCREEN USE OFTEN < 8"), blinkCount));
  left.append(ocP.el);

  // ---------------------------------------------------------------- engage
  const engP = panel({ title: "CONTROL", code: "//04", hot: true });
  const reactor = h("button", { class: "reactor", type: "button", "aria-label": "Engage eye control", html: `
    <svg viewBox="0 0 200 200">
      <circle class="r-ring" cx="100" cy="100" r="96" stroke-width="1" stroke-dasharray="2 6"/>
      <g class="r-spin"><circle class="r-ring" cx="100" cy="100" r="86" stroke-width="3" stroke-dasharray="60 30 10 30"/></g>
      <g class="r-spin2"><circle class="r-ring" cx="100" cy="100" r="76" stroke-width="1.5" stroke-dasharray="4 4"/></g>
    </svg>
    <div class="r-core"><div><div class="r-label">ENGAGE</div><div class="r-sub">EYE CONTROL</div></div></div>` });
  const rLabel = reactor.querySelector(".r-label");
  const rSub = reactor.querySelector(".r-sub");
  reactor.addEventListener("click", () => {
    const on = !(store.frame?.control);
    on ? sfx.engage() : sfx.disengage();
    act("control", { on }).catch(() => {});
  });
  const pauseBtn = button("PAUSE", () => act("pause", {}).catch(() => {}));
  engP.add(h("div", { class: "reactor-wrap" }, reactor),
    h("div", { class: "btn-row" }, pauseBtn,
      button("RECENTER", () => act("perform", { action: "recenter" }).catch(() => {})),
      button("KEYBOARD", () => act("keyboard", {}).catch(() => {}), "violet")));
  right.append(engP.el);

  // ------------------------------------------------------------- style
  const styP = panel({ title: "CONTROL STYLE", code: "//05" });
  const choices = h("div", { class: "choices" });
  const styleEls = {};
  const modeChips = h("div", { class: "mode-chips" });
  const chipEls = {};
  styP.add(choices, modeChips);
  right.append(styP.el);
  onStore("catalog", (cat) => {
    choices.innerHTML = "";
    for (const [k, v] of Object.entries(cat.styles)) {
      const c = h("button", { class: "choice", type: "button" },
        h("div", { class: "choice-title" }, v.label.toUpperCase(), k === "eyes" ? h("span", { class: "badge" }, "RECOMMENDED") : null),
        h("div", { class: "choice-desc" }, v.desc));
      c.addEventListener("click", () => { sfx.toggleOn(); act("style", { style: k }).catch(() => {}); });
      styleEls[k] = c;
      choices.append(c);
    }
    modeChips.innerHTML = "";
    for (const m of cat.mode_order) {
      const b = h("button", { class: "mode-chip", type: "button", title: cat.modes[m] }, MODE_SHORT[m] || m);
      b.addEventListener("click", () => { sfx.tap(); act("mode", { mode: m }).catch(() => {}); });
      chipEls[m] = b;
      modeChips.append(b);
    }
  });

  // ------------------------------------------------------------- quick
  const qP = panel({ title: "QUICK SYSTEMS", code: "//06" });
  qP.add(
    toggle({ label: "Dwell click", sub: "Hold your gaze still to click", path: "dwell.enabled" }).el,
    toggle({ label: "Gaze zones", sub: "Glance past screen edges to scroll & act", path: "zones.enabled" }).el,
    toggle({ label: "Learn from my clicks", sub: "Accuracy improves the more you use it", path: "pointer.adaptive_learning" }).el,
    toggle({ label: "Show gaze dot on desktop", path: "overlay.show_gaze_dot" }).el,
  );
  right.append(qP.el);

  const demoP = panel({ title: "SIMULATED PILOT", code: "//07", tag: "DEMO", tagClass: "warn",
    desc: "No webcam? A synthetic user drives the real engine. Make them perform a gesture:" });
  const demoBtns = h("div", { class: "grid-3" });
  for (const [name, label] of [["smile", "SMILE"], ["double_blink", "2× BLINK"], ["wink_left", "WINK L"],
    ["wink_right", "WINK R"], ["brow_raise", "BROWS"], ["mouth_open", "JAW"]]) {
    demoBtns.append(button(label, () => {
      if (name === "double_blink") {
        act("demo_gesture", { name, length: 0.16 }).catch(() => {});
        setTimeout(() => act("demo_gesture", { name, length: 0.16 }).catch(() => {}), 300);
      } else act("demo_gesture", { name, length: name.startsWith("wink") ? 0.5 : 0.9 }).catch(() => {});
    }, "small"));
  }
  demoP.add(demoBtns);
  demoP.el.style.display = "none";
  right.append(demoP.el);

  // --------------------------------------------------------------- center
  const modeLbl = h("div", { class: "deck-mode" });
  const banner = h("div", { class: "deck-banner" },
    h("div", { class: "txt" }, "EYES NOT CALIBRATED", h("small", {}, "Head control is active until you calibrate — takes ~50 seconds.")),
    button("CALIBRATE", () => ctx.startCalibration("gaze", "standard"), "primary small"));
  banner.style.display = "none";
  center.append(h("div", { class: "deck-hero" }, modeLbl), banner, h("div", { class: "center-spacer" }));

  const eqP = panel({ title: "FACIAL GESTURE SPECTRUM", code: "//08", cls: "eq-panel" });
  const eq = h("div", { class: "eq" });
  eqP.add(eq);
  center.append(eqP.el);
  const bars = {};
  onStore("catalog", (cat) => {
    eq.innerHTML = "";
    for (const [k, v] of Object.entries(cat.gestures)) {
      const fill = h("div", { class: "eq-fill" });
      const thr = h("div", { class: "eq-thr" });
      const bar = h("div", { class: "eq-bar", title: `${v.label} — ${v.desc}` }, h("div", { class: "eq-track" }, fill, thr),
        h("div", { class: "eq-name" }, v.label.toUpperCase()));
      bars[k] = { bar, fill, thr, v: 0 };
      eq.append(bar);
    }
  });

  // --------------------------------------------------------------- update
  let lastActive = new Set();
  const api = {
    el: view,
    onState(s) {
      const set = s.profile.settings;
      for (const [k, el] of Object.entries(styleEls)) el.classList.toggle("on", set.style === k);
      const bound = new Set(set.gestures.bindings.filter((b) => b.action !== "none").map((b) => b.gesture));
      for (const [k, b] of Object.entries(bars)) {
        const g = set.gestures.gestures[k];
        b.thr.style.bottom = `${((g?.threshold ?? 0.5) * 100).toFixed(1)}%`;
        b.bar.classList.toggle("bound", bound.has(k));
        b.bar.style.opacity = g && !g.enabled ? 0.35 : 1;
      }
      banner.style.display = s.profile.gaze.ready ? "none" : "";
      demoP.el.style.display = s.app.demo ? "" : "none";
      feedP.tagEl.textContent = s.app.demo ? "SIMULATED" : "LIVE";
    },
    onFrame(f) {
      reactor.classList.toggle("on", f.control && !f.paused);
      reactor.classList.toggle("paused", f.control && f.paused);
      rLabel.textContent = !f.control ? "ENGAGE" : f.paused ? "PAUSED" : "ACTIVE";
      rSub.textContent = !f.control ? "EYE CONTROL" : f.paused ? "LONG BLINK TO RESUME" : "TAP TO DISENGAGE";
      pauseBtn.textContent = f.paused ? "RESUME" : "PAUSE";
      pauseBtn.disabled = !f.control;
      for (const [m, b] of Object.entries(chipEls)) {
        b.classList.toggle("on", f.mode === m);
        b.classList.toggle("fallback", f.mode_eff === m && f.mode !== m);
      }
      const fb = f.mode !== f.mode_eff ? ` <span style="color:var(--amber)">→ ${MODE_SHORT[f.mode_eff]} UNTIL CALIBRATED</span>` : "";
      modeLbl.innerHTML = `MODE // <b>${MODE_SHORT[f.mode] || f.mode}</b>${fb}`;

      mFps.val.innerHTML = `${Math.round(f.fps)}`;
      mLat.val.innerHTML = `${Math.round(f.lat)}<small>ms</small>`;
      mQ.val.innerHTML = f.face ? `${Math.round(f.q * 100)}<small>%</small>` : "—";
      mFps.spark.push(f.fps); mLat.spark.push(f.lat); mQ.spark.push(f.face ? f.q * 100 : 0);
      feedInfo.children[0].textContent = f.face ? "● FACE LOCK" : "○ SEARCHING";
      feedInfo.children[1].textContent = `${Math.round(f.cam_fps)} FPS · ${f.src.toUpperCase()}`;

      const gz = f.gaze || [0.5, 0.5];
      const dx = (gz[0] - 0.5) * 2, dy = (gz[1] - 0.5) * 2;
      eyeL.set(f.closure[0], dx, dy);
      eyeR.set(f.closure[1], dx, dy);

      const act2 = new Set(f.ga);
      for (const [k, b] of Object.entries(bars)) {
        const v = f.gv[k] ?? 0;
        b.v += (v - b.v) * 0.5;
        b.fill.style.height = `${(b.v * 100).toFixed(1)}%`;
        const on = act2.has(k) || (k === "double_blink" && v > 0.5);
        b.bar.classList.toggle("active", on);
        if (on && !lastActive.has(k)) sfx.blip();
      }
      lastActive = act2;
    },
    onInsights(ins) {
      blinkRate.textContent = ins.blink_rate == null ? "—" : ins.blink_rate.toFixed(0);
      blinkRate.style.color = ins.blink_rate == null ? "" : ins.blink_rate < 8 ? "var(--amber)" : "var(--green)";
      blinkCount.textContent = `${ins.blinks} total`;
    },
    tick() { mFps.spark.draw(); mLat.spark.draw(); mQ.spark.draw(); },
    callouts(stage, f) {
      const out = [];
      if (!f) return out;
      if (stage.anchors.head?.visible && f.face) {
        out.push({ at: stage.anchors.head, dx: -90, dy: -46, title: "HEAD POSE",
          val: `Y ${f.head[0].toFixed(1)}°  P ${f.head[1].toFixed(1)}°  R ${f.head[2].toFixed(1)}°` });
      }
      if (stage.anchors.gaze?.visible && f.gaze) {
        out.push({ at: stage.anchors.gaze, dx: 70, dy: -60, title: "GAZE LOCK",
          val: `${Math.round(f.gaze[0] * (store.state?.screen.w || 1920))}, ${Math.round(f.gaze[1] * (store.state?.screen.h || 1080))} px` });
      }
      if (stage.anchors.screen?.visible) {
        out.push({ at: stage.anchors.screen, dx: -60, dy: 34, title: "VIRTUAL DISPLAY",
          val: `${store.state?.screen.w || "—"} × ${store.state?.screen.h || "—"}${f.zone ? ` · ZONE ${f.zone[0].toUpperCase()}` : ""}` });
      }
      return out;
    },
  };
  return api;
}
