#!/usr/bin/env bash
# Gazer setup for Linux / macOS: creates .venv, installs everything, runs the tests.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest
python -c "from gazer.core.assets import ensure_face_landmarker; print(ensure_face_landmarker())"
QT_QPA_PLATFORM=offscreen python -m pytest -q
echo
echo "Ready!  Start with:   .venv/bin/python -m gazer"
echo "No webcam? Try:       .venv/bin/python -m gazer --demo"
