"""Filesystem locations for app data, profiles and downloadable models."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"


def data_dir() -> Path:
    """Per-user data directory (override with GAZER_DATA_DIR)."""
    override = os.environ.get("GAZER_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Gazer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Gazer"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "gazer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def profiles_dir() -> Path:
    d = data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "gazer.log"
