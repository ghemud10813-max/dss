"""Command-line entry point.

    gazer                 desktop app (Qt shell + overlay + keyboard + tray)
    gazer --web           no Qt: engine + Command Deck in your browser
    gazer --demo          no webcam needed: a simulated user drives everything
                          (cursor output is virtual unless --real-input)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler


def _setup_logging(verbose: bool) -> None:
    from gazer.paths import log_path

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gazer", description="Control your whole computer with your eyes.")
    p.add_argument("--demo", action="store_true", help="simulated user instead of a webcam")
    p.add_argument("--real-input", action="store_true", help="in demo mode, move the real mouse cursor")
    p.add_argument("--web", action="store_true", help="run without Qt and open the deck in a browser")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window (--web)")
    p.add_argument("--port", type=int, default=None, help="local port for the Command Deck (default 8765)")
    p.add_argument("--profile", default=None, help="profile to load")
    p.add_argument("--data-dir", default=None, help="where profiles/config live (default: per-user app data)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def build(args: argparse.Namespace):
    from gazer.config import AppConfig, load_json
    from gazer.controller import Controller
    from gazer.paths import config_path
    from gazer.server import Hub

    config = load_json(AppConfig, config_path())
    if args.profile:
        config.last_profile = args.profile
    ctl = Controller(config, demo=args.demo, virtual_input=args.demo and not args.real_input)
    hub = Hub(ctl, port=args.port or config.port)
    return ctl, hub


def run_web(args: argparse.Namespace) -> int:
    ctl, hub = build(args)
    if not hub.start():
        print(hub.error, file=sys.stderr)
        return 1
    ctl.start()
    print(f"\n  GAZER Command Deck → {hub.url}\n", flush=True)
    if not args.no_browser:
        webbrowser.open(hub.url)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(0.5):
            pass
    finally:
        ctl.stop()
        hub.stop()
    return 0


def run_desktop(args: argparse.Namespace) -> int:
    try:
        from gazer.ui.desktop import run
    except ImportError as exc:
        print(f"Qt unavailable ({exc}); falling back to --web mode.", file=sys.stderr)
        return run_web(args)
    return run(args, build)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ["GAZER_DATA_DIR"] = args.data_dir
    _setup_logging(args.verbose)
    if args.web:
        return run_web(args)
    return run_desktop(args)


if __name__ == "__main__":
    sys.exit(main())
