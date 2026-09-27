@echo off
echo Installing dependencies...
python -m pip install -r requirements.txt
echo.
echo Downloading MediaPipe face model...
python -c "from gazer.models import ensure_face_landmarker; ensure_face_landmarker()"
echo.
echo Running smoke tests...
python tests/test_smoke.py
echo.
echo Ready! Run: python main.py
