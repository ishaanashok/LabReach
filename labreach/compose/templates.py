"""Template loading, hashing and locking.

Approval writes config/templates/LOCK.json with a SHA-256 per template. Rendering for sending refuses any
template whose hash no longer matches, so the structure cannot drift without a new approval.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..config import REPO_ROOT

TEMPLATE_DIR = REPO_ROOT / "config" / "templates"
LOCK_NAME = "LOCK.json"


class TemplateNotLocked(RuntimeError):
    pass


def load_template(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_templates(directory: Path = TEMPLATE_DIR) -> dict[str, dict]:
    return {t["id"]: t for t in (load_template(p) for p in sorted(directory.glob("*.yaml")))}


def template_hash(template: dict) -> str:
    canonical = json.dumps(template, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def lock_templates(directory: Path = TEMPLATE_DIR) -> dict:
    templates = load_templates(directory)
    lock = {
        "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "templates": {tid: {"version": t["version"], "sha256": template_hash(t)} for tid, t in templates.items()},
    }
    (directory / LOCK_NAME).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


def read_lock(directory: Path = TEMPLATE_DIR) -> dict | None:
    path = directory / LOCK_NAME
    return json.loads(path.read_text()) if path.exists() else None


def verify_lock(template: dict, directory: Path = TEMPLATE_DIR) -> None:
    """Raise TemplateNotLocked unless this exact template content was approved."""
    lock = read_lock(directory)
    if lock is None:
        raise TemplateNotLocked("templates have not been approved yet (run `labreach templates approve`)")
    entry = lock["templates"].get(template["id"])
    if entry is None or entry["sha256"] != template_hash(template):
        raise TemplateNotLocked(f"template '{template['id']}' differs from the approved version; re-approval required")


def lock_status(directory: Path = TEMPLATE_DIR) -> dict[str, str]:
    """template id -> 'locked' | 'modified' | 'unlocked'."""
    lock = read_lock(directory)
    out = {}
    for tid, t in load_templates(directory).items():
        if lock is None or tid not in lock["templates"]:
            out[tid] = "unlocked"
        else:
            out[tid] = "locked" if lock["templates"][tid]["sha256"] == template_hash(t) else "modified"
    return out
