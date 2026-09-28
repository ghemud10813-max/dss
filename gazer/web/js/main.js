// GAZER Command Deck — bootstrap, routing, HUD, toasts, callouts, onboarding.

import { connect, on, request, subscribe } from "./net.js";
import { Stage } from "./scene.js";
import { runBoot } from "./boot.js";
import { sfx, setEnabled as setSound, isEnabled as soundOn, unlock } from "./sound.js";
import { openCalibration, closeCalibration, updateCalibration, isCalibrating } from "./calib.js";
import { h, store, setStore, refreshBindings, ICONS, toast, act, button } from "./ui.js";
import { buildDeck } from "./views/deck.js";
import { buildCalibrate } from "./views/calibrate.js";
import { buildGestures } from "./views/gestures.js";
import { buildPointer } from "./views/pointer.js";
import { buildZones } from "./views/zones.js";
import { buildInsights } from "./views/insights.js";
import { buildVoice } from "./views/voice.js";
import { buildSystem } from "./views/system.js";
import { buildTrainer } from "./views/trainer.js";
import { openTrainer, trainerFrame, isTraining } from "./trainer.js";

const CALIB_ONLY = location.hash === "#calib";
const app = document.getElementById("app");
// The 3D stage is eye candy: if WebGL is unavailable (old GPU, remote desktop,
// locked-down browser) every control keeps working on a CSS backdrop.
class NullStage {
  constructor() {
    this.anchors = {};
    this.paused = false;
    this.el = h("div", { class: "flat-display" }, h("div", { class: "fd-label" }, "VIRTUAL DISPLAY · 2D MODE"));
    this.gaze = h("i", { class: "fd-gaze" });
    this.ptr = h("i", { class: "fd-ptr" });
    this.el.append(this.gaze, this.ptr);
    document.body.append(this.el);
  }
  setFrame(f) {
    const place = (el, p) => { el.style.display = p ? "" : "none"; if (p) { el.style.left = `${p[0] * 100}%`; el.style.top = `${p[1] * 100}%`; } };
    place(this.gaze, f.face ? f.gaze : null);
    place(this.ptr, f.pointer);
    this.ptr.classList.toggle("on", !!f.control);
  }
  setView(v) { this.el.style.display = v === "deck" ? "" : "none"; }
  setMeshTopology() {} setHeat() {} setQuality() {} render() {}
}
let stage;
try {
  stage = new Stage(document.getElementById("stage"));
} catch (e) {
  console.warn("3D stage disabled:", e.message);
  stage = new NullStage();
  document.body.classList.add("no-webgl");
  setTimeout(() => showToast("3D disabled — WebGL unavailable; all controls still work", "warn"), 1500);
}
fetch("/assets/face_mesh.json").then((r) => r.json()).then((m) => stage.setMeshTopology(m)).catch(() => {});

// ----------------------------------------------------------------- context

const previewFns = new Set();
const stateFns = new Set();
const ctx = {
  onPreview: (fn) => previewFns.add(fn),
  onState: (fn) => stateFns.add(fn),
  startCalibration,
  startTrainer,
  replayBoot: () => boot(true),
  onboarding: (force) => onboarding(force),
};

// ------------------------------------------------------------------- views

const NAV = [
  ["deck", "DECK", buildDeck], ["calibrate", "CALIBRATE", buildCalibrate], ["gestures", "GESTURES", buildGestures],
  ["pointer", "POINTER", buildPointer], ["zones", "ZONES", buildZones], ["trainer", "TRAINER", buildTrainer],
  ["insights", "INSIGHTS", buildInsights],
  ["voice", "VOICE", buildVoice], ["system", "SYSTEM", buildSystem],
];
const viewsRoot = document.getElementById("views");
const rail = document.getElementById("rail");
const views = {};
const railBtns = {};
NAV.forEach(([key, label, build], i) => {
  views[key] = build(viewsRoot, ctx);
  const b = h("button", { class: "rail-btn", type: "button", title: `${label} (Alt+${i + 1})`, html: `${ICONS[key]}<span>${label}</span>` });
  b.addEventListener("click", () => go(key));
  b.addEventListener("mouseenter", () => sfx.hover());
  railBtns[key] = b;
  rail.append(b);
  if (key === "insights") rail.append(h("div", { class: "rail-spacer" }));
});
let current = null;

function go(key, instant = false) {
  if (!views[key]) key = "deck";
  if (key === current) return;
  if (!instant) sfx.nav();
  for (const [k, v] of Object.entries(views)) {
    v.el.classList.toggle("active", k === key);
    v.el.classList.remove("enter");
  }
  const v = views[key];
  void v.el.offsetWidth;
  v.el.classList.add("enter");
  for (const [k, b] of Object.entries(railBtns)) b.classList.toggle("active", k === key);
  current = key;
  document.body.classList.toggle("dim-stage", key !== "deck");
  stage.setView(key, instant);
  if (history.replaceState) history.replaceState(null, "", `${location.search}#${key}`);
  subscribe({ preview: key === "deck" });
  v.enter && v.enter();
  if (store.state) v.onState && v.onState(store.state);
  document.getElementById("callouts").innerHTML = "";
}

window.addEventListener("keydown", (e) => {
  if (e.altKey && /^[1-9]$/.test(e.key) && NAV[Number(e.key) - 1]) { go(NAV[Number(e.key) - 1][0]); e.preventDefault(); }
});

// ----------------------------------------------------------------- top bar

const chips = document.getElementById("chips");
const chip = (label) => { const el = h("span", { class: "chip" }, h("i"), label, " ", h("b", {}, "—")); chips.append(el); return el; };
const cLink = chip("LINK"), cOptic = chip("OPTIC"), cFace = chip("FACE"), cEyes = chip("EYES"), cCtl = chip("CONTROL"), cFps = chip("FPS");
const setChip = (el, text, cls) => { el.className = `chip ${cls || ""}`; el.querySelector("b").textContent = text; };

const btnSound = document.getElementById("btn-sound");
const paintSound = () => { btnSound.innerHTML = soundOn() ? ICONS.soundOn : ICONS.soundOff; };
btnSound.addEventListener("click", () => {
  const v = !soundOn();
  setSound(v);
  paintSound();
  act("app_settings", { path: "ui.sound", value: v }).catch(() => {});
});
const btnFull = document.getElementById("btn-full");
btnFull.innerHTML = ICONS.full;
btnFull.addEventListener("click", () => {
  document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {});
});
const profSel = document.getElementById("profile-select");
profSel.addEventListener("change", () => { sfx.nav(); act("profile_switch", { name: profSel.value }).catch(() => {}); });

const brandName = document.querySelector(".brand-name");
setInterval(() => {
  brandName.classList.add("glitch");
  setTimeout(() => brandName.classList.remove("glitch"), 140 + Math.random() * 120);
}, 5200);
const brandIris = document.querySelector(".brand-eye .iris");
const brandPupil = document.querySelector(".brand-eye .pupil");

// ------------------------------------------------------------------ toasts

const toastsEl = document.getElementById("toasts");
function showToast(text, kind = "") {
  const el = h("div", { class: `toast ${kind}` });
  const span = h("span");
  el.append(span, h("span", { class: "cursor" }));
  toastsEl.prepend(el);
  while (toastsEl.children.length > 3) toastsEl.lastChild.remove();
  let i = 0;
  const up = String(text).toUpperCase();
  const type = () => { span.textContent = up.slice(0, ++i); if (i < up.length) setTimeout(type, 12); };
  type();
  sfx.toast();
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 500); }, 3400);
}
window.addEventListener("gazer-toast", (e) => showToast(e.detail.text, e.detail.kind));

// ---------------------------------------------------------------- callouts

const calloutRoot = document.getElementById("callouts");
function drawCallouts() {
  const v = views[current];
  const items = v && v.callouts && !isCalibrating() && !isTraining() ? v.callouts(stage, store.frame) : [];
  while (calloutRoot.children.length < items.length * 3) {
    calloutRoot.append(h("div", { class: "callout-dot" }), h("div", { class: "callout-line" }),
      h("div", { class: "callout" }, h("div", { class: "co-title" }), h("div", { class: "co-val" })));
  }
  [...calloutRoot.children].forEach((c, i) => { c.style.display = i < items.length * 3 ? "" : "none"; });
  items.forEach((it, i) => {
    const [dot, line, box] = [calloutRoot.children[i * 3], calloutRoot.children[i * 3 + 1], calloutRoot.children[i * 3 + 2]];
    const x = it.at.x, y = it.at.y, x2 = x + it.dx, y2 = y + it.dy;
    dot.style.left = `${x}px`; dot.style.top = `${y}px`;
    const len = Math.hypot(it.dx, it.dy);
    line.style.left = `${x}px`; line.style.top = `${y}px`; line.style.width = `${len}px`;
    line.style.transform = `rotate(${Math.atan2(it.dy, it.dx)}rad)`;
    box.style.left = `${x2 + (it.dx >= 0 ? 6 : -6)}px`; box.style.top = `${y2 - 14}px`;
    box.style.transform = it.dx >= 0 ? "" : "translateX(-100%)";
    box.style.textAlign = it.dx >= 0 ? "left" : "right";
    box.children[0].textContent = it.title;
    box.children[1].textContent = it.val;
  });
}

// ------------------------------------------------------------------ loop

function loop() {
  requestAnimationFrame(loop);
  stage.render();
  const v = views[current];
  v && v.tick && v.tick();
  drawCallouts();
}

// ------------------------------------------------------------- calibration

let sawCalib = false;
async function startCalibration(kind = "gaze", preset = "standard", append = false) {
  unlock();
  sfx.engage();
  try {
    const res = await request("calibrate", { kind, preset, append });
    if (!res?.native_window) {
      stage.paused = true;
      openCalibration({ native: false, closeCb: () => { stage.paused = false; } });
    }
  } catch (e) {
    toast(e.message, "warn");
  }
}

function startTrainer(level) {
  unlock();
  sfx.engage();
  stage.paused = true;
  openTrainer(level, () => {
    stage.paused = false;
    const v = views[current];
    v && v.enter && v.enter();
  });
}

// -------------------------------------------------------------- networking

let firstState = true;
on("link", (up) => {
  document.getElementById("offline").hidden = up;
  setChip(cLink, up ? "SECURE" : "LOST", up ? "ok" : "bad");
});
on("hello", (m) => setStore("catalog", m.catalog));
on("state", (s) => {
  setStore("state", s);
  refreshBindings();
  for (const v of Object.values(views)) v.onState && v.onState(s);
  for (const fn of stateFns) fn(s);
  const ui = s.config.ui;
  setSound(ui.sound);
  paintSound();
  document.body.classList.toggle("reduced-motion", ui.reduced_motion);
  stage.setQuality({ quality: ui.quality, bloom: ui.bloom, reducedMotion: ui.reduced_motion });
  profSel.innerHTML = "";
  for (const n of s.profiles) profSel.append(h("option", { value: n }, n));
  profSel.value = s.profile.name;
  setChip(cOptic, s.app.demo ? "SIMULATED" : (s.app.source || "—").toUpperCase(), s.app.demo ? "warn" : "live");
  setChip(cEyes, s.profile.gaze.ready ? "CALIBRATED" : "UNCALIBRATED", s.profile.gaze.ready ? "ok" : "warn");
  if (firstState) {
    firstState = false;
    if (CALIB_ONLY) return;
    boot(false).then(() => { if (!s.config.first_run_done) onboarding(false); });
  }
});
on("frame", (f) => {
  setStore("frame", f);
  stage.setFrame(f);
  const v = views[current];
  if (v && v.onFrame && !CALIB_ONLY) v.onFrame(f);
  if (f.insights) {
    setStore("insights", f.insights);
    stage.setHeat(f.insights.heat);
    for (const vv of Object.values(views)) vv.onInsights && vv.onInsights(f.insights);
  }
  trainerFrame(f);
  for (const ev of f.events) {
    if (ev.k === "request" && ev.what === "trainer" && !CALIB_ONLY) go("trainer");
    if (ev.k === "toast") showToast(ev.text, ev.text.startsWith("20-20-20") || ev.text.includes("blink") ? "wellness" : "");
    if (ev.k === "click") sfx.click();
  }
  if (f.calib) {
    sawCalib = true;
    if (!isCalibrating() && (CALIB_ONLY || !store.state?.app.native_calib)) {
      stage.paused = true;
      openCalibration({ native: CALIB_ONLY, closeCb: () => { stage.paused = false; } });
    }
    updateCalibration(f.calib);
  } else if (sawCalib && !f.calibrating) {
    sawCalib = false;
    if (isCalibrating()) closeCalibration();
    if (CALIB_ONLY) setTimeout(() => window.close(), 300);
  }
  setChip(cFace, f.face ? "LOCKED" : "SEARCHING", f.face ? "ok" : "bad");
  setChip(cCtl, !f.control ? "STANDBY" : f.paused ? "PAUSED" : "ACTIVE", !f.control ? "" : f.paused ? "warn" : "live");
  setChip(cFps, `${Math.round(f.fps)} · ${Math.round(f.lat)}ms`, f.fps > 20 ? "ok" : "warn");
  const gz = f.gaze || f.pointer || [0.5, 0.5];
  const ix = 32 + (gz[0] - 0.5) * 22, iy = 20 + (gz[1] - 0.5) * 12;
  brandIris.setAttribute("cx", ix); brandIris.setAttribute("cy", iy);
  brandPupil.setAttribute("cx", ix); brandPupil.setAttribute("cy", iy);
});
on("preview", (blob) => { for (const fn of previewFns) fn(blob); });

// -------------------------------------------------------------------- boot

async function boot(force) {
  const s = store.state;
  const lines = [
    `<span class="ok">[ OK ]</span> SECURE LINK ··········· <span class="hl">TOKEN VERIFIED · 127.0.0.1</span>`,
    `<span class="ok">[ OK ]</span> OPTICAL SENSOR ········ <span class="hl">${s.app.demo ? "SIMULATED PILOT" : s.app.camera}</span>`,
    `<span class="ok">[ OK ]</span> FACE MESH ············· <span class="hl">478 LANDMARKS · 52 BLENDSHAPES</span>`,
    s.profile.gaze.ready
      ? `<span class="ok">[ OK ]</span> NEURAL GAZE MODEL ····· <span class="hl">${s.profile.gaze.samples} SAMPLES · +${s.profile.gaze.implicit} LEARNED</span>`
      : `<span class="warn">[WARN]</span> NEURAL GAZE MODEL ····· <span class="warn">UNCALIBRATED — HEAD FALLBACK</span>`,
    `<span class="ok">[ OK ]</span> INPUT INJECTION ······· <span class="hl">${s.app.virtual_input ? "VIRTUAL CURSOR" : "ARMED"}</span>`,
    `<span class="ok">[ OK ]</span> PILOT PROFILE ········· <span class="hl">${s.profile.name.toUpperCase()}</span>`,
    `<span class="hl">&gt; ALL SYSTEMS NOMINAL. WELCOME BACK.</span>`,
  ];
  const skip = !force && (!s.config.ui.boot_sequence || sessionStorage.getItem("gazer-booted"));
  app.classList.add("booting");
  stage.paused = !skip;
  await runBoot({ lines, skip });
  stage.paused = false;
  sessionStorage.setItem("gazer-booted", "1");
  app.classList.remove("booting");
  app.classList.remove("revealed");
  void app.offsetWidth;
  app.classList.add("revealed");
}

// -------------------------------------------------------------- onboarding

function onboarding(force) {
  const s = store.state;
  if (!s || (!force && s.config.first_run_done)) return;
  const layer = document.getElementById("modal-layer");
  layer.hidden = false;
  let style = s.profile.settings.style || "eyes";
  const close = () => { layer.hidden = true; layer.innerHTML = ""; act("app_settings", { path: "first_run_done", value: true }).catch(() => {}); };
  const step1 = () => {
    layer.innerHTML = "";
    const choices = h("div", { class: "choices" });
    for (const [k, v] of Object.entries(store.catalog.styles)) {
      const c = h("button", { class: `choice ${k === style ? "on" : ""}`, type: "button" },
        h("div", { class: "choice-title" }, v.label.toUpperCase(), k === "eyes" ? h("span", { class: "badge" }, "RECOMMENDED") : null),
        h("div", { class: "choice-desc" }, v.desc));
      c.addEventListener("click", () => { style = k; sfx.toggleOn(); [...choices.children].forEach((x) => x.classList.toggle("on", x === c)); });
      choices.append(c);
    }
    const p = h("div", { class: "panel" }, h("div", { class: "step-dots" }, h("i", { class: "on" }), h("i"), h("i")),
      h("div", { class: "modal-title" }, "WELCOME, PILOT"),
      h("div", { class: "modal-sub" }, "Gazer lets you drive your whole computer with your eyes: move the cursor by looking, click by blinking twice, winking or dwelling, scroll by glancing past the screen edge, and type on a gaze keyboard. How do you want to control it?"),
      choices, h("div", { style: { height: "18px" } }),
      h("div", { class: "btn-row" }, button("SKIP", close), button("CONTINUE ›", async () => {
        await act("style", { style }).catch(() => {});
        step2();
      }, "primary")));
    layer.append(h("div", { class: "modal" }, p));
  };
  let checkTimer = null;
  const step2 = () => {
    // Optics check: fix face visibility and lighting *before* calibrating.
    layer.innerHTML = "";
    const face = h("div", { class: "check-row" });
    const light = h("div", { class: "check-row" });
    const advice = h("div", { class: "modal-sub", style: { marginTop: "10px", marginBottom: "0" } });
    const paint = () => {
      const f = store.frame, L = store.state?.lighting;
      const ok = (el, good, text) => { el.className = `check-row ${good ? "good" : "bad"}`; el.textContent = text; };
      ok(face, !!f?.face, f?.face ? "FACE LOCKED" : "LOOKING FOR YOUR FACE…");
      if (!L || L.status === "unknown") ok(light, false, "LIGHTING: MEASURING…");
      else ok(light, L.status === "good", `LIGHTING: ${L.status.toUpperCase()}`);
      advice.textContent = L && L.status !== "good" && L.status !== "unknown" ? L.advice
        : "Sit where you normally sit, 40–80 cm from the screen, with the camera at the top-centre.";
    };
    paint();
    clearInterval(checkTimer);
    checkTimer = setInterval(() => { if (!document.body.contains(face)) clearInterval(checkTimer); else paint(); }, 300);
    const p = h("div", { class: "panel" }, h("div", { class: "step-dots" }, h("i", { class: "on" }), h("i", { class: "on" }), h("i")),
      h("div", { class: "modal-title" }, "OPTICS CHECK"),
      h("div", { class: "modal-sub" }, "Good light on your face is the biggest single factor in eye-tracking accuracy."),
      face, light, advice, h("div", { style: { height: "18px" } }),
      h("div", { class: "btn-row" }, button("BACK", step1), button("CONTINUE ›", () => { clearInterval(checkTimer); step3(); }, "primary")));
    layer.append(h("div", { class: "modal" }, p));
  };
  const step3 = () => {
    layer.innerHTML = "";
    const needs = style !== "head";
    const p = h("div", { class: "panel" }, h("div", { class: "step-dots" }, h("i", { class: "on" }), h("i", { class: "on" }), h("i", { class: "on" })),
      h("div", { class: "modal-title" }, needs ? "CALIBRATE YOUR EYES" : "YOU'RE READY"),
      h("div", { class: "modal-sub" }, needs
        ? "About 50 seconds: look at each glowing target until it moves. Face the camera with light on your face. You can recalibrate anytime — and Gazer keeps learning from every click."
        : "Press ENGAGE on the deck. Smile to click, raise your brows to scroll, open your mouth to open the action wheel."),
      h("div", { class: "btn-row" }, button(needs ? "LATER" : "CLOSE", close),
        needs ? button("CALIBRATE NOW ›", () => { close(); startCalibration("gaze", "standard"); }, "primary") : null));
    layer.append(h("div", { class: "modal" }, p));
  };
  step1();
}

// ------------------------------------------------------------------- start

document.addEventListener("pointerdown", unlock, { once: true });
if (CALIB_ONLY) {
  app.style.display = "none";
  document.getElementById("stage").style.display = "none";
  subscribe({ mesh: false, preview: false });
} else {
  subscribe({ mesh: true });
  go((location.hash || "#deck").slice(1), true);
  loop();
}
connect();
