import { h, panel, button, act, kv } from "../ui.js";

export function buildVoice(root) {
  const view = h("div", { class: "view wide", id: "view-voice" });
  const left = h("div", { class: "col" });
  const right = h("div", { class: "col" });
  view.append(left, right);
  root.append(view);

  const core = panel({ title: "VOICE LINK", code: "//V1", hot: true,
    desc: "Offline speech (Vosk) — nothing leaves your computer. Say a command, or “start dictation” to type by voice." });
  const orb = h("div", { class: "voice-orb" }, h("div", { class: "vo-label" }, "OFFLINE"));
  const toggleBtn = button("ENABLE VOICE", () => act("voice", {}).catch(() => {}), "primary");
  const dlBtn = button("DOWNLOAD MODEL (40 MB)", () => act("voice_download").catch(() => {}));
  const bar = h("div", { class: "progress" }, h("i"));
  const kStatus = kv("Status"), kAvail = kv("Engine"), kModel = kv("Model");
  core.add(orb, h("div", { class: "btn-row" }, toggleBtn, dlBtn), bar, h("div", { class: "hr" }), kStatus.el, kAvail.el, kModel.el);
  left.append(core.el);

  const heardP = panel({ title: "LAST HEARD", code: "//V2" });
  const heard = h("div", { class: "heard" }, "…");
  heardP.add(heard);
  left.append(heardP.el);

  const cmds = panel({ title: "COMMAND LEXICON", code: "//V3" });
  const chips = h("div", { class: "cmd-chips" });
  cmds.add(chips);
  right.append(cmds.el);

  let lastCmds = "";
  return {
    el: view,
    onState(s) {
      const v = s.voice;
      const on = s.profile.settings.voice.enabled;
      orb.classList.toggle("on", on);
      orb.firstChild.textContent = on ? (/dictation/i.test(v.status) ? "DICTATING" : "LISTENING") : "OFFLINE";
      toggleBtn.textContent = on ? "DISABLE VOICE" : "ENABLE VOICE";
      toggleBtn.disabled = !on && (!v.available || !v.model_ready);
      dlBtn.style.display = v.model_ready ? "none" : "";
      dlBtn.disabled = v.download != null || !v.available;
      bar.style.display = v.download != null ? "" : "none";
      bar.firstChild.style.width = `${(v.download || 0) * 100}%`;
      kStatus.v.textContent = v.status;
      kAvail.v.textContent = v.available ? "vosk + sounddevice" : "pip install vosk sounddevice";
      kAvail.v.style.color = v.available ? "var(--green)" : "var(--amber)";
      kModel.v.textContent = v.model_ready ? "ready" : "not downloaded";
      if (v.heard) heard.textContent = `“${v.heard}”`;
      const key = v.commands.join("|");
      if (key !== lastCmds) {
        lastCmds = key;
        chips.innerHTML = "";
        for (const c of v.commands) chips.append(h("span", {}, c));
      }
    },
  };
}
