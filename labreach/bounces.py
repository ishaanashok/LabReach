"""Bounce handling: mark the email and target, never retry the address, and watch the bounce rate."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import killswitch
from .db import log_event, utc_now_str
from .mail_imap import Bounce


def process_bounces(conn: sqlite3.Connection, bounce: Bounce, stop_file: Path, settings: dict) -> bool:
    row = conn.execute("SELECT e.id, e.target_id, e.bounced_at, t.email FROM emails e JOIN targets t ON t.id = e.target_id "
                       "WHERE e.message_id = ?", (bounce.matched_sent_id,)).fetchone()
    if row is None or row["bounced_at"]:
        return False
    conn.execute("UPDATE emails SET bounced_at = ? WHERE id = ?", (utc_now_str(), row["id"]))
    conn.execute("UPDATE targets SET status = 'bounced' WHERE id = ?", (row["target_id"],))
    if row["email"]:
        conn.execute("INSERT INTO do_not_contact (email, reason) VALUES (?, 'bounced')", (row["email"].lower(),))
    conn.commit()
    log_event(conn, "bounce", row["target_id"], summary=bounce.summary[:200])
    limits = settings["limits"]
    killswitch.check_bounce_rate(conn, stop_file, threshold=limits["bounce_rate_threshold"], window=limits["bounce_window"])
    return True
