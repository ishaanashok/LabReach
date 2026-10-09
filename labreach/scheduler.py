"""Time rules: US holidays, business days, recipient-local send windows, blackouts, daily caps, jitter."""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import HARD_DAILY_CAP
from .db import get_state

LIVE_START_KEY = "live_started_on"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---- holidays ----------------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def thanksgiving(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4)


def us_holidays(year: int) -> set[date]:
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    floating = [_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _last_weekday(year, 5, 0),
                _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), thanksgiving(year)]
    return {_observed(d) for d in fixed} | set(floating) | {thanksgiving(year) + timedelta(days=1)}


def is_holiday(d: date) -> bool:
    # a Jan 1 observed on the prior Dec 31 belongs to next year's set
    return d in us_holidays(d.year) or d in us_holidays(d.year + 1)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def add_business_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def business_days_between(start: date, end: date) -> int:
    """Business days elapsed after `start` up to and including `end`."""
    count, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_business_day(d):
            count += 1
    return count


# ---- blackouts and windows --------------------------------------------------------------------

def _in_range(d: date, start: str, end: str) -> bool:
    sm, sd = map(int, start.split("-"))
    em, ed = map(int, end.split("-"))
    key, lo, hi = (d.month, d.day), (sm, sd), (em, ed)
    return lo <= key <= hi if lo <= hi else key >= lo or key <= hi    # wraps over New Year


def in_blackout(d: date, settings: dict, university: str | None = None) -> str | None:
    """Reason string if `d` is blacked out, else None."""
    window = settings["send_window"]
    for start, end in window.get("blackout_ranges", []):
        if _in_range(d, start, end):
            return f"blackout {start}..{end}"
    if window.get("thanksgiving_week"):
        t = thanksgiving(d.year)
        monday = t - timedelta(days=t.weekday())
        if monday <= d <= monday + timedelta(days=6):
            return "Thanksgiving week"
    for start, end in window.get("university_blackouts", {}).get(university or "", []):
        if _in_range(d, start, end):
            return f"{university} blackout {start}..{end}"
    return None


def day_allows_sending(d: date, settings: dict, university: str | None = None) -> bool:
    return (WEEKDAYS[d.weekday()] in settings["send_window"]["weekdays"] and not is_holiday(d)
            and in_blackout(d, settings, university) is None)


def in_send_window(now_utc: datetime, tz: str, settings: dict, university: str | None = None) -> bool:
    local = now_utc.astimezone(ZoneInfo(tz))
    w = settings["send_window"]
    return day_allows_sending(local.date(), settings, university) and w["start_hour"] <= local.hour < w["end_hour"]


def next_window_start(after_utc: datetime, tz: str, settings: dict, university: str | None = None) -> datetime:
    """Start (UTC) of the next allowed window at or after `after_utc`; if inside one now, returns `after_utc`."""
    zone, w = ZoneInfo(tz), settings["send_window"]
    local = after_utc.astimezone(zone)
    day = local.date()
    for _ in range(400):
        if day_allows_sending(day, settings, university):
            start = datetime(day.year, day.month, day.day, w["start_hour"], tzinfo=zone)
            end = datetime(day.year, day.month, day.day, w["end_hour"], tzinfo=zone)
            if local < end:
                return max(start, local).astimezone(UTC)
        day += timedelta(days=1)
        local = datetime(day.year, day.month, day.day, tzinfo=zone)
    raise RuntimeError("no send window found within 400 days")


def jittered_send_after(after_utc: datetime, tz: str, settings: dict, university: str | None = None,
                        rng: random.Random | None = None) -> datetime:
    """A send time inside a valid window, 5-40 minutes after the window opens (or after `after_utc`)."""
    rng = rng or random.Random()
    lo, hi = settings["caps"]["send_jitter_minutes"]
    base = next_window_start(after_utc, tz, settings, university)
    candidate = base + timedelta(minutes=rng.uniform(lo, hi))
    local = candidate.astimezone(ZoneInfo(tz))
    if local.hour >= settings["send_window"]["end_hour"]:   # we were late in the window: retry next window
        return jittered_send_after(base + timedelta(hours=8), tz, settings, university, rng)
    return candidate


# ---- follow-up reminders (the parent sends every follow-up personally) ------------------------------

def followup_reminder_due(initial_sent_utc: datetime, now_utc: datetime, tz: str, settings: dict) -> bool:
    """True once enough business days have passed since the initial email that a manual follow-up makes sense."""
    zone = ZoneInfo(tz)
    days = business_days_between(initial_sent_utc.astimezone(zone).date(), now_utc.astimezone(zone).date())
    return days >= settings["followups"]["remind_after_business_days"]


# ---- daily caps --------------------------------------------------------------------------------

def daily_initial_limit(conn: sqlite3.Connection, settings: dict, today: date) -> int:
    caps = settings["caps"]
    started = get_state(conn, LIVE_START_KEY)
    if started and (today - date.fromisoformat(started)).days >= 14:
        return min(HARD_DAILY_CAP, caps["daily_initial_after"])
    return min(HARD_DAILY_CAP, caps["daily_initial_first_two_weeks"])


def _sent_today(conn: sqlite3.Connection, local_day: date, tz: str, where: str = "", args: tuple = ()) -> int:
    zone = ZoneInfo(tz)
    start = datetime(local_day.year, local_day.month, local_day.day, tzinfo=zone).astimezone(UTC)
    end = start + timedelta(days=1)
    row = conn.execute(
        "SELECT COUNT(*) FROM emails e JOIN targets t ON t.id = e.target_id "
        "WHERE e.kind = 'initial' AND e.sent_at >= ? AND e.sent_at < ? " + where,
        (start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"), *args)).fetchone()
    return row[0]


def cap_problems(conn: sqlite3.Connection, settings: dict, now_utc: datetime, university_norm: str,
                 department: str | None) -> list[str]:
    tz = settings["timezone"]
    today = now_utc.astimezone(ZoneInfo(tz)).date()
    problems = []
    if _sent_today(conn, today, tz) >= daily_initial_limit(conn, settings, today):
        problems.append("daily initial-email cap reached")
    if _sent_today(conn, today, tz, "AND t.university_norm = ?", (university_norm,)) >= settings["caps"]["per_university_per_day"]:
        problems.append("per-university daily cap reached")
    if department and _sent_today(conn, today, tz, "AND t.university_norm = ? AND t.department = ?",
                                  (university_norm, department)) >= settings["caps"]["per_department_per_day"]:
        problems.append("per-department daily cap reached")
    return problems
