"""The single-process run loop: kill switch -> reply/bounce sync -> discovery top-up -> drafting -> gates ->
sending (windows, caps, jitter). Initial emails only: the parent writes every follow-up personally.
Dry-run by default; the pilot never sends."""

from __future__ import annotations

import contextlib
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import budget, killswitch, notify, replies, scheduler
from .claude_cli import ClaudeError, ClaudeNotLoggedIn, ask_claude
from .compose import mime
from .compose import render as render_mod
from .config import is_live
from .db import get_state, log_event, utc_now_str
from .discovery import run as discovery
from .discovery.fetch import Fetcher
from .gates import GateResults, run_gates
from .mail_imap import ImapLoginFailure
from .mail_smtp import AccountProblem, LoginFailure, RecipientRejected, SmtpSender, TransientSendError
from .models import StudentProfile
from .personalize.generate import draft_initial

PILOT_KEY = "pilot_approved"
LIVE_CONFIRMED_KEY = "live_confirmed"


class RunLocked(RuntimeError):
    pass


@contextlib.contextmanager
def run_lock(path: Path):
    """Two runs never overlap. A lock left by a dead process is taken over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(path.read_text().strip())
            os.kill(pid, 0)
            alive = True
        except (ValueError, ProcessLookupError, PermissionError, OSError) as exc:
            alive = isinstance(exc, PermissionError)
        if alive:
            raise RunLocked(f"another run is active (lock {path})") from None
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


@dataclass
class RunContext:
    conn: sqlite3.Connection
    settings: dict
    profile: StudentProfile
    templates: dict[str, dict]
    stop_file: Path
    out_dir: Path
    fetcher: Fetcher | None = None
    mailbox_factory: object | None = None      # () -> ImapMailbox context manager (live mode only)
    sender: SmtpSender | None = None
    now: object = lambda: datetime.now(UTC)    # noqa: E731
    sleep: object = time.sleep
    rng: random.Random = field(default_factory=random.Random)
    resume_pdf: Path | None = None
    enforce_lock: bool = True
    ask_override: object | None = None         # tests inject a fake asker
    draft_limit: int = 10


@dataclass
class RunReport:
    notes: list[str] = field(default_factory=list)
    sent: int = 0
    dry_run_files: int = 0
    pilot_files: int = 0
    needs_human: int = 0
    stopped: str | None = None

    def say(self, msg: str) -> None:
        self.notes.append(msg)


def mode(ctx: RunContext) -> str:
    """dry_run unless LABREACH_MODE=live AND the parent typed the go-live confirmation; pilot until approve-pilot."""
    if not is_live() or not get_state(ctx.conn, LIVE_CONFIRMED_KEY):
        return "dry_run"
    return "live" if get_state(ctx.conn, PILOT_KEY) else "pilot"


def make_ask(ctx: RunContext):
    if ctx.ask_override is not None:
        return ctx.ask_override
    limit = ctx.settings["limits"]["max_claude_calls_per_day"]

    def ask(prompt, schema, tools=None, purpose="pipeline"):
        return ask_claude(prompt, tools=tools, schema=schema, conn=ctx.conn, purpose=purpose, max_calls_per_day=limit)
    return ask


def _email_file(out: Path, email: sqlite3.Row, target: sqlite3.Row, gates: GateResults, settings: dict, label: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    name = f"{email['id']:03d}_{(target['last_name'] or 'x').lower()}_{email['kind']}.txt"
    stu = settings["student"]
    text = (f"[{label}] NOT SENT\nTo: {target['name']} <{target['email']}>  ({target['role']}, {target['university']})\n"
            f"From: {stu['full_name']} <{stu['gmail']}>\nSubject: {email['subject'] or '(reply in same thread)'}\n"
            f"Attachment: {stu['resume_attachment_name'] if email['kind'] == 'initial' else 'none'}\n"
            f"Template: {email['template_version']}   Words: {email['word_count']}\n"
            f"Address source: {target['email_source_url']}\n  \"{target['email_source_snippet']}\"\n"
            f"Gates: {json.dumps({n: ok for n, (ok, _) in gates.results.items()})}\n"
            + "".join(f"  - {f}\n" for f in gates.failures()) + f"\n{'-' * 70}\n\n{email['body']}\n\n"
            f"{stu['full_name']}\nJunior, {stu['school']} ({stu['school_city']}, CA)\n{stu['gmail']}\n")
    path = out / name
    path.write_text(text)
    return path


# ---- steps --------------------------------------------------------------------------------------------

def step_sync(ctx: RunContext, report: RunReport) -> None:
    """Replies and bounces (read-only IMAP), then classify anything pending. Live mode only."""
    if mode(ctx) == "dry_run" or ctx.mailbox_factory is None:
        return
    try:
        with ctx.mailbox_factory() as box:
            stats = replies.sync_inbox(ctx.conn, box, ctx.settings, ctx.stop_file)
            report.say(f"sync: {stats['replies']} new replies, {stats['bounces']} bounces")
            for rid in replies.pending_replies(ctx.conn):
                label = replies.handle_reply(ctx.conn, rid, ctx.settings, ctx.stop_file, box, ctx.now())
                report.say(f"reply {rid} classified as {label}")
            if killswitch.is_engaged(ctx.conn, ctx.stop_file):
                notify.alert(ctx.conn, ctx.settings, killswitch.reason(ctx.conn, ctx.stop_file) or "stopped", box)
    except ImapLoginFailure as exc:
        killswitch.on_login_failure(ctx.conn, ctx.stop_file, f"Gmail IMAP: {exc}")
        notify.alert(ctx.conn, ctx.settings, str(exc), None)
        report.stopped = str(exc)


def step_discovery(ctx: RunContext, report: RunReport) -> None:
    cfg = ctx.settings["limits"]
    if ctx.fetcher is None or discovery.eligible_count(ctx.conn) >= cfg["target_queue_size"]:
        return
    if budget.remaining(ctx.conn, cfg["max_claude_calls_per_day"]) < 4:
        report.say("discovery paused: Claude call budget nearly used up for today")
        return
    out = discovery.top_up(ctx.conn, ctx.settings, ctx.fetcher, make_ask(ctx), ctx.now(), max_candidates=10)
    report.say(f"discovery: {out.candidates_processed} candidates, {out.eligible_added} new eligible, outcomes {out.outcomes}")


def step_drafting(ctx: RunContext, report: RunReport) -> None:
    limit = ctx.draft_limit
    rows = ctx.conn.execute(
        "SELECT t.* FROM targets t WHERE t.status = 'eligible' AND NOT EXISTS (SELECT 1 FROM emails e WHERE e.target_id = t.id "
        "AND e.kind = 'initial') ORDER BY t.fit_score DESC, t.id LIMIT ?", (limit,)).fetchall()
    ask = make_ask(ctx)
    today = ctx.now().astimezone(ZoneInfo(ctx.settings["timezone"])).date()
    for row in rows:
        if budget.remaining(ctx.conn, ctx.settings["limits"]["max_claude_calls_per_day"]) < 1:
            report.say("drafting paused: Claude call budget used up for today")
            return
        outcome = draft_initial(ctx.conn, row, ctx.templates[row["variant"]], ctx.profile, ctx.settings, ask, today,
                                ctx.stop_file, enforce_lock=ctx.enforce_lock)
        if not outcome.ok:
            report.needs_human += 1
            report.say(f"needs_human: {row['name']}: {outcome.reasons[:2]}")
        if killswitch.check_claim_failure_streak(ctx.conn, ctx.stop_file, streak=ctx.settings["limits"]["claim_failure_streak"]):
            report.stopped = killswitch.reason(ctx.conn, ctx.stop_file)
            return


def _record_gates(ctx: RunContext, email: sqlite3.Row, gates: GateResults) -> None:
    ctx.conn.execute("UPDATE emails SET gate_results_json = ? WHERE id = ?", (gates.as_json(), email["id"]))
    ctx.conn.commit()


def _send(ctx: RunContext, email: sqlite3.Row, target: sqlite3.Row, report: RunReport) -> bool:
    """Build, send, record. Returns False if the run must stop."""
    stu = ctx.settings["student"]
    now = ctx.now()
    built = mime.build_message(from_name=stu["full_name"], from_addr=stu["gmail"], to_addr=target["email"],
                               subject=email["subject"], body=email["body"], signature=render_signature(ctx), now=now,
                               attachment=ctx.resume_pdf, attachment_name=stu["resume_attachment_name"])
    try:
        ctx.sender.send(built.message)
    except LoginFailure as exc:
        killswitch.on_login_failure(ctx.conn, ctx.stop_file, f"Gmail SMTP: {exc}")
        report.stopped = str(exc)
        return False
    except AccountProblem as exc:
        killswitch.on_smtp_error(ctx.conn, ctx.stop_file, exc.code, exc.text) or killswitch.engage(
            ctx.conn, ctx.stop_file, f"SMTP account problem: {exc}")
        report.stopped = str(exc)
        return False
    except TransientSendError as exc:
        report.say(f"transient send error, will retry next run: {exc}")
        return False
    except RecipientRejected as exc:
        ctx.conn.execute("UPDATE emails SET bounced_at = ?, state = 'bounced' WHERE id = ?", (utc_now_str(now), email["id"]))
        ctx.conn.execute("UPDATE targets SET status = 'bounced' WHERE id = ?", (target["id"],))
        ctx.conn.execute("INSERT INTO do_not_contact (email, reason) VALUES (?, 'recipient rejected')", (target["email"].lower(),))
        ctx.conn.commit()
        log_event(ctx.conn, "send_rejected", target["id"], detail=exc.text[:200])
        lim = ctx.settings["limits"]
        killswitch.check_bounce_rate(ctx.conn, ctx.stop_file, threshold=lim["bounce_rate_threshold"], window=lim["bounce_window"])
        return not killswitch.is_engaged(ctx.conn, ctx.stop_file)
    ctx.conn.execute("UPDATE emails SET state = 'sent', sent_at = ?, message_id = ?, thread_root_message_id = ? WHERE id = ?",
                     (utc_now_str(now), built.message_id, built.message_id, email["id"]))
    ctx.conn.execute("UPDATE targets SET status = 'active' WHERE id = ?", (target["id"],))
    ctx.conn.commit()
    log_event(ctx.conn, "email_sent", target["id"], kind=email["kind"], message_id=built.message_id)
    report.sent += 1
    return True


def render_signature(ctx: RunContext) -> str:
    return render_mod.signature(ctx.profile)


def step_sending(ctx: RunContext, report: RunReport) -> None:
    m = mode(ctx)
    now = ctx.now()
    pilot_size = ctx.settings["caps"]["pilot_size"]
    initials = ctx.conn.execute(
        "SELECT e.* FROM emails e JOIN targets t ON t.id = e.target_id WHERE e.kind = 'initial' AND e.sent_at IS NULL "
        "AND e.state IN ('draft','pilot') AND t.status = 'drafted' ORDER BY t.fit_score DESC, e.id").fetchall()
    pilot_count = ctx.conn.execute("SELECT COUNT(*) FROM emails WHERE kind = 'initial' AND state = 'pilot'").fetchone()[0]
    for email in initials:
        if killswitch.is_engaged(ctx.conn, ctx.stop_file):
            report.stopped = killswitch.reason(ctx.conn, ctx.stop_file)
            return
        target = ctx.conn.execute("SELECT * FROM targets WHERE id = ?", (email["target_id"],)).fetchone()
        gates = run_gates(ctx.conn, email, ctx.settings, ctx.profile, ctx.templates, now, ctx.stop_file, ctx.enforce_lock)
        _record_gates(ctx, email, gates)
        content_ok = gates.passed(("template", "lint", "claims", "provenance", "eligibility"))
        if not content_ok:
            ctx.conn.execute("UPDATE emails SET state = 'needs_human' WHERE id = ? AND sent_at IS NULL", (email["id"],))
            ctx.conn.execute("UPDATE targets SET status = 'needs_human' WHERE id = ? AND status = 'drafted'", (target["id"],))
            ctx.conn.commit()
            report.needs_human += 1
            report.say(f"needs_human: {target['name']}: {gates.failures()[:2]}")
            continue
        if m in ("dry_run", "pilot"):
            if m == "pilot" and pilot_count >= pilot_size and email["state"] != "pilot":
                continue                       # pilot already full; wait for approve-pilot
            label = "DRY RUN" if m == "dry_run" else "PILOT"
            folder = "dry_run" if m == "dry_run" else "pilot"
            path = _email_file(ctx.out_dir / folder, email, target, gates, ctx.settings, label)
            if m == "pilot" and email["state"] != "pilot":
                ctx.conn.execute("UPDATE emails SET state = 'pilot', pilot_file = ? WHERE id = ?", (str(path), email["id"]))
                ctx.conn.commit()
                pilot_count += 1
                report.pilot_files += 1
            elif m == "dry_run":
                report.dry_run_files += 1
            continue
        if m != "live":
            continue
        if not gates.passed():                  # timing gates (window, caps, kill switch)
            continue
        if email["send_after"] is None:
            tz = target["timezone"] or ctx.settings["timezone"]
            when = scheduler.jittered_send_after(now, tz, ctx.settings, target["university"], ctx.rng)
            ctx.conn.execute("UPDATE emails SET send_after = ? WHERE id = ?", (utc_now_str(when), email["id"]))
            ctx.conn.commit()
            email = ctx.conn.execute("SELECT * FROM emails WHERE id = ?", (email["id"],)).fetchone()
        if email["send_after"] > utc_now_str(now):
            continue
        if not _send(ctx, email, target, report):
            return
        lo, hi = 20, 60
        ctx.sleep(ctx.rng.uniform(lo, hi))


def finish(ctx: RunContext, report: RunReport) -> RunReport:
    """If anything engaged the kill switch during the run, tell the parent (console + Gmail draft)."""
    if killswitch.is_engaged(ctx.conn, ctx.stop_file):
        report.stopped = report.stopped or killswitch.reason(ctx.conn, ctx.stop_file)
        why = report.stopped or "stopped"
        if mode(ctx) != "dry_run" and ctx.mailbox_factory is not None:
            try:
                with ctx.mailbox_factory() as box:
                    notify.alert(ctx.conn, ctx.settings, why, box)
            except Exception:   # noqa: BLE001 - login may be the very problem; console alert still goes out
                notify.alert(ctx.conn, ctx.settings, why, None)
        else:
            notify.alert(ctx.conn, ctx.settings, why, None)
    return report


def run_once(ctx: RunContext) -> RunReport:
    return finish(ctx, _run_once(ctx))


def _run_once(ctx: RunContext) -> RunReport:
    report = RunReport()
    if killswitch.is_engaged(ctx.conn, ctx.stop_file):
        report.stopped = killswitch.reason(ctx.conn, ctx.stop_file)
        report.say(f"kill switch engaged ({report.stopped}); nothing was done. Fix the cause, then `labreach resume`.")
        return report
    try:
        step_sync(ctx, report)
        if report.stopped or killswitch.is_engaged(ctx.conn, ctx.stop_file):
            report.stopped = report.stopped or killswitch.reason(ctx.conn, ctx.stop_file)
            return report
        step_discovery(ctx, report)
        step_drafting(ctx, report)
        if report.stopped:
            return report
        step_sending(ctx, report)
    except budget.BudgetExhausted as exc:
        report.say(str(exc))
    except ClaudeNotLoggedIn as exc:
        report.stopped = f"claude CLI is not logged in: {exc}. Run `claude` once and log in, then re-run."
        killswitch.engage(ctx.conn, ctx.stop_file, report.stopped)
    except ClaudeError as exc:
        report.say(f"claude call failed ({exc}); stopped this run cleanly, nothing was guessed")
    return report
