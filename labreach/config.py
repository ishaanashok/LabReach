"""Paths and settings loading. All runtime data lives in ./labreach_data (gitignored)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HARD_DAILY_CAP = 10


def data_dir() -> Path:
    path = Path(os.environ.get("LABREACH_DATA_DIR", "labreach_data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "labreach.db"


def stop_file() -> Path:
    return data_dir() / "STOP"


def load_settings(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("LABREACH_SETTINGS", REPO_ROOT / "config" / "settings.yaml"))
    settings = yaml.safe_load(Path(path).read_text())
    private = REPO_ROOT / "data" / "private" / "contact.yaml"   # gitignored: date of birth, phone
    if private.exists():
        settings["student"].update({k: v for k, v in (yaml.safe_load(private.read_text()) or {}).items() if k == "born"})
    return settings


def is_live() -> bool:
    """Live sending needs BOTH the env var and settings mode; dry-run is the default."""
    return os.environ.get("LABREACH_MODE", "").lower() == "live"


def student_age(born: str, today: date) -> int:
    b = date.fromisoformat(born)
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))
