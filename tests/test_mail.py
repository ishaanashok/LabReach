import smtplib
from datetime import UTC, datetime

import pytest

from labreach.compose import mime
from labreach.mail_imap import DRAFTS, ImapLoginFailure, ImapMailbox, strip_quoted
from labreach.mail_smtp import (
    AccountProblem,
    LiveModeRequired,
    LoginFailure,
    RecipientRejected,
    SmtpSender,
    TransientSendError,
)

from .fakes import FakeIMAP, FakeSMTP, raw_message, smtp_auth_error

NOW = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)
ME = "ishaan.ashok123@gmail.com"


def build(**kw):
    args = dict(from_name="Ishaan Ashok", from_addr=ME, to_addr="rivera@stanford.edu", subject="Question about X",
                body="Dear Professor Rivera,\n\nBody.", signature="Ishaan Ashok", now=NOW)
    args.update(kw)
    return mime.build_message(**args)


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "r.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def test_initial_message_shape(pdf):
    built = build(attachment=pdf, attachment_name="Ishaan_Ashok_Resume.pdf")
    m = built.message
    assert m["To"] == "rivera@stanford.edu" and m["Cc"] is None and m["Bcc"] is None
    assert m["Message-ID"] == built.message_id and built.message_id.endswith("@gmail.com>")
    names = [p.get_filename() for p in m.iter_attachments()]
    assert names == ["Ishaan_Ashok_Resume.pdf"]
    assert m.get_body(preferencelist=("plain",)).get_content_type() == "text/plain"
    assert m.get_body(preferencelist=("plain",)).get_content_charset() == "utf-8"


def test_rejects_multiple_recipients_and_header_injection():
    for bad in ("a@x.edu, b@x.edu", "a@x.edu\nBcc: b@x.edu", "a@x.edu;b@x.edu", "Name <a@x.edu>"):
        with pytest.raises(ValueError):
            build(to_addr=bad)
    with pytest.raises(ValueError):
        build(subject="Hi\nBcc: evil@x.com")


def test_reply_drafts_thread_and_have_no_attachment(pdf):
    first = build()
    reply = build(subject=mime.followup_subject("Question about X"), in_reply_to=first.message_id,
                  references=[first.message_id])
    assert reply.message["In-Reply-To"] == first.message_id and reply.message["References"] == first.message_id
    assert reply.message["Subject"] == "Re: Question about X"
    with pytest.raises(ValueError):
        build(in_reply_to=first.message_id, attachment=pdf)
    assert mime.followup_subject("Re: Question about X") == "Re: Question about X"


# ---- SMTP ---------------------------------------------------------------------------------------

def test_smtp_refuses_unless_live(monkeypatch):
    monkeypatch.delenv("LABREACH_MODE", raising=False)
    fake = FakeSMTP()
    with pytest.raises(LiveModeRequired):
        SmtpSender(ME, "pw", factory=fake).send(build().message)
    assert fake.sent == [] and fake.logins == []          # never even connected


def test_smtp_sends_when_live(monkeypatch):
    monkeypatch.setenv("LABREACH_MODE", "live")
    fake = FakeSMTP()
    SmtpSender(ME, "pw", factory=fake).send(build().message)
    assert len(fake.sent) == 1 and fake.logins == [ME] and (fake.host, fake.port) == ("smtp.gmail.com", 465)


@pytest.mark.parametrize("error,expected", [
    (smtp_auth_error(), LoginFailure),
    (smtplib.SMTPResponseException(550, b"5.4.5 Daily user sending limit exceeded"), AccountProblem),
    (smtplib.SMTPResponseException(550, b"5.1.1 The email account does not exist"), RecipientRejected),
    (smtplib.SMTPRecipientsRefused({"a@x.edu": (550, b"no such user")}), RecipientRejected),
    (smtplib.SMTPServerDisconnected("dropped"), TransientSendError),
    (OSError("network down"), TransientSendError),
])
def test_smtp_error_classification(monkeypatch, error, expected):
    monkeypatch.setenv("LABREACH_MODE", "live")
    with pytest.raises(expected):
        SmtpSender(ME, "pw", factory=FakeSMTP(send_error=error)).send(build().message)


def test_smtp_login_failure(monkeypatch):
    monkeypatch.setenv("LABREACH_MODE", "live")
    with pytest.raises(LoginFailure):
        SmtpSender(ME, "bad", factory=FakeSMTP(login_error=smtp_auth_error())).send(build().message)


# ---- IMAP ---------------------------------------------------------------------------------------

SENT_ID = "<sent1@gmail.com>"


def inbox():
    return [
        raw_message(frm="Alex Rivera <rivera@stanford.edu>", to=ME, subject="Re: Question", message_id="<r1@stanford.edu>",
                    in_reply_to=SENT_ID, references=[SENT_ID],
                    body="Sure, call me Thursday.\n\nOn Tue, Oct 13, 2026 Ishaan wrote:\n> Dear Professor\n> long quoted text"),
        raw_message(frm="newsletter@shop.com", to=ME, subject="50% off", message_id="<n1@shop.com>", body="SECRET PROMO"),
        raw_message(frm="friend@gmail.com", to=ME, subject="lunch?", message_id="<f1@gmail.com>", body="PRIVATE CHAT"),
        raw_message(frm=ME, to="rivera@stanford.edu", subject="Re: Question", message_id="<fu1@gmail.com>",
                    in_reply_to=SENT_ID, references=[SENT_ID]),                       # our own follow-up
        raw_message(frm="Mail Delivery Subsystem <mailer-daemon@googlemail.com>", to=ME, subject="Delivery Status Notification",
                    message_id="<b1@googlemail.com>", body="550 5.1.1 no such user\nOriginal Message-ID: <bounced9@gmail.com>"),
        raw_message(frm="mailer-daemon@googlemail.com", to=ME, subject="Delivery failure", message_id="<b2@googlemail.com>",
                    body="unrelated bounce for somebody else"),
    ]


def test_finds_only_replies_to_our_message_ids_and_never_touches_other_mail():
    fake = FakeIMAP({"INBOX": inbox()})
    with ImapMailbox(ME, "pw", factory=fake) as box:
        replies = box.find_replies([SENT_ID])
    assert [r.message_id for r in replies] == ["<r1@stanford.edu>"]
    assert replies[0].excerpt == "Sure, call me Thursday." and replies[0].from_addr == "rivera@stanford.edu"
    fetched = {n for _, n in fake.fetched}
    assert fetched == {1, 4}                      # reply + our own follow-up were fetched (then ignored); no others
    assert all(readonly for _, readonly in fake.selects)


def test_bounces_only_from_daemons_and_only_for_our_ids():
    fake = FakeIMAP({"INBOX": inbox()})
    with ImapMailbox(ME, "pw", factory=fake) as box:
        bounces = box.find_bounces(["<bounced9@gmail.com>", "<sent1@gmail.com>"])
    assert [b.matched_sent_id for b in bounces] == ["<bounced9@gmail.com>"]
    assert {n for _, n in fake.fetched} <= {5, 6}  # human mail (2, 3) never fetched


def test_excerpt_is_truncated_and_quotes_stripped():
    long = "word " * 1000
    fake = FakeIMAP({"INBOX": [raw_message(frm="a@x.edu", to=ME, subject="Re", message_id="<x@x.edu>", in_reply_to=SENT_ID,
                                           references=[SENT_ID], body=long)]})
    with ImapMailbox(ME, "pw", factory=fake) as box:
        assert len(box.find_replies([SENT_ID])[0].excerpt) <= 1500
    assert strip_quoted("Thanks!\n> old\nOn Mon, Jan 1, 2026, X wrote:\nmore") == "Thanks!"


def test_save_draft_appends_to_gmail_drafts_and_nothing_else():
    fake = FakeIMAP({"INBOX": []})
    with ImapMailbox(ME, "pw", factory=fake) as box:
        box.save_draft(build().message)
    assert fake.appended[0][0] == DRAFTS and fake.appended[0][1] == "\\Draft"


def test_imap_login_failure():
    with pytest.raises(ImapLoginFailure):
        with ImapMailbox(ME, "bad", factory=FakeIMAP(bad_login=True)):
            pass


def test_find_in_sent_by_message_id_and_fallback():
    sent = raw_message(frm=ME, to="rivera@stanford.edu", subject="Question about X", message_id="<actual@gmail.com>")
    fake = FakeIMAP({"INBOX": [], "[Gmail]/Sent Mail": [sent]})
    with ImapMailbox(ME, "pw", factory=fake) as box:
        assert box.find_in_sent(message_id="<actual@gmail.com>").found
        rewritten = box.find_in_sent(message_id="<ours@gmail.com>", subject="Question about X", to="rivera@stanford.edu")
        assert rewritten.found and rewritten.message_id == "<actual@gmail.com>"   # Gmail rewrote our id: we adopt its id
        assert not box.find_in_sent(message_id="<nope@gmail.com>").found
