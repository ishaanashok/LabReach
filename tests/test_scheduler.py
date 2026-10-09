import random
from datetime import UTC, date, datetime, timedelta

from labreach import scheduler as sch
from labreach.db import set_state, utc_now_str


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_holidays_2026(settings):
    assert sch.thanksgiving(2026) == date(2026, 11, 26)
    for d in (date(2026, 10, 12), date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 25), date(2026, 7, 3)):
        assert sch.is_holiday(d), d
    assert sch.is_holiday(date(2027, 1, 1))
    assert not sch.is_holiday(date(2026, 10, 13))


def test_send_window_tue_thu_8_to_10_recipient_local(settings):
    pt, et = "America/Los_Angeles", "America/New_York"
    assert sch.in_send_window(utc(2026, 10, 13, 15, 30), pt, settings)          # Tue 8:30 PT
    assert not sch.in_send_window(utc(2026, 10, 13, 17, 0), pt, settings)       # 10:00 PT is closed
    assert not sch.in_send_window(utc(2026, 10, 13, 14, 59), pt, settings)      # 7:59 PT
    assert not sch.in_send_window(utc(2026, 10, 12, 15, 30), pt, settings)      # Monday (and holiday)
    assert not sch.in_send_window(utc(2026, 10, 16, 15, 30), pt, settings)      # Friday
    assert sch.in_send_window(utc(2026, 10, 13, 12, 30), et, settings)          # 8:30 ET (5:30 PT would fail)
    assert not sch.in_send_window(utc(2026, 10, 13, 12, 30), pt, settings)


def test_blackouts(settings):
    assert sch.in_blackout(date(2026, 12, 15), settings)                 # Dec 12 - Jan 5
    assert sch.in_blackout(date(2027, 1, 5), settings)
    assert not sch.in_blackout(date(2027, 1, 6), settings)
    assert sch.in_blackout(date(2026, 11, 24), settings) == "Thanksgiving week"
    assert not sch.day_allows_sending(date(2026, 11, 24), settings)
    custom = {**settings, "send_window": {**settings["send_window"],
                                          "university_blackouts": {"Stanford University": [["03-23", "03-27"]]}}}
    assert not sch.day_allows_sending(date(2027, 3, 24), custom, "Stanford University")
    assert sch.day_allows_sending(date(2027, 3, 24), custom, "Purdue University")


def test_next_window_and_jitter(settings):
    tz = "America/Los_Angeles"
    start = sch.next_window_start(utc(2026, 10, 9, 20, 0), tz, settings)         # Friday afternoon
    assert start == utc(2026, 10, 13, 15, 0)                                     # Tue 8:00 PT
    inside = utc(2026, 10, 13, 15, 20)
    assert sch.next_window_start(inside, tz, settings) == inside
    after_close = sch.next_window_start(utc(2026, 10, 13, 18, 0), tz, settings)
    assert after_close == utc(2026, 10, 14, 15, 0)
    rng = random.Random(1)
    for _ in range(50):
        send = sch.jittered_send_after(utc(2026, 10, 9, 20, 0), tz, settings, rng=rng)
        assert 5 <= (send - utc(2026, 10, 13, 15, 0)).total_seconds() / 60 <= 40
        assert sch.in_send_window(send, tz, settings)


def test_business_day_math_and_followups(settings):
    assert sch.add_business_days(date(2026, 10, 9), 1) == date(2026, 10, 13)    # skips weekend + Columbus Day
    assert sch.business_days_between(date(2026, 10, 13), date(2026, 10, 22)) == 7
    tz = "America/Los_Angeles"
    sent = utc(2026, 10, 13, 15, 30)
    assert not sch.followup_due("fu1", sent, utc(2026, 10, 21, 15, 30), tz, settings)
    assert sch.followup_due("fu1", sent, utc(2026, 10, 22, 15, 30), tz, settings)
    assert not sch.followup_due("fu2", sent, utc(2026, 10, 26, 15, 30), tz, settings)   # needs 10
    assert sch.followup_due("fu2", sent, utc(2026, 10, 27, 15, 30), tz, settings)


def test_daily_limits_pilot_ramp_and_hard_cap(conn, settings):
    today = date(2026, 10, 20)
    assert sch.daily_initial_limit(conn, settings, today) == 5
    set_state(conn, sch.LIVE_START_KEY, "2026-10-10")
    assert sch.daily_initial_limit(conn, settings, today) == 5                  # day 10 of the ramp
    assert sch.daily_initial_limit(conn, settings, date(2026, 10, 24)) == 10
    greedy = {**settings, "caps": {**settings["caps"], "daily_initial_after": 50}}
    assert sch.daily_initial_limit(conn, greedy, date(2026, 11, 20)) == 10      # never above 10


def _send(conn, tid, uni, dept, when):
    conn.execute("INSERT INTO targets (id, name, name_norm, university, university_norm, department) VALUES (?,?,?,?,?,?)",
                 (tid, f"p{tid}", f"p{tid}", uni, uni.lower(), dept))
    conn.execute("INSERT INTO emails (target_id, kind, sent_at) VALUES (?, 'initial', ?)", (tid, utc_now_str(when)))


def test_university_department_and_daily_caps(conn, settings):
    now = utc(2026, 10, 13, 16, 0)
    assert sch.cap_problems(conn, settings, now, "stanford university", "Mechanical Engineering") == []
    _send(conn, 1, "Stanford University", "Mechanical Engineering", now - timedelta(minutes=30))
    assert "per-department daily cap reached" in sch.cap_problems(conn, settings, now, "stanford university",
                                                                  "Mechanical Engineering")
    assert sch.cap_problems(conn, settings, now, "stanford university", "Electrical Engineering") == []
    _send(conn, 2, "Stanford University", "Electrical Engineering", now - timedelta(minutes=20))
    _send(conn, 3, "Stanford University", "Computer Science", now - timedelta(minutes=10))
    assert "per-university daily cap reached" in sch.cap_problems(conn, settings, now, "stanford university", "Bioengineering")
    _send(conn, 4, "MIT", "A", now - timedelta(minutes=9))
    _send(conn, 5, "Caltech", "B", now - timedelta(minutes=8))
    assert "daily initial-email cap reached" in sch.cap_problems(conn, settings, now, "purdue university", "C")
    # yesterday's sends do not count today
    assert sch.cap_problems(conn, settings, now + timedelta(days=1), "purdue university", "C") == []
