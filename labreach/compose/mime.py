"""Plain-text UTF-8 MIME messages with a tool-generated Message-ID. One recipient, no CC/BCC."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from pathlib import Path

ADDR_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


@dataclass
class Built:
    message: EmailMessage
    message_id: str


def _clean_header(value: str, what: str) -> str:
    if re.search(r"[\r\n]", value):
        raise ValueError(f"{what} contains a line break (header injection)")
    return value.strip()


def quote_original(original_body: str, original_signature: str, sent_at: datetime, sender: str) -> str:
    stamp = sent_at.strftime("%a, %b %d, %Y at %I:%M %p UTC")
    quoted = "\n".join("> " + line if line else ">" for line in (original_body + "\n\n" + original_signature).splitlines())
    return f"On {stamp}, {sender} wrote:\n{quoted}"


def build_message(*, from_name: str, from_addr: str, to_addr: str, subject: str, body: str, signature: str,
                  now: datetime, attachment: Path | None = None, attachment_name: str | None = None,
                  in_reply_to: str | None = None, references: list[str] | None = None,
                  quoted: str | None = None) -> Built:
    if not ADDR_RE.match(to_addr):
        raise ValueError(f"recipient must be exactly one plain address, got {to_addr!r}")
    msg = EmailMessage()
    domain = from_addr.split("@", 1)[1]
    message_id = make_msgid(domain=domain)
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_addr
    msg["Subject"] = _clean_header(subject, "subject")
    msg["Date"] = format_datetime(now)
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = _clean_header(in_reply_to, "In-Reply-To")
        msg["References"] = " ".join(_clean_header(r, "References") for r in (references or [in_reply_to]))
    text = f"{body}\n\n{signature}\n"
    if quoted:
        text += f"\n{quoted}\n"
    msg.set_content(text, charset="utf-8")
    if attachment is not None:
        if in_reply_to:
            raise ValueError("follow-ups carry no attachments")
        data = Path(attachment).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=attachment_name or Path(attachment).name)
    return Built(message=msg, message_id=message_id)


def followup_subject(original_subject: str) -> str:
    return original_subject if re.match(r"^re:\s", original_subject, re.IGNORECASE) else f"Re: {original_subject}"
