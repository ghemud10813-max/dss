import { h, panel, toggle, button, act, kv } from "../ui.js";

const PRESET_INFO = {
  quick: ["QUICK", "9 points · ~20 s. Good for a fast refresh."],
  standard: ["STANDARD", "16 points + head motion · ~50 s. Best balance."],
  deep: ["DEEP", "25 points + head motion · ~85 s. Maximum accuracy."],
};

export function buildCalibrate(root, ctx) {
  const view = h("div", { class: "view split", id: "view-calibrate" });
  const left = h("div", { class: "col left" });
  const right = h("div", { class: "col right" });
  view.append(left, h("div"), right);
  root.append(view);

  let append = false;
  const p1 = panel({ title: "EYE CALIBRATION", code: "//C1", hot: true,
    desc: "Teaches Gazer how your eyes map to the screen. Accuracy is measured on points the model never trained on, so the score is honest." });
  const grid = h("div", { class: "preset-grid" });
  for (const [k, [name, desc]] of Object.entries(PRESET_INFO)) {
    const b = h("button", { class: `preset ${k === "standard" ? "rec" : ""}`, type: "button" },
      h("div", { class: "p-name" }, name), h("div", { class: "p-desc" }, desc), h("div", { class: "p-go" }, "LAUNCH ›"));
    b.addEventListener("click", () => ctx.startCalibration("gaze", k, append));
    grid.append(b);
  }
  const head = h("button", { class: "preset", type: "button" },
    h("div", { class: "p-name" }, "HEAD RANGE"), h("div", { class: "p-desc" }, "6 targets · ~12 s. For the head-pointer mode."),
    h("div", { class: "p-go" }, "LAUNCH ›"));
  head.addEventListener("click", () => ctx.startCalibration("head", "head"));
  grid.append(head);
  p1.add(grid, h("div", { class: "hr" }),
    toggle({ label: "Add to existing data", sub: "Cover a new seat position or lighting without starting over", checked: false,
      onChange: (v) => { append = v; } }).el);
  left.append(p1.el);

  const tips = panel({ title: "FIELD MANUAL", code: "//C2" });
  tips.add(
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Light your face from the front"), " — a window behind you is the #1 accuracy killer.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Camera at the top-centre"), " of the screen you control, at your usual distance.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "It keeps learning."), " Every click you make trains the model a little more. Recenter (hotkey or wheel) fixes drift instantly.")),
  );
  left.append(tips.el);

  const res = panel({ title: "LAST RESULT", code: "//C3" });
  const grade = h("div", { class: "grade-big" }, "—");
  const detail = h("div", { class: "muted", style: { marginTop: "4px" } }, "No calibration yet");
  res.add(grade, detail);
  right.append(res.el);

  const hist = panel({ title: "ACCURACY HISTORY", code: "//C4", desc: "Mean error on unseen validation points (lower is better)." });
  const chart = h("canvas", { class: "chart" });
  hist.add(chart);
  right.append(hist.el);

  const model = panel({ title: "GAZE MODEL", code: "//C5" });
  const kReady = kv("Status");
  const kSamples = kv("Calibration samples");
  const kImplicit = kv("Learned from clicks");
  const kRmse = kv("Training fit (RMSE)");
  model.add(kReady.el, kSamples.el, kImplicit.el, kRmse.el, h("div", { class: "hr" }),
    h("div", { class: "btn-row" },
      button("FORGET CLICK SAMPLES", () => act("forget_implicit").catch(() => {}), "small"),
      button("DELETE MODEL", () => { if (confirm("Delete this profile's gaze model?")) act("delete_gaze").catch(() => {}); }, "danger small")));
  right.append(model.el);

  function drawChart(history, W) {
    const c = chart;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, hh = c.clientHeight;
    if (!w) return;
    c.width = w * dpr; c.height = hh * dpr;
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, hh);
    g.strokeStyle = "rgba(110,240,230,0.08)";
    for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(0, (hh * i) / 4); g.lineTo(w, (hh * i) / 4); g.stroke(); }
    if (!history.length) {
      g.fillStyle = "#6f8796"; g.font = "12px Rajdhani"; g.fillText("No data yet", 8, hh / 2); return;
    }
    const vals = history.map((x) => x.mean_px);
    const mx = Math.max(...vals, W * 0.08) * 1.1;
    // grade bands
    for (const [f, col] of [[0.03, "rgba(77,255,166,0.06)"], [0.05, "rgba(62,232,216,0.05)"], [0.08, "rgba(255,181,71,0.05)"]]) {
      const y = hh - ((f * W) / mx) * hh;
      g.fillStyle = col; g.fillRect(0, y, w, hh - y);
    }
    g.beginPath();
    vals.forEach((v, i) => {
      const x = vals.length === 1 ? w / 2 : (i / (vals.length - 1)) * (w - 16) + 8;
      const y = hh - (v / mx) * (hh - 10) - 4;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.strokeStyle = "#3ee8d8"; g.lineWidth = 2; g.shadowColor = "#3ee8d8"; g.shadowBlur = 8; g.stroke(); g.shadowBlur = 0;
    vals.forEach((v, i) => {
      const x = vals.length === 1 ? w / 2 : (i / (vals.length - 1)) * (w - 16) + 8;
      const y = hh - (v / mx) * (hh - 10) - 4;
      g.fillStyle = "#e9fffd"; g.beginPath(); g.arc(x, y, 3, 0, 6.28); g.fill();
    });
  }

  let lastState = null;
  return {
    el: view,
    enter() { if (lastState) drawChart(lastState.profile.history, lastState.screen.w); },
    onState(s) {
      lastState = s;
      const hs = s.profile.history;
      const last = hs[hs.length - 1];
      if (last) {
        grade.textContent = last.grade.toUpperCase();
        grade.className = `grade-big grade-${last.grade}`;
        detail.textContent = `${last.mean_px.toFixed(0)} px mean error · ${last.preset} · ${last.samples} samples · ${new Date(last.when * 1000).toLocaleString()}`;
      }
      const gz = s.profile.gaze;
      kReady.v.textContent = gz.ready ? "READY" : "NOT CALIBRATED";
      kReady.v.style.color = gz.ready ? "var(--green)" : "var(--amber)";
      kSamples.v.textContent = gz.samples;
      kImplicit.v.textContent = gz.implicit;
      kRmse.v.textContent = gz.rmse == null ? "—" : `${(gz.rmse * s.screen.w).toFixed(0)} px`;
      if (view.classList.contains("active")) drawChart(hs, s.screen.w);
    },
  };
}
