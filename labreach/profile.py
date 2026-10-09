"""Load the student profile and enforce one-time approval (hash stored in settings_state)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import yaml

from .db import get_state, log_event, set_state
from .models import StudentProfile

PROFILE_KEY = "profile_approved_hash"


def load_profile(path: Path) -> StudentProfile:
    return StudentProfile.model_validate(yaml.safe_load(Path(path).read_text()))


def profile_hash(profile: StudentProfile) -> str:
    canonical = json.dumps(profile.model_dump(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def approve_profile(conn: sqlite3.Connection, profile: StudentProfile) -> str:
    digest = profile_hash(profile)
    set_state(conn, PROFILE_KEY, digest)
    log_event(conn, "profile_approved", sha256=digest, facts=[f.id for f in profile.facts])
    return digest


def is_approved(conn: sqlite3.Connection, profile: StudentProfile) -> bool:
    return get_state(conn, PROFILE_KEY) == profile_hash(profile)
