"""Gmail IMAP, read-only. It looks ONLY for (a) replies whose In-Reply-To/References match Message-IDs this
tool sent and (b) mailer-daemon/postmaster bounces that mention them. Nothing else is read or stored.
The only write is saving drafts for the parent to review (APPEND to [Gmail]/Drafts)."""

from __future__ import annotations

import email
import imaplib
import re
import time
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime

EXCERPT_CHARS = 1500
DRAFTS = "[Gmail]/Drafts"
SENT = "[Gmail]/Sent Mail"


class ImapLoginFailure(RuntimeError):
    pass


@dataclass
class Inbound:
    message_id: str
    in_reply_to: str
    references: list[str]
    from_addr: str
    subject: str
    received_at: str
    excerpt: str                      # reply text only, quotes removed, truncated
    matched_sent_id: str


@dataclass
class Bounce:
    matched_sent_id: str
    from_addr: str
    summary: str


@dataclass
class SentLookup:
    found: bool
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)


def strip_quoted(text: str) -> str:
    """Keep only what the person wrote: drop '> ' lines and everything after 'On ... wrote:'."""
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*On .{5,200}wrote:\s*$", line) or line.startswith("-----Original Message"):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    try:
        return part.get_content() if part else ""
    except (LookupError, UnicodeDecodeError):
        return ""


class ImapMailbox:
    def __init__(self, address: str, password: str, *, factory=imaplib.IMAP4_SSL, host: str = "imap.gmail.com",
                 port: int = 993, inbox: str = "INBOX"):
        self.address, self._password = address.lower(), password
        self._factory, self._host, self._port, self._inbox = factory, host, port, inbox
        self._conn = None

    def __enter__(self) -> ImapMailbox:
        try:
            self._conn = self._factory(self._host, self._port)
            self._conn.login(self.address, self._password)
        except imaplib.IMAP4.error as exc:
            raise ImapLoginFailure(f"Gmail IMAP login failed ({exc}); is IMAP enabled and the app password valid?") from exc
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._conn.logout()
        except Exception:   # noqa: BLE001 - closing must never mask the real error
            pass

    # -- internals -------------------------------------------------------------------------------
    def _select(self, mailbox: str, readonly: bool = True) -> bool:
        status, _ = self._conn.select(f'"{mailbox}"', readonly=readonly)
        return status == "OK"

    def _search(self, *criteria: str) -> list[bytes]:
        status, data = self._conn.search(None, *criteria)
        return data[0].split() if status == "OK" and data and data[0] else []

    def _fetch(self, num: bytes) -> EmailMessage | None:
        status, data = self._conn.fetch(num, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1], policy=policy.default)

    # -- replies ---------------------------------------------------------------------------------
    def find_replies(self, sent_ids: list[str]) -> list[Inbound]:
        found: dict[str, Inbound] = {}
        if not sent_ids or not self._select(self._inbox):
            return []
        for sent_id in sent_ids:
            nums = set(self._search("HEADER", "In-Reply-To", f'"{sent_id}"')) | \
                   set(self._search("HEADER", "References", f'"{sent_id}"'))
            for num in nums:
                msg = self._fetch(num)
                if msg is None:
                    continue
                sender = getaddresses([str(msg.get("From", ""))])[0][1].lower()
                refs = str(msg.get("References", "")).split()
                if sender == self.address or (sent_id not in refs and sent_id != str(msg.get("In-Reply-To", ""))):
                    continue   # our own message, or a false substring match
                if re.search(r"mailer-daemon|postmaster", sender):
                    continue   # bounces are handled separately
                mid = str(msg.get("Message-ID", "")).strip()
                try:
                    received = parsedate_to_datetime(str(msg["Date"])).isoformat()
                except (TypeError, ValueError):
                    received = ""
                found[mid or f"{sent_id}:{num.decode()}"] = Inbound(
                    message_id=mid, in_reply_to=str(msg.get("In-Reply-To", "")), references=refs, from_addr=sender,
                    subject=str(msg.get("Subject", "")), received_at=received,
                    excerpt=strip_quoted(_body_text(msg))[:EXCERPT_CHARS], matched_sent_id=sent_id)
        return list(found.values())

    # -- bounces -----------------------------------------------------------------------------------
    def find_bounces(self, sent_ids: list[str]) -> list[Bounce]:
        out: dict[str, Bounce] = {}
        if not sent_ids or not self._select(self._inbox):
            return []
        for sender in ("mailer-daemon", "postmaster"):
            for num in self._search("FROM", f'"{sender}"'):
                msg = self._fetch(num)
                if msg is None:
                    continue
                raw = msg.as_string()
                for sent_id in sent_ids:
                    if sent_id in raw and sent_id not in out:
                        out[sent_id] = Bounce(matched_sent_id=sent_id, from_addr=sender,
                                              summary=re.sub(r"\s+", " ", _body_text(msg))[:300])
        return list(out.values())

    # -- drafts and sent-folder checks ---------------------------------------------------------
    def save_draft(self, msg: EmailMessage) -> None:
        status, _ = self._conn.append(f'"{DRAFTS}"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if status != "OK":
            raise RuntimeError("could not save the draft to [Gmail]/Drafts")

    def find_in_sent(self, *, message_id: str | None = None, subject: str | None = None,
                     to: str | None = None) -> SentLookup:
        """Smoke-test helper: confirm a message we sent appears in Sent (by Message-ID, else subject+recipient)."""
        if not self._select(SENT):
            return SentLookup(False)
        nums = self._search("HEADER", "Message-ID", f'"{message_id}"') if message_id else []
        if not nums and subject and to:
            nums = self._search("HEADER", "Subject", f'"{subject}"', "TO", f'"{to}"')
        if not nums:
            return SentLookup(False)
        msg = self._fetch(nums[-1])
        if msg is None:
            return SentLookup(False)
        return SentLookup(True, str(msg.get("Message-ID", "")).strip(), str(msg.get("In-Reply-To", "")).strip() or None,
                          str(msg.get("References", "")).split())
