"""Browser smoke test of the Command Deck (opt-in: GAZER_BROWSER_TESTS=1).

Loads the real deck in headless Chromium against a demo engine, visits every
view, opens the Gaze Trainer and fails on any JavaScript error.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

if os.environ.get("GAZER_BROWSER_TESTS") != "1":
    pytest.skip("set GAZER_BROWSER_TESTS=1 to run browser tests", allow_module_level=True)
playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent


def test_deck_views_load_without_js_errors(tmp_path):
    env = dict(os.environ, GAZER_DATA_DIR=str(tmp_path), PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-m", "gazer", "--web", "--demo", "--no-browser", "--port", "18990"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        url = None
        deadline = time.time() + 30
        while time.time() < deadline and url is None:
            line = proc.stdout.readline()
            m = re.search(r"(http://127\.0\.0\.1:\d+/\?t=\S+)", line or "")
            url = m.group(1) if m else None
        assert url, "deck URL not printed"
        errors = []
        with playwright.sync_playwright() as p:
            exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
            browser = p.chromium.launch(executable_path=exe, args=["--use-gl=angle", "--use-angle=swiftshader",
                                                                   "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1500, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.add_init_script("sessionStorage.setItem('gazer-booted','1')")
            page.goto(url + "#deck")
            page.wait_for_selector(".reactor", timeout=30000)
            page.evaluate("document.getElementById('modal-layer').hidden = true")
            for btn in page.query_selector_all("#rail .rail-btn"):
                btn.click()
                page.wait_for_timeout(400)
            assert page.evaluate("document.querySelectorAll('.view').length") == 9
            page.click("#rail .rail-btn[title^='TRAINER']")
            page.click(".trainer-level")
            page.wait_for_timeout(1500)
            assert not page.evaluate("document.getElementById('trainer-layer').hidden")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.evaluate("document.getElementById('trainer-layer').hidden")
            browser.close()
        assert not errors, errors
    finally:
        proc.terminate()
        proc.wait(timeout=10)
