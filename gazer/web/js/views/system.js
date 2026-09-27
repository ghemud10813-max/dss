import { h, panel, slider, toggle, select, field, button, act, kv, fmtDur, store, toast } from "../ui.js";

export function buildSystem(root, ctx) {
  const view = h("div", { class: "view wide", id: "view-system" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  // profiles
  const prof = panel({ title: "PILOT PROFILES", code: "//S1", hot: true,
    desc: "Each profile has its own calibration, gestures and settings — one per person or seating position." });
  const list = h("div", { class: "prof-list" });
  const name = h("input", { type: "text", placeholder: "New profile name", maxlength: 40 });
  const styleSel = select({ options: { eyes: "Eyes only", eyes_head: "Eyes + head", head: "Head only" }, value: "eyes" });
  prof.add(list, h("div", { class: "hr" }), h("div", { class: "btn-row" }, name, styleSel.el,
    button("CREATE", async () => {
      const n = name.value.trim();
      if (!n) return;
      await act("profile_create", { name: n, style: styleSel.el.value }).catch(() => {});
      name.value = "";
    }, "primary")));
  left.append(prof.el);

  // camera
  const cam = panel({ title: "OPTICAL SENSOR", code: "//S2" });
  const camStatus = kv("Status");
  cam.add(camStatus.el,
    field("Camera index", select({ options: { 0: "Camera 0", 1: "Camera 1", 2: "Camera 2", 3: "Camera 3" }, path: "camera.index", scope: "app" }).el),
    field("Backend", select({ options: { auto: "Auto", msmf: "Media Foundation", dshow: "DirectShow", v4l2: "V4L2", avfoundation: "AVFoundation", any: "Any" }, path: "camera.backend", scope: "app" }).el),
    field("Resolution", (() => {
      const s = h("select");
      for (const [w, hh] of [[640, 480], [1280, 720], [1920, 1080]]) s.append(h("option", { value: `${w}x${hh}` }, `${w} × ${hh}`));
      s.addEventListener("change", async () => {
        const [w, hh] = s.value.split("x").map(Number);
        await act("app_settings", { path: "camera.width", value: w }).catch(() => {});
        await act("app_settings", { path: "camera.height", value: hh }).catch(() => {});
      });
      ctx.onState((st) => { if (document.activeElement !== s) s.value = `${st.config.camera.width}x${st.config.camera.height}`; });
      return s;
    })()),
    toggle({ label: "Mirror image", sub: "Keep on for a normal selfie-style webcam", path: "camera.mirror", scope: "app" }).el,
    h("div", { class: "btn-row" }, button("RESTART CAMERA", () => act("camera_restart").catch(() => {}))));
  left.append(cam.el);

  // display
  const disp = panel({ title: "DISPLAY & OVERLAY", code: "//S3" });
  const monSel = select({ options: {}, path: "screen_index", scope: "app" });
  disp.add(field("Controlled monitor", monSel.el),
    toggle({ label: "Cursor ring", path: "overlay.show_cursor_ring" }).el,
    toggle({ label: "Dwell progress ring", path: "overlay.show_dwell_ring" }).el,
    toggle({ label: "Click ripples", path: "overlay.click_ripple" }).el,
    toggle({ label: "On-screen toasts", path: "overlay.show_toasts" }).el,
    field("Keyboard dock", select({ options: { bottom: "Bottom", top: "Top" }, path: "keyboard.dock" }).el),
    slider({ label: "Keyboard height", path: "keyboard.height_frac", min: 0.25, max: 0.6, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%` }).el,
    toggle({ label: "Word prediction", path: "keyboard.predictions" }).el);
  right.append(disp.el);

  // interface
  const uiP = panel({ title: "INTERFACE", code: "//S4" });
  uiP.add(
    toggle({ label: "Sound design", path: "ui.sound", scope: "app" }).el,
    toggle({ label: "Boot sequence", path: "ui.boot_sequence", scope: "app" }).el,
    toggle({ label: "Reduced motion", path: "ui.reduced_motion", scope: "app" }).el,
    slider({ label: "Bloom intensity", path: "ui.bloom", scope: "app", min: 0, max: 2, step: 0.05 }).el,
    field("Render quality", select({ options: { high: "High", low: "Low (faster)" }, path: "ui.quality", scope: "app" }).el));
  right.append(uiP.el);

  // hotkeys + about
  const hk = panel({ title: "HOTKEYS", code: "//S5" });
  const hkList = h("div");
  hk.add(toggle({ label: "Global hotkeys", path: "hotkeys.enabled", scope: "app" }).el, hkList);
  right.append(hk.el);

  const about = panel({ title: "SYSTEM", code: "//S6" });
  const kVer = kv("Version"), kMode = kv("Mode"), kPlat = kv("Platform"), kUp = kv("Uptime"), kShell = kv("Desktop layer");
  about.add(kVer.el, kMode.el, kPlat.el, kUp.el, kShell.el, h("div", { class: "hr" }),
    h("div", { class: "btn-row" }, button("REPLAY BOOT", () => ctx.replayBoot()), button("SETUP WIZARD", () => ctx.onboarding(true)),
      button("QUIT", () => { if (confirm("Quit Gazer?")) act("quit").catch(() => toast("Close this tab and stop the terminal process")); }, "danger")));
  right.append(about.el);

  return {
    el: view,
    onState(s) {
      list.innerHTML = "";
      for (const n of s.profiles) {
        const on = n === s.profile.name;
        const row = h("div", { class: `prof ${on ? "on" : ""}` }, h("span", { class: "p-n" }, n),
          on ? h("span", { class: "panel-tag live" }, "ACTIVE") : button("SWITCH", () => act("profile_switch", { name: n }).catch(() => {}), "small"),
          button("CLONE", () => { const d = prompt("Name for the copy:", `${n} copy`); if (d) act("profile_duplicate", { src: n, dst: d }).catch(() => {}); }, "small"),
          on ? null : button("DELETE", () => { if (confirm(`Delete profile “${n}”?`)) act("profile_delete", { name: n }).catch(() => {}); }, "danger small"));
        list.append(row);
      }
      camStatus.v.textContent = s.app.camera;
      const opts = {};
      s.monitors.forEach((m, i) => { opts[i] = `${m.primary ? "Primary" : `Display ${i + 1}`} · ${m.w}×${m.h}`; });
      monSel.fill(opts);
      monSel.el.value = String(s.config.screen_index);
      const hkc = s.config.hotkeys;
      hkList.innerHTML = "";
      for (const [k, label] of [["pause", "Pause / resume"], ["stop", "Control on/off"], ["recenter", "Recenter"], ["keyboard", "Keyboard"],
        ["mode", "Next mode"], ["calibrate", "Calibrate"]]) {
        const r = kv(label);
        r.v.textContent = (hkc[k] || "").replace(/[<>]/g, "").toUpperCase();
        hkList.append(r.el);
      }
      if (s.hotkeys.error) hkList.append(h("div", { class: "muted", style: { fontSize: "13px" } }, s.hotkeys.error));
      kVer.v.textContent = `v${s.app.version}`;
      kMode.v.textContent = s.app.demo ? "DEMO · simulated pilot" : "LIVE · webcam";
      kPlat.v.textContent = s.app.platform;
      kUp.v.textContent = fmtDur(s.app.uptime);
      kShell.v.textContent = s.app.native_ui ? "overlay · keyboard · tray" : "browser only";
      void store;
    },
  };
}
