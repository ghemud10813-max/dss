import { h, panel, button, store } from "../ui.js";
import { LEVELS, bestScore } from "../trainer.js";

const DESC = {
  warmup: "Big, slow targets. Learn the rhythm: look, settle, click.",
  precision: "Button-sized targets. This is everyday computer use.",
  sniper: "Tiny targets. Use the Eye Dock's PRECISE click or magnetic targets.",
};

export function buildTrainer(root, ctx) {
  const view = h("div", { class: "view wide", id: "view-trainer" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  const lv = panel({ title: "GAZE TRAINER", code: "//T1", hot: true,
    desc: "Target practice for your eyes. Targets appear one by one — hit each with any Gazer click (dwell, double blink, wink). You're scored on accuracy, speed and Fitts' throughput, the standard measure of pointing performance." });
  const cards = h("div", { class: "choices" });
  const bestEls = {};
  for (const [k, L] of Object.entries(LEVELS)) {
    const best = h("span", { class: "trainer-best" });
    bestEls[k] = best;
    const c = h("button", { class: "choice trainer-level", type: "button" },
      h("div", { class: "choice-title" }, L.label, h("span", { class: "badge" }, `${L.size}px · ${L.count} targets`), best),
      h("div", { class: "choice-desc" }, DESC[k]));
    c.addEventListener("click", () => ctx.startTrainer(k));
    cards.append(c);
  }
  lv.add(cards);
  left.append(lv.el);

  const tips = panel({ title: "COACHING", code: "//T2" });
  tips.add(
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Look, don't chase. "), "Put your eyes on the target's centre and wait — the cursor comes to you.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Missing to one side every time? "), "That's drift — recalibrate, or let magnetic targets and click-learning absorb it.")),
    h("div", { style: { height: "8px" } }),
    h("div", { class: "hint" }, h("span", {}, h("b", {}, "Throughput "), "over ~1.5 bit/s is solid webcam eye control; a mouse is ~4–5.")),
  );
  right.append(tips.el);

  const note = panel({ title: "SETUP", code: "//T3" });
  const monitorNote = h("div", { class: "muted", style: { fontSize: "13.5px", lineHeight: "1.45" } });
  note.add(monitorNote, h("div", { style: { height: "10px" } }),
    h("div", { class: "btn-row" }, button("START WARM-UP", () => ctx.startTrainer("warmup"), "primary")));
  right.append(note.el);

  const refresh = () => {
    for (const k of Object.keys(LEVELS)) {
      const b = bestScore(k);
      bestEls[k].textContent = b ? `BEST ${b.score.toLocaleString()} · ${Math.round(b.accuracy * 100)}%` : "";
    }
  };
  return {
    el: view,
    enter() { refresh(); },
    onState(s) {
      refresh();
      monitorNote.textContent = s.app.demo
        ? "Demo mode: the simulated pilot plays the game so you can watch. Engage control is switched on for the round."
        : `The trainer runs fullscreen — keep this window on the monitor Gazer controls (${s.screen.w}×${s.screen.h}). Control is switched on automatically for the round.`;
      void store;
    },
  };
}
