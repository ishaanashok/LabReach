from datetime import UTC, datetime

import pytest

from labreach import killswitch as ks
from labreach.gates import run_gates
from labreach.personalize.generate import draft_initial

from .helpers import make_sent
from .world import asker_for, make_ready_target

NOW = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)          # Tuesday 9:30 am Pacific


@pytest.fixture
def ready(conn, settings, profile, templates, stop_file, today):
    tid, source, slots = make_ready_target(conn, 0)
    row = conn.execute("SELECT * FROM targets WHERE id = ?", (tid,)).fetchone()
    outcome = draft_initial(conn, row, templates["initial_a"], profile, settings, asker_for({source.id: slots}), today,
                            stop_file, enforce_lock=False)
    assert outcome.ok, outcome.reasons
    return tid


def gates(conn, settings, profile, templates, stop_file, when=NOW, kind="initial", enforce_lock=False, tid=1):
    email = conn.execute("SELECT * FROM emails WHERE target_id = ? AND kind = ?", (tid, kind)).fetchone()
    return run_gates(conn, email, settings, profile, templates, when, stop_file, enforce_lock)


def failing(g):
    return {n for n, (ok, _) in g.results.items() if not ok}


def test_clean_email_passes_every_gate(conn, settings, profile, templates, stop_file, ready):
    g = gates(conn, settings, profile, templates, stop_file)
    assert failing(g) == set(), g.failures()
    assert set(g.results) == {"template", "lint", "claims", "provenance", "eligibility", "limits", "window", "killswitch"}


def test_gate1_template_tampering_and_lock(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE emails SET body = replace(body, 'Thank you for your time', 'Thanks a lot')")
    assert "template" in failing(gates(conn, settings, profile, templates, stop_file))


def test_gate1_requires_the_approved_lock(conn, settings, profile, templates, stop_file, ready):
    g = gates(conn, settings, profile, templates, stop_file, enforce_lock=True)
    assert "template" in failing(g) and "approved" in g.results["template"][1]


def test_gate2_lint_catches_banned_wording_and_addresses_in_the_text(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE emails SET body = replace(body, 'I read your', 'Your groundbreaking work; email me at evil@x.edu. I read your')")
    f = failing(gates(conn, settings, profile, templates, stop_file))
    assert {"lint", "claims"} <= f        # p1/p2 are free text, so the skeleton still conforms; lint and claims catch it


def test_gate3_claims_are_rechecked_against_stored_sources(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE sources SET snippet = 'An unrelated passage about something else entirely and nothing more.', title = 'Other'")
    assert "claims" in failing(gates(conn, settings, profile, templates, stop_file))


def test_gate4_address_provenance(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE targets SET email_source_snippet = 'Contact: someone-else@stanford.edu'")
    assert "provenance" in failing(gates(conn, settings, profile, templates, stop_file))
    conn.execute("UPDATE targets SET email_source_snippet = 'Contact: abernathy@stanford.edu', email_source_url = 'https://evil.example.com/p'")
    assert "provenance" in failing(gates(conn, settings, profile, templates, stop_file))
    conn.execute("UPDATE targets SET email = 'abernathy@gmail.com', email_source_url = 'https://me.stanford.edu/p', "
                 "email_source_snippet = 'Contact: abernathy@gmail.com'")
    assert "provenance" in failing(gates(conn, settings, profile, templates, stop_file))   # domain not allowlisted
    conn.execute("UPDATE targets SET email = NULL")
    assert "provenance" in failing(gates(conn, settings, profile, templates, stop_file))


def test_gate5_do_not_contact_and_dedupe(conn, settings, profile, templates, stop_file, ready):
    conn.execute("INSERT INTO do_not_contact (email, reason) VALUES ('abernathy@stanford.edu', 'asked')")
    assert "eligibility" in failing(gates(conn, settings, profile, templates, stop_file))
    conn.execute("DELETE FROM do_not_contact")
    conn.execute("INSERT INTO do_not_contact (domain, reason) VALUES ('stanford.edu', 'university asked')")
    assert "eligibility" in failing(gates(conn, settings, profile, templates, stop_file))
    conn.execute("DELETE FROM do_not_contact")
    # same person already contacted under another address
    conn.execute("INSERT INTO targets (id, name, name_norm, university, university_norm, email, status) VALUES "
                 "(99, 'Pat Abernathy', 'patabernathy', 'Stanford University', 'stanford university', 'pat@cs.stanford.edu', 'active')")
    make_sent(conn, 99, message_id="<old@gmail.com>")
    g = gates(conn, settings, profile, templates, stop_file)
    assert "eligibility" in failing(g) and "already contacted" in g.results["eligibility"][1]


def test_gate5_one_active_contact_per_lab(conn, settings, profile, templates, stop_file, ready):
    conn.execute("INSERT INTO targets (id, name, name_norm, university, university_norm, email, status, lab_key) VALUES "
                 "(50, 'Grad Student', 'gradstudent', 'Stanford University', 'stanford university', 'gs@stanford.edu', 'active', 'lab-0')")
    make_sent(conn, 50, message_id="<gs@gmail.com>")
    assert "eligibility" in failing(gates(conn, settings, profile, templates, stop_file))
    conn.execute("UPDATE targets SET status = 'closed' WHERE id = 50")
    assert "eligibility" not in failing(gates(conn, settings, profile, templates, stop_file))


@pytest.mark.parametrize("status", ["bounced", "dnc", "closed", "replied", "needs_human"])
def test_gate5_status_must_allow_sending(conn, settings, profile, templates, stop_file, ready, status):
    conn.execute("UPDATE targets SET status = ?", (status,))
    assert "eligibility" in failing(gates(conn, settings, profile, templates, stop_file))


@pytest.mark.parametrize("when,ok", [
    (datetime(2026, 10, 20, 15, 30, tzinfo=UTC), True),     # Tue 8:30 PT
    (datetime(2026, 10, 20, 17, 15, tzinfo=UTC), False),    # Tue 10:15 PT
    (datetime(2026, 10, 19, 16, 30, tzinfo=UTC), False),    # Monday
    (datetime(2026, 10, 23, 16, 30, tzinfo=UTC), False),    # Friday
    (datetime(2026, 12, 15, 16, 30, tzinfo=UTC), False),    # December blackout
    (datetime(2026, 11, 24, 16, 30, tzinfo=UTC), False),    # Thanksgiving week
])
def test_gate7_window(conn, settings, profile, templates, stop_file, ready, when, ok):
    assert ("window" not in failing(gates(conn, settings, profile, templates, stop_file, when))) is ok


def test_gate7_uses_recipient_local_time(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE targets SET timezone = 'America/New_York'")
    assert "window" not in failing(gates(conn, settings, profile, templates, stop_file, datetime(2026, 10, 20, 12, 30, tzinfo=UTC)))
    assert "window" in failing(gates(conn, settings, profile, templates, stop_file, NOW))      # 12:30 pm in New York


def test_gate6_caps(conn, settings, profile, templates, stop_file, ready):
    for n, (uni, dept) in enumerate([("Stanford University", "Mechanical Engineering")]):
        conn.execute("INSERT INTO targets (id, name, name_norm, university, university_norm, department, email, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (70 + n, f"X{n}", f"x{n}", uni, uni.lower(), dept, f"x{n}@stanford.edu", "active"))
        make_sent(conn, 70 + n, message_id=f"<x{n}@gmail.com>", sent_at="2026-10-20T15:10:00")
    g = gates(conn, settings, profile, templates, stop_file)
    assert "limits" in failing(g) and "department" in g.results["limits"][1]


def test_gate8_kill_switch(conn, settings, profile, templates, stop_file, ready):
    ks.engage(conn, stop_file, "test")
    assert "killswitch" in failing(gates(conn, settings, profile, templates, stop_file))


# ---- one initial email per person, ever ---------------------------------------------------------------

def test_an_already_sent_email_cannot_be_sent_again(conn, settings, profile, templates, stop_file, ready):
    conn.execute("UPDATE emails SET state='sent', sent_at='2026-10-20T16:00:00', message_id='<i1@gmail.com>'")
    conn.execute("UPDATE targets SET status = 'active'")
    g = gates(conn, settings, profile, templates, stop_file)
    assert "eligibility" in failing(g) and "already sent" in g.results["eligibility"][1]
