"""User profiles: settings, gaze model, calibration history, stats, vocabulary."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gazer.config import ProfileSettings, load_json, save_json
from gazer.core.gaze import GazeEstimator
from gazer.paths import profiles_dir

_NAME_RE = re.compile(r"^[\w\- ]{1,40}$")


@dataclass
class CalibRecord:
    when: float
    preset: str
    samples: int
    mean_px: float
    grade: str


@dataclass
class UsageStats:
    sessions: int = 0
    seconds_active: float = 0.0
    clicks: int = 0
    keys_typed: int = 0
    gestures: int = 0
    implicit_samples: int = 0
    created: float = field(default_factory=time.time)


class Profile:
    def __init__(self, name: str, directory: Path):
        self.name = name
        self.dir = directory
        self.settings = ProfileSettings()
        self.gaze = GazeEstimator()
        self.history: list[CalibRecord] = []
        self.stats = UsageStats()
        self.words: dict[str, int] = {}

    # files
    @property
    def settings_path(self) -> Path:
        return self.dir / "settings.json"

    @property
    def gaze_path(self) -> Path:
        return self.dir / "gaze.npz"

    def load(self) -> "Profile":
        self.settings = load_json(ProfileSettings, self.settings_path)
        self.gaze.load(self.gaze_path)
        try:
            raw = json.loads((self.dir / "history.json").read_text(encoding="utf-8"))
            self.history = [CalibRecord(**r) for r in raw]
        except (OSError, ValueError, TypeError):
            self.history = []
        try:
            self.stats = UsageStats(**json.loads((self.dir / "stats.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self.stats = UsageStats()
        try:
            self.words = {str(k): int(v) for k, v in
                          json.loads((self.dir / "words.json").read_text(encoding="utf-8")).items()}
            # keep the file small: drop one-off pairs once it grows large
            if len(self.words) > 20000:
                self.words = {k: v for k, v in self.words.items() if ">" not in k or v > 1}
        except (OSError, ValueError, TypeError, AttributeError):
            self.words = {}
        return self

    def save_settings(self) -> None:
        save_json(self.settings, self.settings_path)

    def save_gaze(self) -> None:
        self.gaze.save(self.gaze_path)

    def save_meta(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "history.json").write_text(json.dumps([asdict(h) for h in self.history], indent=2),
                                               encoding="utf-8")
        (self.dir / "stats.json").write_text(json.dumps(asdict(self.stats), indent=2), encoding="utf-8")
        (self.dir / "words.json").write_text(json.dumps(self.words), encoding="utf-8")

    def save_all(self) -> None:
        self.save_settings()
        self.save_gaze()
        self.save_meta()

    def add_calibration(self, preset: str, samples: int, mean_px: float, grade: str) -> None:
        self.history.append(CalibRecord(time.time(), preset, samples, mean_px, grade))
        self.history = self.history[-50:]


class ProfileStore:
    def __init__(self, base: Path | None = None):
        self.base = base or profiles_dir()
        self.base.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def valid_name(name: str) -> bool:
        return bool(_NAME_RE.match(name.strip())) and name.strip() not in (".", "..")

    def names(self) -> list[str]:
        return sorted(p.name for p in self.base.iterdir() if p.is_dir() and (p / "settings.json").exists())

    def exists(self, name: str) -> bool:
        return (self.base / name / "settings.json").exists()

    def create(self, name: str, settings: ProfileSettings | None = None) -> Profile:
        name = name.strip()
        if not self.valid_name(name):
            raise ValueError("Use letters, numbers, spaces, - or _ (max 40).")
        if self.exists(name):
            raise ValueError(f"Profile '{name}' already exists.")
        p = Profile(name, self.base / name)
        if settings is not None:
            p.settings = settings
        p.dir.mkdir(parents=True, exist_ok=True)
        p.save_all()
        return p

    def load(self, name: str) -> Profile:
        return Profile(name, self.base / name).load()

    def delete(self, name: str) -> None:
        d = self.base / name
        if d.is_dir() and d.parent == self.base:
            shutil.rmtree(d)

    def duplicate(self, src: str, dst: str) -> Profile:
        dst = dst.strip()
        if not self.valid_name(dst) or self.exists(dst):
            raise ValueError("Invalid or existing name.")
        shutil.copytree(self.base / src, self.base / dst)
        return self.load(dst)

    def rename(self, src: str, dst: str) -> None:
        dst = dst.strip()
        if not self.valid_name(dst) or self.exists(dst):
            raise ValueError("Invalid or existing name.")
        (self.base / src).rename(self.base / dst)
