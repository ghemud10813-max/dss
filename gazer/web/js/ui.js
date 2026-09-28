// Tiny DOM toolkit + state-bound controls.

import { request } from "./net.js";
import { sfx } from "./sound.js";

export const store = {
  catalog: null,
  state: null,
  frame: null,
  insights: null,
};

const subs = new Map();
export function onStore(key, fn) {
  if (!subs.has(key)) subs.set(key, new Set());
  subs.get(key).add(fn);
}
export function setStore(key, value) {
  store[key] = value;
  const s = subs.get(key);
  if (s) for (const fn of s) { try { fn(value); } catch (e) { console.error(e); } }
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

export function toast(text, kind = "") {
  window.dispatchEvent(new CustomEvent("gazer-toast", { detail: { text, kind } }));
}

export async function act(cmd, args) {
  try {
    return await request(cmd, args);
  } catch (e) {
    toast(e.message, "warn");
    sfx.fail();
    throw e;
  }
}

// ------------------------------------------------------------------ binders

const binders = new Set();
export function refreshBindings() {
  for (const b of binders) { try { b(); } catch (e) { console.error(e); } }
}
function bindUpdate(fn) { binders.add(fn); fn(); return fn; }

export const settingsOf = () => store.state?.profile?.settings;
export const configOf = () => store.state?.config;

function sender(scope, path) {
  return (value) => act(scope === "app" ? "app_settings" : "settings", { path, value }).catch(() => {});
}

// ------------------------------------------------------------------- panels

let panelN = 0;
export function panel({ title, code, tag, tagClass = "", desc, hot = false, cls = "" } = {}) {
  panelN += 1;
  const tagEl = tag ? h("span", { class: `panel-tag ${tagClass}` }, tag) : null;
  const el = h("section", { class: `panel ${hot ? "hot" : ""} ${cls}` },
    title ? h("div", { class: "panel-head" },
      h("span", { class: "panel-code" }, code || `//${String(panelN).padStart(2, "0")}`),
      h("span", { class: "panel-title" }, title),
      h("span", { class: "spacer" }),
      tagEl) : null,
    desc ? h("div", { class: "panel-desc" }, desc) : null);
  return { el, tagEl, add: (...c) => { el.append(...c.flat().filter(Boolean)); return el; } };
}

// ----------------------------------------------------------------- controls

export function slider({ label, path, scope = "profile", min, max, step, fmt = (v) => v.toFixed(2), tip, onInput }) {
  const input = h("input", { type: "range", min, max, step, "aria-label": label });
  const val = h("span", { class: "val" });
  const el = h("div", { class: "slider", title: tip || "" }, h("label", {}, label), input, val);
  const send = path ? sender(scope, path) : null;
  let dragging = false;
  let timer = null;
  const paint = (v) => {
    val.textContent = fmt(Number(v));
    input.style.setProperty("--p", `${((v - min) / (max - min)) * 100}%`);
  };
  input.addEventListener("input", () => {
    dragging = true;
    const v = Number(input.value);
    paint(v);
    onInput && onInput(v);
    clearTimeout(timer);
    if (send) timer = setTimeout(() => send(v), 120);
  });
  input.addEventListener("change", () => { dragging = false; clearTimeout(timer); if (send) send(Number(input.value)); sfx.tap(); });
  if (path) {
    bindUpdate(() => {
      if (dragging) return;
      const src = scope === "app" ? configOf() : settingsOf();
      const v = getPath(src, path);
      if (v != null) { input.value = v; paint(v); }
    });
  }
  return { el, input, set: (v) => { input.value = v; paint(v); } };
}

export function toggle({ label, sub, path, scope = "profile", onChange, checked }) {
  const input = h("input", { type: "checkbox" });
  const el = h("label", { class: "toggle" }, input, h("span", { class: "sw" }),
    h("span", { class: "t-label" }, label, sub ? h("span", { class: "t-sub" }, sub) : null));
  input.addEventListener("change", () => {
    input.checked ? sfx.toggleOn() : sfx.toggleOff();
    if (onChange) onChange(input.checked);
    else if (path) sender(scope, path)(input.checked);
  });
  if (path) {
    bindUpdate(() => {
      const src = scope === "app" ? configOf() : settingsOf();
      const v = getPath(src, path);
      if (v != null) input.checked = !!v;
    });
  } else if (checked != null) input.checked = checked;
  return { el, input, set: (v) => { input.checked = !!v; } };
}

export function select({ options, path, scope = "profile", value, onChange, label }) {
  const el = h("select", { "aria-label": label || path || "" });
  const fill = (opts) => {
    el.innerHTML = "";
    for (const [k, v] of Object.entries(opts)) el.append(h("option", { value: k }, v));
  };
  fill(typeof options === "function" ? options() : options);
  el.addEventListener("change", () => {
    sfx.tap();
    const v = el.value;
    if (onChange) onChange(v);
    else if (path) sender(scope, path)(isNaN(v) || v === "" || typeof getPath(scope === "app" ? configOf() : settingsOf(), path) === "string" ? v : Number(v));
  });
  if (path) {
    bindUpdate(() => {
      if (document.activeElement === el) return;
      const src = scope === "app" ? configOf() : settingsOf();
      const v = getPath(src, path);
      if (v != null) el.value = String(v);
    });
  } else if (value != null) el.value = value;
  return { el, fill, set: (v) => { el.value = v; } };
}

export function field(label, control) {
  return h("div", { class: "field" }, h("label", {}, label), control);
}

export function button(label, onClick, cls = "") {
  const b = h("button", { class: `btn ${cls}`, type: "button" }, label);
  b.addEventListener("click", (e) => { sfx.tap(); onClick && onClick(e); });
  b.addEventListener("mouseenter", () => sfx.hover());
  return b;
}

export function kv(label) {
  const v = h("span");
  return { el: h("div", { class: "kv" }, h("span", {}, label), v), v };
}

export function actionOptions() {
  return store.catalog ? store.catalog.actions : { none: "— nothing —" };
}

export function fmtDur(s) {
  if (s == null) return "—";
  s = Math.max(0, s);
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}:${String(ss).padStart(2, "0")}`;
}

// ------------------------------------------------------------------- sparkline

export class Spark {
  constructor(canvas, { color = "#3ee8d8", max = null, len = 90 } = {}) {
    this.c = canvas; this.color = color; this.max = max; this.len = len; this.data = [];
  }
  push(v) {
    this.data.push(v);
    if (this.data.length > this.len) this.data.shift();
  }
  draw() {
    const c = this.c;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, hgt = c.clientHeight;
    if (!w) return;
    if (c.width !== w * dpr) { c.width = w * dpr; c.height = hgt * dpr; }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, hgt);
    const d = this.data;
    if (d.length < 2) return;
    const mx = this.max ?? Math.max(...d, 1e-6) * 1.15;
    g.beginPath();
    d.forEach((v, i) => {
      const x = (i / (this.len - 1)) * w;
      const y = hgt - (Math.min(v, mx) / mx) * (hgt - 2) - 1;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.strokeStyle = this.color;
    g.lineWidth = 1.4;
    g.shadowColor = this.color;
    g.shadowBlur = 6;
    g.stroke();
    g.lineTo(((d.length - 1) / (this.len - 1)) * w, hgt);
    g.lineTo(0, hgt);
    const grad = g.createLinearGradient(0, 0, 0, hgt);
    grad.addColorStop(0, this.color + "40");
    grad.addColorStop(1, this.color + "00");
    g.fillStyle = grad;
    g.shadowBlur = 0;
    g.fill();
  }
}

export const ICONS = {
  deck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 12S5.5 4.5 12 4.5 22.5 12 22.5 12 18.5 19.5 12 19.5 1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3.6"/><circle cx="12" cy="12" r="1" fill="currentColor"/></svg>',
  calibrate: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/></svg>',
  gestures: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="10"/><path d="M7.5 14.5s1.6 2.5 4.5 2.5 4.5-2.5 4.5-2.5"/><path d="M8 9.5h1.5M14.5 9.5H16"/></svg>',
  pointer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 3l7.5 18 2.4-7.6L21.5 11z"/></svg>',
  zones: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="13" rx="1"/><path d="M3 9V5h4M17 5h4v4M21 14v4h-4M7 18H3v-4"/></svg>',
  trainer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3"/></svg>',
  insights: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 20h18"/><path d="M6 16v-5M10 16V7M14 16v-8M18 16V4"/></svg>',
  voice: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3.5"/></svg>',
  system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z"/></svg>',
  soundOn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13"/></svg>',
  soundOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 5L6 9H2v6h4l5 4z"/><path d="M22 9l-6 6M16 9l6 6"/></svg>',
  full: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9V3h6M21 9V3h-6M3 15v6h6M21 15v6h-6"/></svg>',
};
