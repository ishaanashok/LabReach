import sqlite3

import pytest

from labreach.db import log_event, migrate


def test_migrations_idempotent(conn):
    assert migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"targets", "sources", "emails", "events", "programs", "do_not_contact", "claude_calls",
            "settings_state"} <= tables


def test_events_append_only(conn):
    log_event(conn, "x", None, a=1)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE events SET type='y'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM events")


def _target(conn, email, name="A B"):
    conn.execute("INSERT INTO targets (name, name_norm, university, university_norm, email) VALUES (?,?,?,?,?)",
                 (name, name.lower(), "Stanford", "stanford", email))


def test_unique_email_and_one_email_per_kind(conn):
    _target(conn, "a@stanford.edu")
    with pytest.raises(sqlite3.IntegrityError):
        _target(conn, "a@stanford.edu", "C D")
    _target(conn, None, "E F")
    _target(conn, None, "G H")  # several needs_manual_email rows are fine
    conn.execute("INSERT INTO emails (target_id, kind) VALUES (1, 'initial')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO emails (target_id, kind) VALUES (1, 'initial')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO emails (target_id, kind) VALUES (1, 'fu3')")
