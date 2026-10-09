"""In-memory SMTP and IMAP doubles. No network."""

import imaplib
import smtplib


class FakeSMTP:
    """Use as factory: FakeSMTP.factory(...) returns itself; records every send."""

    def __init__(self, login_error=None, send_error=None):
        self.logins, self.sent = [], []
        self.login_error, self.send_error = login_error, send_error

    def __call__(self, host, port, timeout=None):
        self.host, self.port = host, port
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if self.login_error:
            raise self.login_error
        self.logins.append(user)

    def send_message(self, msg):
        if self.send_error:
            raise self.send_error
        self.sent.append(msg)


def smtp_auth_error():
    return smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")


class FakeIMAP:
    """boxes: {"INBOX": [raw_bytes, ...], "[Gmail]/Sent Mail": [...]}. Numbers are 1-based."""

    def __init__(self, boxes=None, bad_login=False):
        self.boxes = boxes or {"INBOX": []}
        self.bad_login = bad_login
        self.selects, self.fetched, self.appended, self.current = [], [], [], None

    def __call__(self, host, port):
        return self

    def login(self, user, password):
        if self.bad_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def logout(self):
        return "BYE", []

    def select(self, name, readonly=False):
        box = name.strip('"')
        self.selects.append((box, readonly))
        if box not in self.boxes:
            return "NO", []
        self.current = box
        return "OK", [str(len(self.boxes[box])).encode()]

    def _headers(self, raw: bytes) -> dict[str, str]:
        import email
        msg = email.message_from_bytes(raw)
        return {k.lower(): str(v) for k, v in msg.items()}

    def search(self, charset, *crit):
        crit = [c.strip('"') if isinstance(c, str) else c for c in crit]
        tests, i = [], 0
        while i < len(crit):
            if crit[i] == "HEADER":
                tests.append((crit[i + 1].lower(), crit[i + 2]))
                i += 3
            else:
                tests.append((crit[i].lower(), crit[i + 1]))
                i += 2
        hits = []
        for n, raw in enumerate(self.boxes[self.current], start=1):
            headers = self._headers(raw)
            if all(value.lower() in headers.get(name, "").lower() for name, value in tests):
                hits.append(str(n).encode())
        return "OK", [b" ".join(hits)]

    def fetch(self, num, spec):
        n = int(num)
        self.fetched.append((self.current, n))
        raw = self.boxes[self.current][n - 1]
        return "OK", [(b"%d (BODY[] {%d}" % (n, len(raw)), raw), b")"]

    def append(self, mailbox, flags, date, data):
        self.appended.append((mailbox.strip('"'), flags, data))
        return "OK", []

    def __getattr__(self, name):   # any mutating IMAP verb is a bug in a read-only client
        if name in {"store", "expunge", "copy", "delete", "rename", "create"}:
            raise AssertionError(f"IMAP {name} must never be called")
        raise AttributeError(name)


def raw_message(*, frm, to, subject, message_id, body="hello", in_reply_to=None, references=None):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = frm, to, subject, message_id
    msg["Date"] = "Tue, 20 Oct 2026 09:00:00 -0700"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    msg.set_content(body)
    return msg.as_bytes()
