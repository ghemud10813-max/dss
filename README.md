# Gazer

Eye gaze cursor control desktop app for Windows/Linux.

Tracks your eyes via webcam, moves the mouse cursor in real time, and supports blink-to-click with personalized calibration profiles.

## Quick start (Windows)

```bat
setup.bat
python main.py
```

Or manually:

```bash
pip install -r requirements.txt
python tests/test_smoke.py    # verify everything works
python main.py
```

On first run, the MediaPipe face model (~4 MB) downloads automatically to `gazer/assets/`.

## Usage flow

1. **Profile** — choose one of:
   - **Use profile — skip calibration** → instant cursor (if already trained)
   - **Calibrate profile** → full 15-phase training
   - **Retrain profile** → append data + merge retrain
   - **New profile** → first-time setup
2. **Training duration** — Quick (1 min) / Standard (3 min) / Deep (5 min) / Custom
3. **Calibration** — follow the white dot; green dot shows live gaze prediction tightening over time
4. **Verification** — 30s figure-8 accuracy test
5. **Cursor control** — blink to click; runtime window has pause + dwell-click options

## Controls

| Action | How |
|--------|-----|
| Move cursor | Look at screen |
| Click | Blink (personalized EAR threshold) |
| Dwell-click | Enable in runtime window (hold gaze ~800ms) |
| Pause cursor | Runtime window → Pause cursor |
| Quit | Runtime window → Quit Gazer |

## Profiles

Saved under `profiles/<name>/`:

| File | Contents |
|------|----------|
| `dataset.npz` | All calibration samples (features + targets) |
| `model.pt` | PyTorch gaze regression weights |
| `meta.json` | Sessions, sample count, EAR baseline, last error |

## Architecture

```
Webcam → MediaPipe Face Landmarker (478 pts + iris)
       → Feature vector [iris ratios, head pose, face scale]
       → PyTorch NN → screen (x, y)
       → One Euro Filter → pynput cursor @ 60Hz
```

Worker thread handles camera + inference; main Qt thread handles overlay + cursor.

## Requirements

- Python 3.10+
- Webcam with permissions enabled
- Windows 10+ (DirectShow) or Linux (V4L2)
- ~500 MB disk for dependencies

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Camera won't open | Close Zoom/Teams; check Settings → Privacy → Camera |
| No face detected | Face the camera; improve lighting |
| Green dot far from white dot early on | Normal — wait ~15 samples (~2s) for first model fit |
| Head movement breaks sync | Phase 14 (Head-Robustness) trains this — use Standard or Deep duration |
| PyQt6 DLL error | `pip install --force-reinstall PyQt6 PyQt6-Qt6` |

## Accuracy grades

| Avg error | Grade |
|-----------|-------|
| < 30 px | Excellent |
| 30–60 px | Good |
| 60–100 px | Fair |
| > 100 px | Redo calibration |

## Tests

```bash
python tests/test_smoke.py
```

Verifies: filter, model, profiles, 15 phases, MediaPipe model, camera.
