import { h, panel, slider, toggle, select, field, act, onStore, actionOptions, store } from "../ui.js";
import { sfx } from "../sound.js";

const MODE_DETAIL = {
  gaze: ["GAZE", "Cursor follows your eyes directly. Pure eye control — pair with dwell, winks or double-blink to click and the zoom lens for tiny targets."],
  hybrid: ["HYBRID", "Eyes jump the cursor across the screen; small head movements place it exactly (MAGIC pointing)."],
  head_mouse: ["HEAD MOUSE", "Move your head like a mouse; faster moves travel further. Very precise, no calibration."],
  head_joystick: ["JOYSTICK", "Lean away from centre to glide, return to stop. Good for limited range of motion."],
  head_absolute: ["HEAD POINTER", "Your nose points at the screen. Run head-range calibration for best results."],
};

export function buildPointer(root) {
  const view = h("div", { class: "view split", id: "view-pointer" });
  const left = h("div", { class: "col left" });
  const right = h("div", { class: "col right" });
  view.append(left, h("div"), right);
  root.append(view);

  const modes = panel({ title: "POINTER MODE", code: "//P1", hot: true });
  const list = h("div", { class: "choices" });
  const modeEls = {};
  for (const [k, [name, desc]] of Object.entries(MODE_DETAIL)) {
    const c = h("button", { class: "choice", type: "button" }, h("div", { class: "choice-title" }, name,
      k === "gaze" ? h("span", { class: "badge" }, "EYES ONLY") : null), h("div", { class: "choice-desc" }, desc));
    c.addEventListener("click", () => { sfx.toggleOn(); act("mode", { mode: k }).catch(() => {}); });
    modeEls[k] = c;
    list.append(c);
  }
  modes.add(list);
  left.append(modes.el);

  const gaze = panel({ title: "EYE CURSOR", code: "//P2" });
  gaze.add(
    slider({ label: "Gaze smoothing", path: "pointer.gaze_smoothing", min: 0, max: 1, step: 0.05, tip: "Higher = steadier while you fixate, slightly slower to follow" }).el,
    slider({ label: "Output glide", path: "pointer.output_smoothing_ms", min: 0, max: 120, step: 5, fmt: (v) => `${v} ms` }).el,
    slider({ label: "Hybrid jump distance", path: "pointer.warp_threshold", min: 0.04, max: 0.35, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%` }).el,
    slider({ label: "Hybrid jump delay", path: "pointer.warp_delay_ms", min: 40, max: 500, step: 10, fmt: (v) => `${v} ms` }).el,
    slider({ label: "Precision factor", path: "pointer.precision_factor", min: 0.1, max: 1, step: 0.05 }).el,
    toggle({ label: "Physical mouse overrides", sub: "Moving the real mouse takes over for a moment", path: "pointer.manual_override" }).el,
  );
  left.append(gaze.el);

  const head = panel({ title: "HEAD CONTROL", code: "//P3" });
  head.add(
    slider({ label: "Speed", path: "pointer.head_gain", min: 0.2, max: 3, step: 0.05 }).el,
    slider({ label: "Acceleration", path: "pointer.head_accel", min: 1, max: 2.5, step: 0.05 }).el,
    slider({ label: "Jitter filter", path: "pointer.head_deadzone", min: 0, max: 0.005, step: 0.0002, fmt: (v) => v.toFixed(4) }).el,
    slider({ label: "Hybrid fine gain", path: "pointer.hybrid_fine_gain", min: 0.1, max: 1.5, step: 0.05 }).el,
    slider({ label: "Joystick speed", path: "pointer.joystick_speed", min: 0.2, max: 3, step: 0.05 }).el,
    slider({ label: "Joystick deadzone", path: "pointer.joystick_deadzone", min: 0, max: 0.1, step: 0.005, fmt: (v) => v.toFixed(3) }).el,
    slider({ label: "Head pointer gain", path: "pointer.absolute_gain", min: 0.3, max: 2.5, step: 0.05 }).el,
    h("div", { class: "grid-2" }, toggle({ label: "Invert X", path: "pointer.invert_x" }).el, toggle({ label: "Invert Y", path: "pointer.invert_y" }).el),
  );
  left.append(head.el);

  const mag = panel({ title: "MAGNETIC TARGETS", code: "//P0", hot: true,
    desc: "The cursor locks onto the button or link you're looking at (via the OS accessibility tree), so small targets become easy — and every locked click teaches the gaze model." });
  const magStatus = h("div", { class: "kv" }, h("span", {}, "Provider"), h("span"));
  mag.add(
    toggle({ label: "Magnetic targets", sub: "Gaze mode · Windows UI Automation (pip install comtypes)", path: "pointer.magnetic" }).el,
    slider({ label: "Capture radius", path: "pointer.magnetic_radius_px", min: 15, max: 120, step: 5, fmt: (v) => `${v} px` }).el,
    toggle({ label: "Dwell only on buttons & links", sub: "No accidental clicks while you read", path: "dwell.targets_only" }).el,
    magStatus);
  right.append(mag.el);

  const dock = panel({ title: "EYE DOCK", code: "//P6",
    desc: "A toolbar on the screen edge: rest your gaze on a button to choose what your next dwell does — right, double, drag, precise — or toggle keyboard, dwell and pause." });
  dock.add(
    toggle({ label: "Show the Eye Dock", sub: "Appears while control is engaged (desktop app)", path: "dock.enabled" }).el,
    field("Side", select({ options: { right: "Right edge", left: "Left edge" }, path: "dock.side" }).el),
    slider({ label: "Button size", path: "dock.button_px", min: 56, max: 130, step: 2, fmt: (v) => `${v} px` }).el,
    slider({ label: "Dock dwell", path: "dock.dwell_ms", min: 300, max: 1500, step: 50, fmt: (v) => `${v} ms` }).el);

  const dwell = panel({ title: "DWELL CLICK", code: "//P4", desc: "Hold the cursor still to act — the core of hands-free clicking. “Precise click” magnifies first, then clicks inside the lens." });
  const dwellAction = select({ options: actionOptions, path: "dwell.action" });
  onStore("catalog", () => dwellAction.fill(actionOptions()));
  dwell.add(
    toggle({ label: "Enabled", path: "dwell.enabled" }).el,
    slider({ label: "Dwell time", path: "dwell.time_ms", min: 300, max: 2500, step: 50, fmt: (v) => `${v} ms` }).el,
    slider({ label: "Radius", path: "dwell.radius_px", min: 15, max: 150, step: 5, fmt: (v) => `${v} px` }).el,
    slider({ label: "Cooldown", path: "dwell.cooldown_ms", min: 200, max: 2500, step: 50, fmt: (v) => `${v} ms` }).el,
    field("Action", dwellAction.el),
  );
  right.append(dwell.el);
  right.append(dock.el);

  const scroll = panel({ title: "SCROLL · ZOOM · WHEEL", code: "//P5" });
  const wheelBox = h("div", { class: "cmd-chips", style: { marginTop: "6px" } });
  scroll.add(
    slider({ label: "Scroll speed", path: "scroll.speed", min: 0.2, max: 3, step: 0.1 }).el,
    slider({ label: "Scroll deadzone", path: "scroll.deadzone", min: 0, max: 0.1, step: 0.005, fmt: (v) => v.toFixed(3) }).el,
    h("div", { class: "grid-2" }, toggle({ label: "Invert scroll", path: "scroll.invert" }).el, toggle({ label: "Horizontal", path: "scroll.horizontal" }).el),
    h("div", { class: "hr" }),
    slider({ label: "Zoom lens factor", path: "zoom.factor", min: 1.5, max: 6, step: 0.25, fmt: (v) => `${v.toFixed(2)}×` }).el,
    slider({ label: "Wheel select dwell", path: "wheel.select_dwell_ms", min: 200, max: 1500, step: 50, fmt: (v) => `${v} ms` }).el,
    h("div", { class: "muted", style: { marginTop: "8px", fontSize: "13px" } }, "ACTION WHEEL ITEMS (click to toggle)"),
    wheelBox,
  );
  right.append(scroll.el);

  const WHEEL_CHOICES = ["left_click", "double_click", "right_click", "middle_click", "drag_toggle", "scroll_mode", "zoom",
    "keyboard_toggle", "recenter", "precision_toggle", "pause_toggle", "mode_cycle", "hotkey:alt+tab", "hotkey:ctrl+c",
    "hotkey:ctrl+v", "hotkey:alt+left", "key:enter", "key:esc"];

  function renderWheel(items) {
    wheelBox.innerHTML = "";
    const acts = store.catalog?.actions || {};
    for (const k of WHEEL_CHOICES) {
      const on = items.includes(k);
      const chip = h("span", { style: { cursor: "pointer", borderColor: on ? "var(--cyan)" : "", color: on ? "var(--cyan-hi)" : "" } }, acts[k] || k);
      chip.addEventListener("click", () => {
        const next = on ? items.filter((x) => x !== k) : [...items, k];
        if (next.length < 2 || next.length > 12) return;
        sfx.tap();
        act("settings", { path: "wheel.items", value: next }).catch(() => {});
      });
      wheelBox.append(chip);
    }
  }

  return {
    el: view,
    onState(s) {
      const t = s.targets;
      magStatus.lastChild.textContent = t.available ? `${t.provider.toUpperCase()} · READY` : `${t.provider.toUpperCase()} · ${t.status.toUpperCase()}`;
      magStatus.lastChild.style.color = t.available ? "var(--green)" : "var(--amber)";
      const p = s.profile.settings.pointer;
      for (const [k, el] of Object.entries(modeEls)) el.classList.toggle("on", p.mode === k);
      renderWheel(s.profile.settings.wheel.items);
    },
  };
}
