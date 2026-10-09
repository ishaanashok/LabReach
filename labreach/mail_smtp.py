"""Gmail SMTP sender. The only code that can put mail on the wire, and it refuses unless live mode is on."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import is_live
from .killswitch import is_account_problem


class LiveModeRequired(RuntimeError):
    pass


class LoginFailure(RuntimeError):
    pass


class AccountProblem(RuntimeError):
    """Sending restriction or account trouble: the caller must engage the kill switch."""

    def __init__(self, code: int | None, text: str):
        super().__init__(f"SMTP {code}: {text}")
        self.code, self.text = code, text


class TransientSendError(RuntimeError):
    """Network trouble or a temporary condition: skip this run and retry later (no kill switch)."""


class RecipientRejected(RuntimeError):
    """The address itself was refused (counts as a bounce, not an account problem)."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class SmtpSender:
    def __init__(self, address: str, password: str, *, factory=smtplib.SMTP_SSL, host: str = "smtp.gmail.com",
                 port: int = 465):
        self._address, self._password = address, password
        self._factory, self._host, self._port = factory, host, port

    def send(self, msg: EmailMessage) -> None:
        if not is_live():
            raise LiveModeRequired("refusing to send: LABREACH_MODE=live is not set")
        try:
            with self._factory(self._host, self._port, timeout=30) as smtp:
                smtp.login(self._address, self._password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise LoginFailure(f"Gmail rejected the login: {exc.smtp_error!r}") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise RecipientRejected(str(exc)) from exc
        except smtplib.SMTPResponseException as exc:
            text = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            if is_account_problem(exc.smtp_code, text):
                raise AccountProblem(exc.smtp_code, text) from exc
            raise RecipientRejected(f"{exc.smtp_code} {text}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise TransientSendError(f"connection problem: {exc}") from exc
