"""Claude-call budget (subscription usage), tracked per local day in the claude_calls table."""

from __future__ import annotations

import sqlite3
from datetime import datetime


class BudgetExhausted(RuntimeError):
    pass


def _day(now: datetime | None) -> str:
    return (now or datetime.now().astimezone()).strftime("%Y-%m-%d")


def record_call(conn: sqlite3.Connection, purpose: str, success: bool, now: datetime | None = None) -> None:
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO claude_calls (timestamp, purpose, success) VALUES (?, ?, ?)",
        (stamp, purpose, int(success)),
    )
    conn.commit()


def calls_today(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM claude_calls WHERE substr(timestamp, 1, 10) = ?", (_day(now),)
    ).fetchone()
    return row[0]


def remaining(conn: sqlite3.Connection, max_per_day: int, now: datetime | None = None) -> int:
    return max(0, max_per_day - calls_today(conn, now))


def check_budget(conn: sqlite3.Connection, max_per_day: int, now: datetime | None = None) -> None:
    if remaining(conn, max_per_day, now) <= 0:
        raise BudgetExhausted(
            f"Claude call budget reached ({max_per_day}/day); discovery and drafting pause until tomorrow"
        )
