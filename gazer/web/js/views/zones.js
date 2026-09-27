import { h, panel, slider, toggle, act, store, onStore, actionOptions } from "../ui.js";
import { sfx } from "../sound.js";

// zone → [left%, top%, translate]
const POS = {
  top_left: [0, 0, "translate(-60%,-70%)"], top: [50, 0, "translate(-50%,-80%)"], top_right: [100, 0, "translate(-40%,-70%)"],
  right: [100, 50, "translate(-30%,-50%)"], bottom_right: [100, 100, "translate(-40%,-30%)"], bottom: [50, 100, "translate(-50%,-20%)"],
  bottom_left: [0, 100, "translate(-60%,-30%)"], left: [0, 50, "translate(-70%,-50%)"],
};

export function buildZones(root) {
  const view = h("div", { class: "view wide", id: "view-zones" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  const map = panel({ title: "GAZE HOT-ZONES", code: "//Z1", hot: true,
    desc: "Look just past an edge or corner of your screen and hold for a moment. Zones sit on the bezel, so on-screen buttons stay clickable. Scroll zones repeat and speed up while you keep looking — hands-free reading." });
  const stage = h("div", { class: "zone-stage" }, h("div", { class: "screen-label" }, "YOUR SCREEN"));
  const dot = h("div", { class: "gaze-dot" });
  stage.append(dot);
  const zoneEls = {};
  const bands = {};
  const bandDefs = {
    top: { left: "20%", top: "-3%", width: "60%", height: "6%" }, bottom: { left: "20%", bottom: "-3%", width: "60%", height: "6%" },
    left: { left: "-2%", top: "20%", width: "4%", height: "60%" }, right: { right: "-2%", top: "20%", width: "4%", height: "60%" },
    top_left: { left: "-2%", top: "-3%", width: "8%", height: "14%" }, top_right: { right: "-2%", top: "-3%", width: "8%", height: "14%" },
    bottom_left: { left: "-2%", bottom: "-3%", width: "8%", height: "14%" }, bottom_right: { right: "-2%", bottom: "-3%", width: "8%", height: "14%" },
  };
  for (const [k, st] of Object.entries(bandDefs)) {
    const b = h("div", { class: "zone-band", style: st });
    bands[k] = b;
    stage.append(b);
  }
  map.add(stage);
  left.append(map.el);

  onStore("catalog", (cat) => {
    for (const [k, label] of Object.entries(cat.zones)) {
      const [x, y, tr] = POS[k];
      const sel = h("select", { "aria-label": label });
      const prog = h("i", { class: "z-prog" });
      const z = h("div", { class: "zone", style: { left: `${x}%`, top: `${y}%`, transform: tr } },
        h("div", { class: "z-box" }, h("div", { class: "z-name" }, label.toUpperCase()), sel, prog));
      sel.addEventListener("change", () => { sfx.tap(); act("settings", { path: `zones.actions.${k}`, value: sel.value }).catch(() => {}); });
      zoneEls[k] = { z, sel, prog };
      stage.append(z);
    }
    fillSelects();
  });

  function fillSelects() {
    const opts = actionOptions();
    for (const { sel } of Object.values(zoneEls)) {
      const v = sel.value;
      sel.innerHTML = "";
      for (const [k, l] of Object.entries(opts)) sel.append(h("option", { value: k }, l));
      if (v) sel.value = v;
    }
  }

  const cfg = panel({ title: "ZONE ENGINE", code: "//Z2" });
  cfg.add(
    toggle({ label: "Gaze zones enabled", sub: "Also toggle with the “Gaze zones on/off” action", path: "zones.enabled" }).el,
    slider({ label: "Dwell before firing", path: "zones.dwell_ms", min: 250, max: 2000, step: 50, fmt: (v) => `${v} ms` }).el,
    slider({ label: "Edge band", path: "zones.size", min: 0.01, max: 0.1, step: 0.005, fmt: (v) => `${(v * 100).toFixed(1)}%` }).el,
  );
  right.append(cfg.el);

  const how = panel({ title: "EYES-ONLY PLAYBOOK", code: "//Z3" });
  how.add(
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Reading: "), "glance below the screen to scroll down, above it to scroll up. Keep looking to go faster.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Browsing: "), "left/right edges go back and forward. Top-left opens the action wheel for right-click, drag, copy, paste.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Typing: "), "bottom-right opens the gaze keyboard. Top-right pauses everything (look there again to resume).")),
  );
  right.append(how.el);

  const lastFire = {};
  return {
    el: view,
    onState(s) {
      const a = s.profile.settings.zones.actions;
      for (const [k, { sel }] of Object.entries(zoneEls)) if (document.activeElement !== sel && a[k] != null) sel.value = a[k];
      stage.style.opacity = s.profile.settings.zones.enabled ? 1 : 0.55;
    },
    onFrame(f) {
      const p = f.gaze || f.pointer;
      dot.style.display = p && f.face ? "" : "none";
      if (p) { dot.style.left = `${Math.max(-3, Math.min(103, p[0] * 100))}%`; dot.style.top = `${Math.max(-5, Math.min(105, p[1] * 100))}%`; }
      for (const [k, { z, prog }] of Object.entries(zoneEls)) {
        const hot = f.zone && f.zone[0] === k;
        z.classList.toggle("hot", !!hot);
        bands[k].classList.toggle("hot", !!hot);
        prog.style.width = hot ? `${f.zone[1] * 100}%` : "0";
      }
      for (const ev of f.events) {
        if (ev.k === "zone" && zoneEls[ev.zone]) {
          const z = zoneEls[ev.zone].z;
          z.classList.remove("fire"); void z.offsetWidth; z.classList.add("fire");
          if (!lastFire[ev.zone] || performance.now() - lastFire[ev.zone] > 400) sfx.toggleOn();
          lastFire[ev.zone] = performance.now();
        }
      }
      void store;
    },
  };
}
