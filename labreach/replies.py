"""Reply sync, classification and safe handling. We NEVER auto-respond: positive/ambiguous replies stop the
sequence and produce a Gmail DRAFT for the parent; complaints or anything unclear engage the kill switch."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from . import killswitch
from .claude_cli import ask_claude, wrap_untrusted
from .compose import mime
from .db import log_event, utc_now_str
from .mail_imap import ImapMailbox, Inbound
from .scheduler import is_business_day

STOP_PATTERNS = [
    r"\bdo(?:n'?t| not) (?:contact|email|e-mail|write|message)\b", r"\bstop (?:e-?mailing|contacting|sending|writing)\b",
    r"\bremove (?:me|my|this)\b", r"\bunsubscribe\b", r"\bspam\b", r"\bwho gave you\b",
    r"\bhow did you (?:get|find|obtain) (?:my|this) (?:e-?mail|address|contact)\b", r"\breport(?:ed|ing)? (?:you|this)\b",
    r"\bcompliance\b", r"\bminors?\b.{0,30}\b(?:office|protection)\b", r"\bprotection of minors\b",
    r"\byouth (?:programs?|protection)\b", r"\btitle ix\b", r"\bharass",
]
HS_DECLINE_PATTERNS = [
    r"\b(?:not|n't|unable to|cannot|can't)\b.{0,25}\b(?:accept|take|host|hire|mentor|supervise|work with)\b.{0,30}"
    r"\b(?:high[- ]school|hs|minors?|pre-?college|under ?18|undergrad)",
    r"\bhigh[- ]school students?\b.{0,40}\b(?:not|aren'?t|cannot|can'?t)\b", r"\bmust be (?:at least )?18\b",
    r"\b18 (?:years )?(?:or older|and (?:over|older))\b", r"\bno high[- ]school\b",
]
COMPLIANCE_SENDER = re.compile(r"compliance|minors?|youth|risk|ehs|titleix|ethics|legal|safeguard|protection", re.I)
OOO = re.compile(r"\b(?:out of (?:the )?office|automatic reply|auto-?reply|on (?:vacation|leave|sabbatical))\b", re.I)
MIN_CONFIDENCE = 0.8
Label = Literal["positive", "ambiguous", "decline_hs", "decline_other", "out_of_office", "unsafe"]


class ReplyClassification(BaseModel):
    label: Label
    confidence: float


def rule_classify(excerpt: str, sender: str) -> Label | None:
    text = excerpt.lower()
    if any(re.search(p, text) for p in STOP_PATTERNS) or COMPLIANCE_SENDER.search(sender.split("@")[0]):
        return "unsafe"
    if any(re.search(p, text) for p in HS_DECLINE_PATTERNS):
        return "decline_hs"
    if OOO.search(text) and len(text) < 800:
        return "out_of_office"
    return None


def classify_reply(conn: sqlite3.Connection, excerpt: str, sender: str, settings: dict) -> tuple[Label, float]:
    """Rules first (cheap, conservative), then one tool-free Claude call. Low confidence means 'unsafe'."""
    by_rule = rule_classify(excerpt, sender)
    if by_rule:
        return by_rule, 1.0
    prompt = (
        "Classify this reply to a high school student's research-inquiry email. Labels: positive (open to a call, "
        "interested, asks a question), ambiguous (unclear intent, forwards to someone, asks for more info), "
        "decline_hs (cannot take high school students), decline_other (no capacity/not now), out_of_office, "
        "unsafe (complaint, anger, asks to stop/remove, privacy or policy concern, anything from a compliance or "
        "minors office). Give a confidence 0-1. Reply JSON {label, confidence}.\n\n" + wrap_untrusted("reply", excerpt))
    out = ask_claude(prompt, schema=ReplyClassification, conn=conn, purpose="classify_reply",
                     max_calls_per_day=settings["limits"]["max_claude_calls_per_day"])
    if out.confidence < MIN_CONFIDENCE:
        return "unsafe", out.confidence
    return out.label, out.confidence


# ---- inbox sync ---------------------------------------------------------------------------------

def active_sent_ids(conn: sqlite3.Connection) -> dict[str, int]:
    """message_id -> email id for every sent message whose thread is still live."""
    rows = conn.execute(
        "SELECT e.id, e.message_id FROM emails e JOIN targets t ON t.id = e.target_id "
        "WHERE e.sent_at IS NOT NULL AND e.message_id IS NOT NULL AND e.replied_at IS NULL AND e.bounced_at IS NULL "
        "AND t.status NOT IN ('closed','dnc','bounced')").fetchall()
    return {r["message_id"]: r["id"] for r in rows}


def store_inbound(conn: sqlite3.Connection, inbound: Inbound, email_id: int) -> int | None:
    """Record a reply the moment it is seen; this alone stops follow-ups (replied_at). Returns reply id if new."""
    if conn.execute("SELECT 1 FROM replies WHERE message_id = ?", (inbound.message_id,)).fetchone():
        return None
    target_id = conn.execute("SELECT target_id FROM emails WHERE id = ?", (email_id,)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO replies (email_id, target_id, received_at, message_id, label, excerpt) VALUES (?,?,?,?,?,?)",
        (email_id, target_id, inbound.received_at, inbound.message_id, "unclassified", inbound.excerpt))
    conn.execute("UPDATE emails SET replied_at = ? WHERE id = ?", (utc_now_str(), email_id))
    conn.execute("UPDATE targets SET status = 'replied' WHERE id = ? AND status IN ('active','needs_human','drafted')",
                 (target_id,))
    conn.commit()
    log_event(conn, "reply_received", target_id, email_id=email_id)
    return cur.lastrowid


def sync_inbox(conn: sqlite3.Connection, mailbox: ImapMailbox, settings: dict, stop_file: Path) -> dict:
    from .bounces import process_bounces  # local import: bounces imports killswitch too
    sent = active_sent_ids(conn)
    stats = {"replies": 0, "bounces": 0}
    for b in mailbox.find_bounces(list(sent)):
        process_bounces(conn, b, stop_file, settings)
        stats["bounces"] += 1
    for inbound in mailbox.find_replies([m for m in sent if m in active_sent_ids(conn)]):
        if store_inbound(conn, inbound, sent[inbound.matched_sent_id]):
            stats["replies"] += 1
    return stats


# ---- handling ------------------------------------------------------------------------------------

def _fmt(dt: datetime) -> str:
    return f"{dt:%A, %B} {dt.day} at {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def call_times(tz: str, settings: dict, now: datetime) -> list[str]:
    """Three after-school call slots on upcoming business days, shown in the recipient's time zone."""
    cfg = settings["reply_drafts"]
    pacific, zone = ZoneInfo(settings["timezone"]), ZoneInfo(tz)
    day, out, seen = now.astimezone(pacific).date(), [], 0
    while len(out) < cfg["count"]:
        day += timedelta(days=1)
        if not is_business_day(day):
            continue
        seen += 1
        if seen < cfg["min_days_out"]:
            continue
        slot = datetime(day.year, day.month, day.day, cfg["call_hour_pacific"], tzinfo=pacific)
        local = slot.astimezone(zone)
        text = f"{_fmt(local)} {local.tzname()}"
        out.append(text if tz == settings["timezone"] else f"{text} ({_fmt(slot).split(' at ')[1]} Pacific)")
    return out


def _greeting_name(target: sqlite3.Row) -> str:
    if target["role"] == "professor":
        return f"Professor {target['last_name']}"
    if target["role"] in ("postdoc", "staff"):
        return f"Dr. {target['last_name']}"
    return target["first_name"] or target["name"]


def build_reply_draft(conn: sqlite3.Connection, reply_id: int, settings: dict, label: str, now: datetime):
    """A DRAFT reply for the parent to review. Returns an EmailMessage; never sent by this tool."""
    row = conn.execute(
        "SELECT r.message_id AS reply_mid, e.subject, e.message_id AS sent_id, t.email, t.role, t.last_name, "
        "t.first_name, t.name, t.timezone FROM replies r JOIN emails e ON e.id = r.email_id "
        "JOIN targets t ON t.id = r.target_id WHERE r.id = ?", (reply_id,)).fetchone()
    name = _greeting_name(row)
    if label in ("decline_hs", "decline_other"):
        body = (f"Dear {name},\n\nThank you for taking the time to reply, and for letting me know. I appreciate it and "
                "wish you and your group all the best.")
    else:
        slots = "\n".join(f"- {t}" for t in call_times(row["timezone"] or settings["timezone"], settings, now))
        body = (f"Dear {name},\n\nThank you for getting back to me. I'd be glad to talk. These times work on my end "
                f"(shown in your time zone):\n{slots}\n\nIf none of those fit, I'm happy to work around your schedule.")
    stu = settings["student"]
    body = "[LabReach draft: review and edit before sending]\n\n" + body
    built = mime.build_message(
        from_name=stu["full_name"], from_addr=stu["gmail"], to_addr=row["email"],
        subject=mime.followup_subject(row["subject"] or "Your reply"), body=body,
        signature=f"{stu['full_name']}\n{stu['school']}\n{stu['gmail']}", now=now,
        in_reply_to=row["reply_mid"], references=[row["sent_id"], row["reply_mid"]])
    return built.message


def handle_reply(conn: sqlite3.Connection, reply_id: int, settings: dict, stop_file: Path, mailbox: ImapMailbox | None,
                 now: datetime | None = None) -> str:
    """Classify one stored reply and act. Never sends mail."""
    now = now or datetime.now(UTC)
    r = conn.execute("SELECT r.*, t.email AS temail FROM replies r JOIN targets t ON t.id = r.target_id WHERE r.id = ?",
                     (reply_id,)).fetchone()
    sender = (r["temail"] or "").lower()
    label, confidence = classify_reply(conn, r["excerpt"] or "", sender, settings)
    conn.execute("UPDATE replies SET label = ?, confidence = ? WHERE id = ?", (label, confidence, reply_id))
    conn.commit()
    tid = r["target_id"]
    if label == "out_of_office":
        conn.execute("UPDATE emails SET replied_at = NULL WHERE id = ?", (r["email_id"],))   # not a real reply
        conn.execute("UPDATE targets SET status = 'active' WHERE id = ? AND status = 'replied'", (tid,))
        conn.commit()
        log_event(conn, "reply_out_of_office", tid)
        return label
    if label == "unsafe":
        conn.execute("INSERT INTO do_not_contact (email, reason) VALUES (?, ?)", (sender, "unsafe or unclear reply"))
        conn.execute("UPDATE targets SET status = 'dnc' WHERE id = ?", (tid,))
        conn.commit()
        killswitch.on_unsafe_reply(conn, stop_file, tid, "complaint, stop request, or unclassifiable reply")
        return label
    if label in ("decline_hs", "decline_other"):
        conn.execute("INSERT INTO do_not_contact (email, reason) VALUES (?, ?)", (sender, label))
        conn.execute("UPDATE targets SET status = 'closed' WHERE id = ?", (tid,))
        conn.commit()
        if label == "decline_hs" and settings.get("replies", {}).get("halt_on_hs_decline", False):
            killswitch.on_unsafe_reply(conn, stop_file, tid, "high-school decline (halt_on_hs_decline is on)")
    else:
        conn.execute("UPDATE targets SET status = 'replied' WHERE id = ?", (tid,))
        conn.commit()
    if mailbox is not None:
        mailbox.save_draft(build_reply_draft(conn, reply_id, settings, label, now))
        conn.execute("UPDATE replies SET draft_saved = 1 WHERE id = ?", (reply_id,))
        conn.commit()
    log_event(conn, "reply_handled", tid, label=label, draft=mailbox is not None)
    return label


def pending_replies(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM replies WHERE label = 'unclassified' ORDER BY id")]
