from datetime import UTC, datetime

import pytest

from labreach import killswitch as ks
from labreach import replies as R
from labreach.bounces import process_bounces
from labreach.mail_imap import Bounce, ImapMailbox
from labreach.notify import alert

from .fakes import FakeIMAP, raw_message
from .helpers import make_sent, make_target

ME = "ishaan.ashok123@gmail.com"
NOW = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)


@pytest.fixture
def world(conn, settings):
    make_target(conn)
    eid = make_sent(conn, 1)
    return eid


def inbound_box(body, frm="Alex Rivera <rivera@stanford.edu>"):
    return FakeIMAP({"INBOX": [raw_message(frm=frm, to=ME, subject="Re: Question about X", message_id="<reply1@stanford.edu>",
                                           in_reply_to="<sent1@gmail.com>", references=["<sent1@gmail.com>"], body=body)]})


def sync(conn, settings, stop_file, fake):
    with ImapMailbox(ME, "pw", factory=fake) as box:
        return R.sync_inbox(conn, box, settings, stop_file)


@pytest.mark.parametrize("text,expected", [
    ("Please do not contact me again.", "unsafe"),
    ("Remove me from your list.", "unsafe"),
    ("How did you get my email address?", "unsafe"),
    ("This looks like spam.", "unsafe"),
    ("I'm writing from the Office of Compliance regarding minors in university programs.", "unsafe"),
    ("Unfortunately we do not accept high school students in the lab.", "decline_hs"),
    ("Our lab cannot take minors due to policy.", "decline_hs"),
    ("I am out of the office until Nov 3 and will reply on return.", "out_of_office"),
    ("Sounds interesting, let's chat.", None),
])
def test_rule_classifier(text, expected):
    assert R.rule_classify(text, "rivera@stanford.edu") == expected


def test_sender_from_compliance_mailbox_is_unsafe():
    assert R.rule_classify("Hello", "minors-compliance@stanford.edu") == "unsafe"


def test_sync_records_reply_once_and_blocks_followups(conn, settings, stop_file, world):
    fake = inbound_box("Happy to talk. How about Thursday?")
    assert sync(conn, settings, stop_file, fake) == {"replies": 1, "bounces": 0}
    row = conn.execute("SELECT replied_at FROM emails WHERE id = ?", (world,)).fetchone()
    assert row["replied_at"] is not None
    assert conn.execute("SELECT label, excerpt FROM replies").fetchone()["label"] == "unclassified"
    assert sync(conn, settings, stop_file, fake) == {"replies": 0, "bounces": 0}      # its thread is no longer watched
    assert conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1


def test_positive_reply_creates_draft_and_never_sends(conn, settings, stop_file, world, fake_claude, monkeypatch):
    monkeypatch.setenv("LABREACH_MODE", "live")
    sync(conn, settings, stop_file, inbound_box("Happy to talk. How about Thursday?"))
    fake_claude.queue([fake_claude.ok({"label": "positive", "confidence": 0.95})])
    imap = FakeIMAP({"INBOX": []})
    with ImapMailbox(ME, "pw", factory=imap) as box:
        label = R.handle_reply(conn, 1, settings, stop_file, box, NOW)
    assert label == "positive" and not ks.is_engaged(conn, stop_file)
    mailbox, flags, raw = imap.appended[0]
    text = raw.decode()
    assert mailbox == "[Gmail]/Drafts" and flags == "\\Draft"
    assert "[LabReach draft" in text and "In-Reply-To: <reply1@stanford.edu>" in text and "Re: Question about X" in text
    assert text.count("\n- ") == 3 or text.count("\r\n- ") == 3
    assert conn.execute("SELECT status FROM targets").fetchone()[0] == "replied"
    assert "reply" not in {c["prompt"] for c in fake_claude.calls() if "Dear" in c["prompt"]}   # reply text only classified


def test_complaint_engages_kill_switch_and_adds_dnc(conn, settings, stop_file, world):
    sync(conn, settings, stop_file, inbound_box("Please do not contact me again. Who gave you my address?"))
    assert R.handle_reply(conn, 1, settings, stop_file, None, NOW) == "unsafe"
    assert ks.is_engaged(conn, stop_file) and "reply requires human review" in ks.reason(conn, stop_file)
    assert conn.execute("SELECT email FROM do_not_contact").fetchone()[0] == "rivera@stanford.edu"
    assert conn.execute("SELECT status FROM targets").fetchone()[0] == "dnc"


def test_low_confidence_claude_label_is_treated_as_unsafe(conn, settings, stop_file, world, fake_claude):
    sync(conn, settings, stop_file, inbound_box("Hmm, send me something."))
    fake_claude.queue([fake_claude.ok({"label": "positive", "confidence": 0.6})])
    assert R.handle_reply(conn, 1, settings, stop_file, None, NOW) == "unsafe" and ks.is_engaged(conn, stop_file)


def test_polite_hs_decline_closes_with_thank_you_and_does_not_halt(conn, settings, stop_file, world):
    sync(conn, settings, stop_file, inbound_box("Thanks for writing, but we do not accept high school students."))
    imap = FakeIMAP({"INBOX": []})
    with ImapMailbox(ME, "pw", factory=imap) as box:
        assert R.handle_reply(conn, 1, settings, stop_file, box, NOW) == "decline_hs"
    assert not ks.is_engaged(conn, stop_file)
    assert conn.execute("SELECT status FROM targets").fetchone()[0] == "closed"
    assert conn.execute("SELECT COUNT(*) FROM do_not_contact").fetchone()[0] == 1
    assert b"Thank you for taking the time to reply" in imap.appended[0][2]


def test_hs_decline_halts_when_configured(conn, settings, stop_file, world):
    strict = {**settings, "replies": {"halt_on_hs_decline": True}}
    sync(conn, strict, stop_file, inbound_box("We do not accept high school students."))
    R.handle_reply(conn, 1, strict, stop_file, None, NOW)
    assert ks.is_engaged(conn, stop_file)


def test_out_of_office_is_not_a_reply(conn, settings, stop_file, world):
    sync(conn, settings, stop_file, inbound_box("I am out of the office until November 3."))
    assert R.handle_reply(conn, 1, settings, stop_file, None, NOW) == "out_of_office"
    assert conn.execute("SELECT replied_at FROM emails").fetchone()[0] is None
    assert conn.execute("SELECT status FROM targets").fetchone()[0] == "active"


def test_claude_down_leaves_reply_pending_and_does_not_guess(conn, settings, stop_file, world, monkeypatch):
    from labreach.claude_cli import ClaudeError
    sync(conn, settings, stop_file, inbound_box("Let's talk next week."))
    monkeypatch.setenv("LABREACH_CLAUDE_BIN", "/nonexistent/claude")
    with pytest.raises(ClaudeError):
        R.handle_reply(conn, 1, settings, stop_file, None, NOW)
    assert R.pending_replies(conn) == [1]
    assert conn.execute("SELECT replied_at FROM emails").fetchone()[0] is not None      # still blocks follow-ups


def test_call_times_use_recipient_timezone(settings):
    assert R.call_times("America/Los_Angeles", settings, NOW) == [
        "Thursday, October 22 at 4:00 PM PDT", "Friday, October 23 at 4:00 PM PDT", "Monday, October 26 at 4:00 PM PDT"]
    eastern = R.call_times("America/New_York", settings, NOW)
    assert eastern[0] == "Thursday, October 22 at 7:00 PM EDT (4:00 PM Pacific)"


def test_bounce_marks_dnc_and_trips_rate_limit(conn, settings, stop_file):
    for i in range(1, 21):
        make_target(conn, i, name=f"P{i}", email=f"p{i}@stanford.edu", lab_key=f"l{i}")
        make_sent(conn, i, message_id=f"<m{i}@gmail.com>", sent_at=f"2026-10-13T15:{i:02d}:00")
    for i in range(1, 4):
        assert process_bounces(conn, Bounce(f"<m{i}@gmail.com>", "mailer-daemon", "no such user"), stop_file, settings)
    assert not ks.is_engaged(conn, stop_file)                                   # 15% is not above 15%
    assert not process_bounces(conn, Bounce("<m1@gmail.com>", "mailer-daemon", "dup"), stop_file, settings)
    process_bounces(conn, Bounce("<m4@gmail.com>", "mailer-daemon", "no such user"), stop_file, settings)
    assert ks.is_engaged(conn, stop_file) and "bounce rate" in ks.reason(conn, stop_file)
    assert conn.execute("SELECT status FROM targets WHERE id=1").fetchone()[0] == "bounced"
    assert conn.execute("SELECT COUNT(*) FROM do_not_contact").fetchone()[0] == 4


def test_alert_saves_one_draft_per_reason(conn, settings):
    imap = FakeIMAP({"INBOX": []})
    with ImapMailbox(ME, "pw", factory=imap) as box:
        alert(conn, settings, "bounce rate 20%", box)
        alert(conn, settings, "bounce rate 20%", box)
        alert(conn, settings, "login failure: smtp", box)
    assert len(imap.appended) == 2 and b"[LabReach ALERT]" in imap.appended[0][2]
    alert(conn, settings, "another", None)      # no mailbox (e.g. login is the problem): console only, no crash
