import { h, panel, button, act, store, onStore, actionOptions } from "../ui.js";
import { sfx } from "../sound.js";

const R = 22, C = 2 * Math.PI * R;

export function buildGestures(root) {
  const view = h("div", { class: "view wide", id: "view-gestures" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  const lib = panel({ title: "GESTURE LIBRARY", code: "//G1",
    desc: "Live strength of every facial signal. The white tick is the trigger threshold — green means it fired. Tune per gesture." });
  const grid = h("div", { class: "g-grid" });
  lib.add(h("div", { class: "btn-row", style: { marginBottom: "12px" } },
    button("CAPTURE NEUTRAL FACE", () => act("neutral").catch(() => {}), "primary"),
  ), grid);
  left.append(lib.el);

  const bindP = panel({ title: "BINDINGS", code: "//G2",
    desc: "What each gesture does. Eyes-only tip: double blink and winks click, long blink pauses, dwell covers the rest." });
  const rows = h("div");
  const head = h("div", { class: "bind-row bind-head" }, h("span", {}, "GESTURE"), h("span", {}, "TRIGGER"), h("span", {}, "ACTION"),
    h("span", {}, "HOLD MS"), h("span"));
  bindP.add(head, rows, h("div", { class: "btn-row", style: { marginTop: "10px" } },
    button("+ ADD BINDING", () => { bindings.push({ gesture: "smile", trigger: "start", action: "left_click", hold_ms: 600 }); save(); })));
  right.append(bindP.el);

  const cards = {};
  let bindings = [];
  let saveTimer = null;
  const save = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => act("bindings", { bindings }).catch(() => {}), 150);
    renderBindings();
  };

  onStore("catalog", (cat) => {
    grid.innerHTML = "";
    for (const [k, g] of Object.entries(cat.gestures)) {
      const fg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      const ring = h("div", { html: `<svg class="g-ring" viewBox="0 0 54 54"><circle class="bg" cx="27" cy="27" r="${R}"/>
        <circle class="fg" cx="27" cy="27" r="${R}" stroke-dasharray="${C}" stroke-dashoffset="${C}"/>
        <line class="thr" x1="27" y1="1" x2="27" y2="9"/></svg>` });
      void fg;
      const en = h("input", { type: "checkbox" });
      const thr = h("input", { type: "range", min: 0.1, max: 0.95, step: 0.01 });
      const thrV = h("span", { class: "val" });
      const hold = h("input", { type: "range", min: 0, max: 1500, step: 25 });
      const holdV = h("span", { class: "val" });
      const card = h("div", { class: "g-card" },
        h("div", { class: "g-top" }, ring,
          h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "g-name" }, g.label.toUpperCase()), h("div", { class: "g-desc" }, g.desc)),
          h("label", { class: "toggle", style: { padding: 0 } }, en, h("span", { class: "sw" }))),
        h("div", { class: "slider" }, h("label", {}, "Trigger"), thr, thrV),
        k === "double_blink" ? null : h("div", { class: "slider" }, h("label", {}, "Hold"), hold, holdV));
      const send = (path, value) => act("settings", { path: `gestures.gestures.${k}.${path}`, value }).catch(() => {});
      en.addEventListener("change", () => { en.checked ? sfx.toggleOn() : sfx.toggleOff(); send("enabled", en.checked); });
      let t1, t2;
      thr.addEventListener("input", () => { thrV.textContent = Number(thr.value).toFixed(2); paintThr(); clearTimeout(t1); t1 = setTimeout(() => send("threshold", Number(thr.value)), 150); });
      hold.addEventListener("input", () => { holdV.textContent = `${hold.value}`; clearTimeout(t2); t2 = setTimeout(() => send("min_hold_ms", Number(hold.value)), 150); });
      const svg = ring.querySelector("svg");
      const paintThr = () => {
        const a = Number(thr.value) * 2 * Math.PI - Math.PI / 2;
        const l = svg.querySelector(".thr");
        l.setAttribute("x1", 27 + Math.cos(a) * (R - 4)); l.setAttribute("y1", 27 + Math.sin(a) * (R - 4));
        l.setAttribute("x2", 27 + Math.cos(a) * (R + 4)); l.setAttribute("y2", 27 + Math.sin(a) * (R + 4));
      };
      cards[k] = { card, fg: svg.querySelector(".fg"), en, thr, thrV, hold, holdV, paintThr, v: 0 };
      grid.append(card);
    }
  });

  function renderBindings() {
    rows.innerHTML = "";
    const cat = store.catalog;
    if (!cat) return;
    const gOpts = Object.fromEntries(Object.entries(cat.gestures).map(([k, v]) => [k, v.label]));
    bindings.forEach((b, i) => {
      const mkSel = (opts, val, key) => {
        const s = h("select");
        for (const [k, v] of Object.entries(opts)) s.append(h("option", { value: k }, v));
        s.value = val;
        s.addEventListener("change", () => { bindings[i][key] = s.value; sfx.tap(); save(); });
        return s;
      };
      const hold = h("input", { type: "number", min: 0, max: 5000, step: 50, value: b.hold_ms });
      hold.addEventListener("change", () => { bindings[i].hold_ms = Number(hold.value) || 0; save(); });
      hold.disabled = !(b.trigger === "hold" || b.trigger === "tap");
      const x = h("button", { class: "x-btn", type: "button", title: "Remove" }, "×");
      x.addEventListener("click", () => { bindings.splice(i, 1); sfx.toggleOff(); save(); });
      const trig = { start: "On start", hold: "After hold", tap: "Quick tap", while_held: "While held" };
      rows.append(h("div", { class: "bind-row" }, mkSel(gOpts, b.gesture, "gesture"), mkSel(trig, b.trigger, "trigger"),
        mkSel(actionOptions(), b.action, "action"), hold, x));
    });
  }

  return {
    el: view,
    onState(s) {
      const set = s.profile.settings.gestures;
      bindings = set.bindings.map((b) => ({ ...b }));
      renderBindings();
      for (const [k, c] of Object.entries(cards)) {
        const g = set.gestures[k];
        if (!g) continue;
        c.en.checked = g.enabled;
        c.card.classList.toggle("off", !g.enabled);
        if (document.activeElement !== c.thr) { c.thr.value = g.threshold; c.thrV.textContent = g.threshold.toFixed(2); c.paintThr(); }
        if (document.activeElement !== c.hold) { c.hold.value = g.min_hold_ms; c.holdV.textContent = `${g.min_hold_ms}`; }
      }
    },
    onFrame(f) {
      const act2 = new Set(f.ga);
      for (const [k, c] of Object.entries(cards)) {
        const v = f.gv[k] ?? 0;
        c.v += (v - c.v) * 0.5;
        c.fg.setAttribute("stroke-dashoffset", (C * (1 - c.v)).toFixed(1));
        c.card.classList.toggle("active", act2.has(k) || (k === "double_blink" && v > 0.5));
      }
    },
  };
}
