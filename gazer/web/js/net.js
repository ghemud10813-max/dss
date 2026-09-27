// WebSocket link to the Gazer engine: requests with acks, live frames, state.

const listeners = new Map();
let ws = null;
let nextId = 1;
const pending = new Map();
let wants = { mesh: false, preview: false };
let connected = false;
let backoff = 500;

const params = new URLSearchParams(location.search);
const token = params.get("t") || sessionStorage.getItem("gazer-token") || "";
if (params.get("t")) sessionStorage.setItem("gazer-token", token);

export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type).delete(fn);
}

function emit(type, data) {
  const set = listeners.get(type);
  if (set) for (const fn of set) { try { fn(data); } catch (e) { console.error(e); } }
}

export function isConnected() { return connected; }

export function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?t=${encodeURIComponent(token)}`;
  ws = new WebSocket(url);
  ws.binaryType = "blob";
  ws.onopen = () => {
    connected = true;
    backoff = 500;
    emit("link", true);
    send({ cmd: "subscribe", args: wants });
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") { emit("preview", ev.data); return; }
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "ack") {
      const p = pending.get(msg.id);
      if (p) {
        pending.delete(msg.id);
        msg.ok ? p.resolve(msg.result) : p.reject(new Error(msg.error || "failed"));
      }
      return;
    }
    emit(msg.type, msg.type === "state" ? msg.state : msg);
  };
  ws.onclose = () => {
    const was = connected;
    connected = false;
    for (const p of pending.values()) p.reject(new Error("link lost"));
    pending.clear();
    if (was) emit("link", false);
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 1.6, 5000);
  };
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

export function request(cmd, args = {}) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== 1) { reject(new Error("not connected")); return; }
    const id = nextId++;
    pending.set(id, { resolve, reject });
    send({ id, cmd, args });
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error("timeout")); } }, 15000);
  });
}

export function subscribe(opts) {
  wants = { ...wants, ...opts };
  if (ws && ws.readyState === 1) request("subscribe", wants).catch(() => {});
}
