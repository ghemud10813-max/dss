"""The local hub: security, protocol and commands against a live demo engine."""

import asyncio
import json

import pytest

aiohttp = pytest.importorskip("aiohttp")

from gazer.config import AppConfig  # noqa: E402
from gazer.controller import Controller  # noqa: E402
from gazer.core.profiles import ProfileStore  # noqa: E402
from gazer.server import Hub  # noqa: E402


@pytest.fixture
def live(tmp_path):
    ctl = Controller(AppConfig(), demo=True, store=ProfileStore(tmp_path / "profiles"), persist_config=False)
    hub = Hub(ctl, port=18765)
    assert hub.start()
    ctl.start()
    yield ctl, hub
    ctl.stop()
    hub.stop()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _session(hub, fn, **kw):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{hub.port}/ws?t={hub.token}", **kw) as ws:
            return await fn(ws)


async def _recv_until(ws, pred, limit=400):
    for _ in range(limit):
        m = await ws.receive(timeout=5)
        if m.type != aiohttp.WSMsgType.TEXT:
            continue
        d = json.loads(m.data)
        if pred(d):
            return d
    raise AssertionError("message not received")


def test_rejects_bad_token_and_foreign_origin(live):
    _, hub = live

    async def go():
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"http://127.0.0.1:{hub.port}/ws?t=wrong")
            assert r.status == 403
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await s.ws_connect(f"http://127.0.0.1:{hub.port}/ws?t={hub.token}", origin="https://evil.example")
            r = await s.get(f"http://127.0.0.1:{hub.port}/")
            assert r.status == 200 and "Gazer" in await r.text()
    run(go())


def test_hello_state_and_frames(live):
    ctl, hub = live

    async def go(ws):
        hello = await _recv_until(ws, lambda d: d["type"] == "hello")
        assert "double_blink" in hello["catalog"]["gestures"]
        state = await _recv_until(ws, lambda d: d["type"] == "state")
        assert state["state"]["profile"]["gaze"]["ready"]  # demo starts calibrated
        await ws.send_json({"id": 1, "cmd": "subscribe", "args": {"mesh": True}})
        frame = await _recv_until(ws, lambda d: d["type"] == "frame" and "mesh" in d)
        assert len(frame["mesh"]) == 478 * 3 and frame["face"] and frame["src"] == "simulator"
    run(_session(hub, go))


def test_commands_patch_settings_and_report_errors(live):
    ctl, hub = live

    async def go(ws):
        await ws.send_json({"id": 1, "cmd": "settings", "args": {"path": "dwell.time_ms", "value": 1400}})
        ack = await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 1)
        assert ack["ok"] and ctl.profile.settings.dwell.time_ms == 1400
        await ws.send_json({"id": 2, "cmd": "settings", "args": {"path": "dwell.nope", "value": 1}})
        ack = await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 2)
        assert not ack["ok"] and "unknown" in ack["error"]
        await ws.send_json({"id": 3, "cmd": "perform", "args": {"action": "rm -rf"}})
        ack = await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 3)
        assert not ack["ok"]
        await ws.send_json({"id": 4, "cmd": "control", "args": {"on": True}})
        await _recv_until(ws, lambda d: d["type"] == "frame" and d["control"])
        await ws.send_json({"id": 5, "cmd": "calibrate", "args": {"kind": "gaze", "preset": "quick"}})
        ack = await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 5)
        assert ack["result"]["active"] and ack["result"]["native_window"] is False
        f = await _recv_until(ws, lambda d: d["type"] == "frame" and d.get("calib"))
        assert f["calib"]["state"] == "waiting"
        await ws.send_json({"id": 6, "cmd": "calib", "args": {"what": "cancel"}})
        await _recv_until(ws, lambda d: d["type"] == "state" and not d["state"]["calibrating"])
    run(_session(hub, go))


def test_profiles_via_commands(live):
    ctl, hub = live

    async def go(ws):
        await ws.send_json({"id": 1, "cmd": "profile_create", "args": {"name": "Head user", "style": "head"}})
        await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 1)
        st = await _recv_until(ws, lambda d: d["type"] == "state" and d["state"]["profile"]["name"] == "Head user")
        assert st["state"]["profile"]["settings"]["pointer"]["mode"] == "head_mouse"
        await ws.send_json({"id": 2, "cmd": "profile_delete", "args": {"name": "Head user"}})
        ack = await _recv_until(ws, lambda d: d["type"] == "ack" and d["id"] == 2)
        assert not ack["ok"]  # cannot delete the active profile
    run(_session(hub, go))
