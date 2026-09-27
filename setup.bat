@echo off
REM Gazer setup for Windows: creates .venv, installs everything, runs the tests.
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :error
python -m pip install pytest
echo.
echo Downloading the MediaPipe face model...
python -c "from gazer.core.assets import ensure_face_landmarker; print(ensure_face_landmarker())" || goto :error
echo.
echo Running tests...
set QT_QPA_PLATFORM=offscreen
python -m pytest -q
set QT_QPA_PLATFORM=
echo.
echo Ready!  Start with:   .venv\Scripts\python -m gazer
echo No webcam? Try:       .venv\Scripts\python -m gazer --demo
goto :eof
:error
echo Setup failed.
exit /b 1
