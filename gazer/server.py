"""Local hub: serves the Command Deck and streams live telemetry over WebSocket.

Security: the hub can inject keystrokes, so it binds to 127.0.0.1 only,
requires a random per-launch token (in the page URL) on the socket, and
rejects WebSocket handshakes from foreign origins — a web page you happen to
visit cannot drive your PC through it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from aiohttp import WSMsgType, web

from gazer.controller import Controller
from gazer.core.engine import Snapshot
from gazer.core.features import PREVIEW_CONTOURS
from gazer.paths import ASSETS_DIR, PACKAGE_DIR

log = logging.getLogger("gazer.server")
WEB_DIR = PACKAGE_DIR / "web"


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def frame_payload(ctl: Controller, snap: Snapshot, extra: dict, with_mesh: bool) -> dict:
    sc = ctl.core.screen

    def norm(p):
        if p is None:
            return None
        nx, ny = sc.px_to_norm(*p)
        return [_r(nx), _r(ny)]

    f = snap.features
    head = list(f.matrix_angles) if f is not None else [0.0, 0.0, 0.0]
    events = []
    for ev in snap.events:
        k = ev[0]
        if k == "toast":
            events.append({"k": "toast", "text": ev[1]})
        elif k == "click":
            events.append({"k": "click", "p": norm(ev[1]), "button": ev[2]})
        elif k == "action":
            events.append({"k": "action", "action": ev[1]})
        elif k == "zone":
            events.append({"k": "zone", "zone": ev[1], "action": ev[2]})
        elif k == "mode":
            events.append({"k": "mode", "mode": ev[1]})
        elif k == "wellness":
            events.append({"k": "wellness", "kind": ev[1], "text": ev[2]})
        elif k == "request":
            events.append({"k": "request", "what": ev[1]})
        elif k == "dwell_next":
            events.append({"k": "dwell_next", "action": ev[1]})
    out = {
        "type": "frame", "t": _r(snap.t, 3), "face": snap.face, "control": snap.control, "paused": snap.paused,
        "calibrating": snap.calibrating, "mode": snap.mode, "mode_eff": snap.mode_effective,
        "pointer": norm(snap.pointer), "gaze": norm(snap.gaze), "gaze_ready": snap.gaze_ready,
        "dwell": _r(snap.dwell_progress, 3), "dwell_on": snap.dwell_enabled,
        "gv": {k: _r(v, 3) for k, v in snap.gesture_values.items()}, "ga": snap.gesture_active,
        "closure": [_r(snap.closure[0], 3), _r(snap.closure[1], 3)],
        "head": [_r(h, 2) for h in head], "blinked": snap.blinked,
        "scroll": snap.scrolling, "scroll_rate": [_r(snap.scroll_rate[0], 1), _r(snap.scroll_rate[1], 1)],
        "drag": snap.dragging, "precision": snap.precision,
        "wheel": None, "zoom": snap.zoom is not None,
        "zone": [snap.zone[0], _r(snap.zone[1], 3)] if snap.zone else None, "zones_on": snap.zones_enabled,
        "fps": _r(snap.fps, 1), "cam_fps": _r(snap.cam_fps, 1), "lat": _r(snap.latency_ms, 1),
        "q": _r(snap.quality, 3), "src": snap.source, "events": events,
        "dwell_next": snap.dwell_next, "target": None,
    }
    if snap.target is not None:
        t = snap.target
        x0, y0 = sc.px_to_norm(t.x, t.y)
        out["target"] = {"rect": [_r(x0), _r(y0), _r(t.w / sc.width), _r(t.h / sc.height)],
                         "kind": t.kind, "name": t.name}
    if snap.lighting is not None:
        out["lighting"] = snap.lighting
    if snap.wheel is not None:
        w = snap.wheel
        out["wheel"] = {"center": norm(w.center), "items": w.items, "hover": w.hover,
                        "progress": _r(w.progress, 3), "pointer": norm(w.pointer),
                        "radius": _r(w.radius / sc.width)}
    if with_mesh and snap.mesh is not None:
        out["mesh"] = np.round(snap.mesh.reshape(-1), 3).tolist()
    if "calib" in extra:
        out["calib"] = extra["calib"]
    if "insights" in extra:
        out["insights"] = extra["insights"]
    return out


_EYE_IDX = PREVIEW_CONTOURS["eyes"] + PREVIEW_CONTOURS["iris"]


def preview_jpeg(snap: Snapshot) -> bytes | None:
    img = snap.preview
    if img is None:
        return None
    img = img.copy()
    if snap.points is not None:
        pts = snap.points.astype(np.int32)
        for i in PREVIEW_CONTOURS["face"] + PREVIEW_CONTOURS["lips"]:
            if i < len(pts):
                cv2.circle(img, tuple(pts[i]), 1, (200, 200, 200), -1, cv2.LINE_AA)
        for i in _EYE_IDX:
            if i < len(pts):
                cv2.circle(img, tuple(pts[i]), 1, (198, 214, 61), -1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return buf.tobytes() if ok else None


class Client:
    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.mesh = False
        self.preview = False
        self.sending = False
        self.last_preview = 0.0


class Hub:
    def __init__(self, ctl: Controller, host: str = "127.0.0.1", port: int = 8765, token: str | None = None):
        self.ctl = ctl
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(18)
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gazer-cmd")
        self._state_pending = False
        self.error = ""
        ctl.listeners.append(self)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?t={self.token}"

    # ------------------------------------------------------------- lifecycle

    def start(self, timeout: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="gazer-hub", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return not self.error

    def stop(self) -> None:
        if self.loop is not None:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _shutdown(self) -> None:
        for c in list(self.clients):
            await c.ws.close()
        if self._runner:
            await self._runner.cleanup()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001
            self.error = f"Hub failed: {exc}"
            log.exception("hub")
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    async def _serve(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/assets/face_mesh.json", self._mesh)
        app.router.add_static("/static/", WEB_DIR, follow_symlinks=False)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        last_exc: Exception | None = None
        for port in range(self.port, self.port + 20):
            try:
                site = web.TCPSite(self._runner, self.host, port)
                await site.start()
                self.port = port
                return
            except OSError as exc:
                last_exc = exc
        raise RuntimeError(f"no free port: {last_exc}")

    # -------------------------------------------------------------- http

    async def _index(self, request: web.Request) -> web.StreamResponse:
        resp = web.FileResponse(WEB_DIR / "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def _mesh(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(ASSETS_DIR / "face_mesh.json")

    def _origin_ok(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin")
        if origin is None:  # non-browser clients (tests, tools)
            return True
        return origin in (f"http://{self.host}:{self.port}", f"http://localhost:{self.port}")

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if not secrets.compare_digest(request.query.get("t", ""), self.token) or not self._origin_ok(request):
            return web.Response(status=403, text="forbidden")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
        await ws.prepare(request)
        client = Client(ws)
        self.clients.add(client)
        log.info("Command Deck connected (%d client%s)", len(self.clients), "" if len(self.clients) == 1 else "s")
        try:
            await ws.send_json({"type": "hello", "catalog": self.ctl.catalog(), "token_ok": True})
            await ws.send_json({"type": "state", "state": self.ctl.state()})
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._on_message(client, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(client)
            self._update_wants()
        return ws

    async def _on_message(self, client: Client, data: str) -> None:
        try:
            msg = json.loads(data)
            cmd = str(msg.get("cmd", ""))
            args = msg.get("args") or {}
            mid = msg.get("id")
        except (ValueError, AttributeError):
            return
        if cmd == "subscribe":
            client.mesh = bool(args.get("mesh", client.mesh))
            client.preview = bool(args.get("preview", client.preview))
            self._update_wants()
            await client.ws.send_json({"type": "ack", "id": mid, "ok": True})
            return
        try:
            result = await asyncio.get_running_loop().run_in_executor(self._pool, self.ctl.command, cmd, args)
            reply = {"type": "ack", "id": mid, "ok": True, "result": result}
        except (ValueError, KeyError, TypeError) as exc:
            reply = {"type": "ack", "id": mid, "ok": False, "error": str(exc) or exc.__class__.__name__}
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s", cmd)
            reply = {"type": "ack", "id": mid, "ok": False, "error": f"Internal error: {exc}"}
        await client.ws.send_json(reply)

    def _update_wants(self) -> None:
        self.ctl.runner.want_mesh = any(c.mesh for c in self.clients)
        self.ctl.runner.want_preview = any(c.preview for c in self.clients)

    # --------------------------------------------------------- listeners

    def on_frame(self, snap: Snapshot, extra: dict) -> None:
        loop = self.loop
        if loop is None or not self.clients:
            return
        want_mesh = any(c.mesh for c in self.clients)
        full = json.dumps(frame_payload(self.ctl, snap, extra, want_mesh), separators=(",", ":"))
        lite = full
        if want_mesh and not all(c.mesh for c in self.clients):
            lite = json.dumps(frame_payload(self.ctl, snap, extra, False), separators=(",", ":"))
        jpeg = None
        if snap.preview is not None:
            now = time.perf_counter()
            if any(c.preview and now - c.last_preview > 1 / 15 for c in self.clients):
                jpeg = preview_jpeg(snap)
        loop.call_soon_threadsafe(self._broadcast, full, lite, jpeg, "calib" in extra or "insights" in extra
                                  or bool(snap.events))

    def _broadcast(self, full: str, lite: str, jpeg: bytes | None, important: bool) -> None:
        now = time.perf_counter()
        for c in list(self.clients):
            if c.sending and not important:
                continue  # slow client: drop this frame rather than queue it
            payload = full if c.mesh else lite
            img = jpeg if (jpeg is not None and c.preview and now - c.last_preview > 1 / 15) else None
            if img is not None:
                c.last_preview = now
            asyncio.ensure_future(self._send(c, payload, img))

    async def _send(self, c: Client, text: str, img: bytes | None) -> None:
        c.sending = True
        try:
            await c.ws.send_str(text)
            if img is not None:
                await c.ws.send_bytes(img)
        except Exception:
            pass
        finally:
            c.sending = False

    def on_state(self) -> None:
        loop = self.loop
        if loop is None or self._state_pending:
            return
        self._state_pending = True
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._push_state()))

    async def _push_state(self) -> None:
        await asyncio.sleep(0.05)  # coalesce bursts
        self._state_pending = False
        if not self.clients:
            return
        try:
            state = await asyncio.get_running_loop().run_in_executor(self._pool, self.ctl.state)
        except Exception:
            log.exception("state")
            return
        text = json.dumps({"type": "state", "state": state}, separators=(",", ":"))
        for c in list(self.clients):
            try:
                await c.ws.send_str(text)
            except Exception:
                pass


def serve_forever(ctl: Controller, hub: Hub) -> None:  # pragma: no cover - CLI helper
    stop = threading.Event()
    ctl.start()
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        ctl.stop()


__all__ = ["Hub", "frame_payload", "WEB_DIR", "Path"]
