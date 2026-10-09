"""Kill switch: engaged by a STOP file, `labreach stop`, or any automatic stop condition."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import get_state, log_event, set_state

KILL_KEY = "kill_switch"


def is_engaged(conn: sqlite3.Connection, stop_file: Path) -> bool:
    return stop_file.exists() or get_state(conn, KILL_KEY) is not None


def reason(conn: sqlite3.Connection, stop_file: Path) -> str | None:
    state = get_state(conn, KILL_KEY)
    if state:
        return json.loads(state)["reason"]
    if stop_file.exists():
        return "manual STOP file"
    return None


def engage(conn: sqlite3.Connection, stop_file: Path, why: str, *, write_stop_file: bool = True) -> None:
    """Stop all sending. Idempotent; the first reason wins."""
    if get_state(conn, KILL_KEY) is None:
        set_state(conn, KILL_KEY, json.dumps({"reason": why}))
        log_event(conn, "kill_switch_engaged", reason=why)
    if write_stop_file and not stop_file.exists():
        stop_file.write_text(f"{why}\n")


def resume(conn: sqlite3.Connection, stop_file: Path) -> None:
    set_state(conn, KILL_KEY, None)
    conn.execute("DELETE FROM settings_state WHERE key = ?", (KILL_KEY,))
    conn.commit()
    stop_file.unlink(missing_ok=True)
    log_event(conn, "kill_switch_cleared")


# ---- automatic stop conditions -------------------------------------------------------------

def check_bounce_rate(conn: sqlite3.Connection, stop_file: Path, *, threshold: float = 0.15,
                      window: int = 20) -> bool:
    """Engage if more than `threshold` of the last `window` sends bounced. Needs a full window."""
    rows = conn.execute(
        "SELECT bounced_at FROM emails WHERE sent_at IS NOT NULL ORDER BY sent_at DESC, id DESC LIMIT ?",
        (window,),
    ).fetchall()
    if len(rows) < window:
        return False
    rate = sum(1 for r in rows if r["bounced_at"]) / len(rows)
    if rate > threshold:
        engage(conn, stop_file, f"bounce rate {rate:.0%} over last {window} sends exceeds {threshold:.0%}")
        return True
    return False


def record_claim_check(conn: sqlite3.Connection, target_id: int | None, source_type: str, passed: bool) -> None:
    log_event(conn, "claim_check", target_id, source_type=source_type, passed=passed)


def check_claim_failure_streak(conn: sqlite3.Connection, stop_file: Path, *, streak: int = 3) -> bool:
    """Engage on `streak` consecutive claim-verification failures from the same source type."""
    rows = conn.execute(
        "SELECT detail_json FROM events WHERE type = 'claim_check' ORDER BY id DESC LIMIT ?", (streak,)
    ).fetchall()
    if len(rows) < streak:
        return False
    details = [json.loads(r["detail_json"]) for r in rows]
    types = {d["source_type"] for d in details}
    if len(types) == 1 and not any(d["passed"] for d in details):
        engage(conn, stop_file, f"{streak} consecutive claim-verification failures from source type '{types.pop()}'")
        return True
    return False


ACCOUNT_MARKERS = ("sending limit", "suspended", "unusual", "blocked", "too many", "spam", "authentication",
                   "username and password", "application-specific password", "5.4.5", "5.7.")
ACCOUNT_CODES = (421, 450, 452, 454, 530, 534, 535)


def is_account_problem(code: int | None, text: str) -> bool:
    """True for SMTP errors that signal sending restrictions or account trouble (not a bad recipient)."""
    return any(m in text.lower() for m in ACCOUNT_MARKERS) or code in ACCOUNT_CODES


def on_smtp_error(conn: sqlite3.Connection, stop_file: Path, code: int | None, text: str) -> bool:
    """Any 4xx/5xx that signals sending limits or account problems stops everything."""
    if is_account_problem(code, text):
        engage(conn, stop_file, f"SMTP error {code}: {text[:200]}")
        return True
    return False


def on_login_failure(conn: sqlite3.Connection, stop_file: Path, what: str) -> None:
    engage(conn, stop_file, f"login failure: {what}")


def on_unsafe_reply(conn: sqlite3.Connection, stop_file: Path, target_id: int | None, label: str) -> None:
    """Complaint / stop request / unclassifiable reply: engage and let the caller add to do_not_contact."""
    log_event(conn, "unsafe_reply", target_id, label=label)
    engage(conn, stop_file, f"reply requires human review ({label})")
