"""Tell the parent when something stops the tool: console output plus a clearly labeled Gmail DRAFT to the
parent's own address (never a sent email)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from rich.console import Console

from .compose import mime
from .db import get_state, log_event, set_state
from .mail_imap import ImapMailbox

console = Console(stderr=True)


def alert(conn: sqlite3.Connection, settings: dict, reason: str, mailbox: ImapMailbox | None) -> None:
    """Idempotent per reason: prints, logs, and saves one draft in the student's Gmail Drafts folder."""
    console.print(f"[bold red]LABREACH STOPPED:[/bold red] {reason}")
    if get_state(conn, "alerted_reason") == reason:
        return
    set_state(conn, "alerted_reason", reason)
    log_event(conn, "alert", reason=reason)
    if mailbox is None:
        return
    me = settings["student"]["gmail"]
    built = mime.build_message(
        from_name="LabReach", from_addr=me, to_addr=me, subject=f"[LabReach ALERT] Sending stopped: {reason[:70]}",
        body=("LabReach has stopped all sending and will not send again until you resume it.\n\n"
              f"Reason: {reason}\n\nReview it, fix the cause, then run: labreach resume"),
        signature="LabReach (automatic alert draft; this was not sent)", now=datetime.now(UTC))
    try:
        mailbox.save_draft(built.message)
    except Exception as exc:   # noqa: BLE001 - the console message above already went out
        console.print(f"[yellow]Could not save the alert draft: {exc}[/yellow]")
