# Analysis of the original Gazer project

## Verdict: not a wasted project, an unfinished one

The uploaded `gazer.zip` held two generations of the app:

- **v1** (`archive/gazer_v1`): a PyTorch network mapping eye features to the screen, blink-to-click, and a 15-phase calibration.
- **v2** (the main tree): a rewrite with a genuinely good engine and no application around it.

The v2 engine is solid work. All 34 of its original unit tests pass unchanged. What it gets right:

| Area | What was there |
|---|---|
| Gaze model | Quadratic ridge regression with cross-validated regularization and robust outlier rejection. For webcam gaze (little data, noisy labels) this beats a neural net: it trains in milliseconds and refits online. |
| Learning from use | Every click becomes a labelled sample (you were looking at what you clicked), with an immediate bias correction and background refits. |
| Pointing | One-Euro filtering, a saccade-aware deadband, MAGIC-style hybrid pointing (eyes jump, head refines), and three head modes. |
| Gestures | 14 face gestures from MediaPipe blendshapes, with hysteresis, minimum hold, neutral-face subtraction and flexible bindings (start / hold / tap / while-held). |
| "Midas touch" answers | Dwell click, a radial action wheel, and a zoom lens for small targets. |
| Calibration | Fixation-based sampling: samples only after the eyes settle, blinks rejected, accuracy measured on held-out points. |
| OS input | `SendInput` on Windows, pynput elsewhere, and a recording backend for tests. |

## Why it felt broken

It could not start. The rewrite stopped halfway:

1. **No application module.** `main.py` and `gazer/__main__.py` both import `gazer.app`, which didn't exist in v2, so launching crashed immediately.
2. **No controller.** Every UI page took a `ctl` object with ~15 methods (`set_control`, `start_calibration`, `settings_changed`…) that was never written.
3. **No main window.** Three pages existed (dashboard, pointer, calibrate), but nothing assembled them. Gestures, voice, profiles and settings had no UI at all.
4. **Hotkeys** were configured in `config.py` but never implemented.
5. **Stale packaging.** `README.md` and `setup.bat` described v1 (`tests/test_smoke.py`, `gazer.models` no longer exist). `requirements.txt` still pulled ~2 GB of `torch`/`scikit-learn`/`joblib` that v2 never imports.
6. **Calibration buried in a widget.** The calibration logic lived inside a Qt paint widget, so it was untestable and tied to one UI.
7. **Eye control depended on the face.** Clicking needed smiles, winks or dwell. There was no deliberate eyes-only click that ignores natural blinking (v1's blink-to-click had fired on every normal blink).

## What changed in this version

| Problem | Resolution |
|---|---|
| Couldn't launch | `gazer/app.py`: desktop, `--web` and `--demo` modes. |
| No controller | `gazer/controller.py`: UI-agnostic owner of engine, profiles, calibration, voice, hotkeys and insights, with a validated command API and autosave. |
| No UI shell | A cinematic web **Command Deck** (Three.js, bloom, 8 views, boot sequence, onboarding) served by a local hub, plus a Qt desktop layer (HUD overlay, focus-free keyboard, tray, embedded deck). |
| Hotkeys | `core/hotkeys.py` (pynput global hotkeys). |
| Packaging | New README, `setup.bat`/`setup.sh`, lean `requirements.txt`, `pyproject.toml` with a `gazer` command. |
| Untestable calibration | `core/calib_session.py`: a state machine driven by snapshots, with hands-free gaze-dwell SAVE/RETRY/CANCEL. |
| Eyes-only control | Double-blink click (natural blinks ignored), gaze hot-zones past the screen edges (scroll, back/forward, wheel, zoom, keyboard, pause), an "Eyes only" control style as the default, and head-control fallback until calibrated. |
| Couldn't be tested without a webcam | `core/simulator.py`: a synthetic pilot rendering all 478 landmarks from a canonical face mesh, with saccades, head motion, blinks and expressions, feeding the real pipeline. |
| No feedback on eye health | `core/insights.py`: heatmap, I-DT fixations, reading time, blink rate, 20-20-20 reminders. |

Bugs found and fixed along the way:

- QtWebEngine has to be imported before `QApplication` is created. Otherwise the embedded deck silently fell back to the browser.
- The deck would crash entirely without WebGL. It now degrades to a 2D display.
- Demo mode would have written a simulator-trained gaze model into your real profile. It now uses its own data folder.
- Mediapipe 1.0 (new major version) was verified working with the existing tracker code.

## How it was verified

- **51 automated tests.** The synthetic pilot drives the real feature extraction, calibration, regression, pointer fusion and gesture pipeline. It measures:
  - calibration accuracy ("Excellent", about 12 px on held-out points),
  - eyes-only cursor error (under 20 px),
  - one click per double blink,
  - zero clicks across 20 s of natural blinking.
- **The real MediaPipe tracker** was run on a test photograph to confirm landmarks, blendshapes and features.
- **Every deck view** was screenshotted in headless Chromium in demo mode, including a full calibration run end to end, with zero console errors.
- **The Qt overlay and keyboard** were rendered offscreen. The full desktop app was launched with the embedded QtWebEngine deck, which connected to the hub.

**Not verified here:** a real webcam and a real display. The cloud container has neither, so real-world gaze accuracy, camera backends and on-screen input injection still need a run on your machine. `python -m gazer --demo` is the quickest check that everything is wired up before plugging in the camera.

## Ideas for next steps

- **Snap-to-target.** Use OS accessibility APIs (UI Automation / AT-SPI) to pull the gaze cursor onto the nearest clickable element. This is the biggest possible accuracy win for eyes-only use.
- **Dasher-style or swipe gaze typing** for faster text entry than key-by-key dwell.
- **Per-app profiles** (e.g. reading mode in the browser, precision mode in design tools).
- **Multi-monitor gaze**, choosing the monitor from head pose.
- **A small CNN gaze model** trained from the implicit click samples collected over weeks of use.
