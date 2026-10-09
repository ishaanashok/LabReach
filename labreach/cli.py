"""LabReach command line. Dry-run is the default; nothing here contacts anyone yet."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import auth as auth_mod
from . import budget as budget_mod
from . import killswitch
from . import run as run_mod
from .claude_cli import ClaudeError, api_key_present, missing_flags
from .compose import lint as lint_mod
from .compose import render as render_mod
from .compose import templates as tpl
from .config import REPO_ROOT, data_dir, db_path, is_live, load_settings, stop_file
from .db import connect, log_event, set_state
from .discovery import run as discovery
from .discovery.fetch import Fetcher
from .logging_setup import setup_logging
from .mail_imap import ImapLoginFailure, ImapMailbox
from .mail_smtp import SmtpSender
from .personalize.samples import SAMPLES
from .profile import approve_profile, is_approved, load_profile
from .scheduler import LIVE_START_KEY

app = typer.Typer(help="LabReach Auto: local outreach agent (dry-run unless LABREACH_MODE=live).", no_args_is_help=True)
profile_app = typer.Typer(help="Student profile facts")
templates_app = typer.Typer(help="Email templates")
dnc_app = typer.Typer(help="Do-not-contact list")
programs_app = typer.Typer(help="Summer programs to track")
app.add_typer(profile_app, name="profile")
app.add_typer(templates_app, name="templates")
app.add_typer(dnc_app, name="dnc")
app.add_typer(programs_app, name="programs")
console = Console()
PROFILE_PATH = REPO_ROOT / "data" / "student_profile.yaml"


def _conn() -> sqlite3.Connection:
    setup_logging(data_dir() / "logs")
    return connect(db_path())


@app.callback()
def main(dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Dry-run is ON unless LABREACH_MODE=live")):
    if api_key_present():
        console.print("[yellow]ANTHROPIC_API_KEY is set; it is stripped from every claude call so usage "
                      "counts against your subscription.[/yellow]")
    if not dry_run and not is_live():
        console.print("[red]--no-dry-run requires LABREACH_MODE=live in the environment. Staying in dry-run.[/red]")


@app.command()
def init() -> None:
    """Create ./labreach_data and the database."""
    conn = _conn()
    conn.close()
    console.print(f"Initialized {data_dir()} (mode: {'LIVE' if is_live() else 'dry-run'})")


@app.command()
def doctor() -> None:
    """Check the local `claude` CLI supports every flag this tool relies on."""
    try:
        missing = missing_flags()
    except (FileNotFoundError, OSError):
        console.print("[red]`claude` not found on PATH. Install Claude Code and log in.[/red]")
        raise typer.Exit(1) from None
    if missing:
        console.print(f"[red]Installed claude lacks flags: {', '.join(missing)}[/red]")
        raise typer.Exit(1)
    console.print("[green]claude CLI supports all required flags.[/green]")


@app.command()
def budget() -> None:
    """Claude calls used today."""
    conn = _conn()
    maximum = load_settings()["limits"]["max_claude_calls_per_day"]
    used = budget_mod.calls_today(conn)
    console.print(f"Claude calls today: {used}/{maximum} ({maximum - used} left)")


@app.command()
def stop(reason: str = typer.Argument("manual stop")) -> None:
    """Engage the kill switch (writes labreach_data/STOP)."""
    conn = _conn()
    killswitch.engage(conn, stop_file(), reason)
    console.print(f"[red]Kill switch engaged:[/red] {reason}")


@app.command()
def pause() -> None:
    """Pause all sending (same effect as stop; resume with `labreach resume`)."""
    stop("paused by user")


@app.command()
def resume() -> None:
    """Clear the kill switch after you have reviewed why it engaged."""
    conn = _conn()
    why = killswitch.reason(conn, stop_file())
    killswitch.resume(conn, stop_file())
    console.print(f"Kill switch cleared (was: {why or 'not engaged'}).")


@app.command()
def status() -> None:
    """Show mode, kill switch, approvals and counts."""
    conn = _conn()
    profile = load_profile(PROFILE_PATH)
    table = Table(show_header=False)
    table.add_row("mode", "LIVE" if is_live() else "dry-run")
    table.add_row("kill switch", killswitch.reason(conn, stop_file()) or "off")
    table.add_row("profile approved", "yes" if is_approved(conn, profile) else "NO")
    table.add_row("templates", ", ".join(f"{k}:{v}" for k, v in tpl.lock_status().items()))
    table.add_row("targets", str(conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]))
    table.add_row("emails sent", str(conn.execute("SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL").fetchone()[0]))
    console.print(table)


@dnc_app.command("add")
def dnc_add(email: str = typer.Argument(None), domain: str = typer.Option(None), reason: str = "manual") -> None:
    """Add an address or a whole domain to do-not-contact."""
    if not email and not domain:
        raise typer.BadParameter("give an email or --domain")
    conn = _conn()
    conn.execute("INSERT INTO do_not_contact (email, domain, reason) VALUES (?, ?, ?)",
                 (email.lower() if email else None, domain.lower() if domain else None, reason))
    conn.commit()
    log_event(conn, "dnc_added", email=email, domain=domain, reason=reason)
    console.print(f"Added to do-not-contact: {email or domain}")


@profile_app.command("show")
def profile_show() -> None:
    """List every fact emails may use, and which still need confirmation."""
    profile = load_profile(PROFILE_PATH)
    table = Table("id", "status", "email wording")
    for fact in profile.facts:
        table.add_row(fact.id, fact.status, fact.email_phrase)
    console.print(table)


@profile_app.command("approve")
def profile_approve() -> None:
    """Record one-time approval of the facts file (any later edit un-approves it)."""
    conn = _conn()
    profile = load_profile(PROFILE_PATH)
    pending = [f.id for f in profile.facts if f.status == "needs_confirmation"]
    digest = approve_profile(conn, profile)
    console.print(f"Profile approved ({digest[:12]}). Facts still needing confirmation (excluded from emails): "
                  f"{', '.join(pending) or 'none'}")


def _preview_text() -> str:
    settings = load_settings()
    profile = load_profile(PROFILE_PATH)
    templates = tpl.load_templates()
    today = date(2026, 10, 13)
    out = ["SAMPLE EMAILS: fictional recipients and papers, for layout review only. Never sent.\n"]
    for tid, sample in SAMPLES.items():
        template = templates[tid]
        r = render_mod.render_initial(template, sample["slots"], sample["recipient"], profile, settings, today,
                                      enforce_lock=False)
        ctx = lint_mod.LintContext(kind="initial", recipient_last=sample["recipient"].last,
                                   subject_terms=[sample["slots"].subject_topic], personalization=r.personalization,
                                   banned_claims=profile.banned_claims)
        report = lint_mod.lint_email(r.subject, r.body, ctx)
        out += [f"===== {tid}: {template['description']} =====", f"Subject: {r.subject}", "", r.body, "", r.signature, "",
                f"[{report.word_count} words | lint {'PASS' if report.ok else 'FAIL: ' + '; '.join(i.rule for i in report.issues)}]\n"]
    return "\n".join(out)


@templates_app.command("build")
def templates_build(out: Path = Path("out/template_preview.txt")) -> None:
    """Render the 3 variants plus follow-ups with fictional sample data for your review."""
    text = _preview_text()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    console.print(text)
    console.print(f"[green]Written to {out}[/green]")


@templates_app.command("status")
def templates_status() -> None:
    for tid, state in tpl.lock_status().items():
        console.print(f"{tid}: {state}")


@templates_app.command("approve")
def templates_approve(yes: bool = typer.Option(False, "--yes", help="Confirm you reviewed the templates")) -> None:
    """Lock the template skeletons (hash + version). Any later change needs approval again."""
    if not yes:
        console.print("Review `labreach templates build`, then re-run with --yes to lock.")
        raise typer.Exit(1)
    conn = _conn()
    lock = tpl.lock_templates()
    log_event(conn, "templates_locked", templates=lock["templates"])
    console.print(f"[green]Locked {len(lock['templates'])} templates.[/green]")


# ---- accounts ------------------------------------------------------------------------------------------

@app.command()
def auth() -> None:
    """Store the Gmail app password in the OS keychain (never in a file, never logged)."""
    settings = load_settings()
    address = typer.prompt("Gmail address", default=settings["student"]["gmail"])
    password = typer.prompt("App password (16 characters from Google Account > Security > App passwords)", hide_input=True)
    auth_mod.store_credentials(address, password)
    console.print("[green]Stored in your OS keychain.[/green] Enable IMAP in Gmail settings, then run `labreach auth-check`.")


@app.command("auth-check")
def auth_check() -> None:
    """Log in to Gmail SMTP and IMAP (login only; sends and reads nothing)."""
    import smtplib
    settings = load_settings()
    address = settings["student"]["gmail"]
    password = auth_mod.get_password(address)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(address, password)
        console.print("[green]SMTP login OK[/green]")
        with ImapMailbox(address, password):
            console.print("[green]IMAP login OK[/green]")
    except (smtplib.SMTPException, ImapLoginFailure, OSError) as exc:
        console.print(f"[red]Login failed: {exc}[/red]\nCheck 2-Step Verification, the app password, and that IMAP is enabled.")
        raise typer.Exit(1) from None


@app.command("go-live")
def go_live() -> None:
    """Typed confirmation that real sending may happen (still needs LABREACH_MODE=live and approve-pilot)."""
    conn = _conn()
    if not is_live():
        console.print("Set LABREACH_MODE=live in the environment first.")
        raise typer.Exit(1)
    if typer.prompt('Type "I CONFIRM" to allow live sending from the Gmail account') != "I CONFIRM":
        console.print("Not confirmed. Staying in dry-run.")
        raise typer.Exit(1)
    set_state(conn, run_mod.LIVE_CONFIRMED_KEY, datetime.now(UTC).isoformat())
    console.print("[yellow]Live mode confirmed. The first 5 emails are still only a pilot until `labreach approve-pilot`.[/yellow]")


# ---- pipeline ------------------------------------------------------------------------------------------

def _context(conn: sqlite3.Connection, *, drafts: int = 10) -> run_mod.RunContext:
    settings = load_settings()
    profile = load_profile(PROFILE_PATH)
    if not is_approved(conn, profile):
        console.print("[red]Profile facts are not approved. Run `labreach profile show`, then `labreach profile approve`.[/red]")
        raise typer.Exit(1)
    live = is_live()
    stu = settings["student"]
    mailbox_factory = sender = None
    if live:
        password = auth_mod.get_password(stu["gmail"])
        mailbox_factory = lambda: ImapMailbox(stu["gmail"], password, inbox=settings["imap"]["inbox"])  # noqa: E731
        sender = SmtpSender(stu["gmail"], password)
    return run_mod.RunContext(
        conn=conn, settings=settings, profile=profile, templates=tpl.load_templates(), stop_file=stop_file(),
        out_dir=Path("out"), fetcher=Fetcher(settings, data_dir() / "cache"), mailbox_factory=mailbox_factory, sender=sender,
        resume_pdf=REPO_ROOT / stu["resume_pdf"], draft_limit=drafts, enforce_lock=live)


def _require_locked_templates() -> None:
    bad = {k: v for k, v in tpl.lock_status().items() if v != "locked"}
    if bad:
        console.print(f"[red]Templates are not locked/approved: {bad}. Review them, then `labreach templates approve --yes`.[/red]")
        raise typer.Exit(1)


@app.command()
def discover(max_candidates: int = typer.Option(15, "--max", help="Candidates to examine this run")) -> None:
    """Find professors: directories, profiles, exclusions, official addresses, verified recent work, fit scores."""
    conn = _conn()
    ctx = _context(conn)
    try:
        report = discovery.top_up(conn, ctx.settings, ctx.fetcher, run_mod.make_ask(ctx), datetime.now(UTC),
                                  max_candidates=max_candidates)
    except (ClaudeError, run_mod.budget.BudgetExhausted) as exc:
        console.print(f"[yellow]Stopped cleanly: {exc}[/yellow]")
        raise typer.Exit(0) from None
    console.print(f"directories added {report.directories_added}, candidates {report.candidates_processed}, "
                  f"new eligible {report.eligible_added}, outcomes {report.outcomes}")
    console.print(f"Eligible queue: {discovery.eligible_count(conn)}/{ctx.settings['limits']['target_queue_size']}")


@app.command()
def run(drafts: int = typer.Option(10, help="Max new drafts per run"),
        loop: bool = typer.Option(False, "--loop", help="Keep running every --interval minutes (or use cron/launchd)"),
        interval: int = typer.Option(20, help="Minutes between runs with --loop")) -> None:
    """One pass: kill switch, sync, discovery, drafting, gates, sending. Dry-run unless live is confirmed."""
    import time
    conn = _conn()
    if is_live():
        _require_locked_templates()   # dry-run may preview drafts before the templates are locked
    ctx = _context(conn, drafts=drafts)
    while True:
        try:
            with run_mod.run_lock(data_dir() / "run.lock"):
                result = run_mod.run_once(ctx)
            console.print(f"[{datetime.now():%H:%M}] mode: {run_mod.mode(ctx)} | sent {result.sent} | dry-run files "
                          f"{result.dry_run_files} | pilot files {result.pilot_files} | needs_human {result.needs_human}")
            for note in result.notes:
                console.print(f"  {note}")
            if result.stopped:
                console.print(f"[red]STOPPED: {result.stopped}[/red]")
                raise typer.Exit(2)
        except run_mod.RunLocked as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            if not loop:
                raise typer.Exit(0) from None
        if not loop:
            return
        time.sleep(interval * 60)


@app.command()
def report(targets: bool = typer.Option(False, "--targets", help="List the top eligible targets")) -> None:
    """Counts, queue, today's caps, Claude budget."""
    conn = _conn()
    settings = load_settings()
    by_status = conn.execute("SELECT status, COUNT(*) n FROM targets GROUP BY status ORDER BY n DESC").fetchall()
    table = Table("target status", "count")
    for r in by_status:
        table.add_row(r["status"], str(r["n"]))
    console.print(table)
    used = run_mod.budget.calls_today(conn)
    console.print(f"Claude calls today: {used}/{settings['limits']['max_claude_calls_per_day']}; "
                  f"sent total: {conn.execute('SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL').fetchone()[0]}; "
                  f"kill switch: {killswitch.reason(conn, stop_file()) or 'off'}")
    if targets:
        t = Table("id", "name", "role", "university", "fit", "variant", "status", "address source")
        for r in conn.execute("SELECT * FROM targets WHERE status IN ('eligible','drafted','needs_human','active') "
                              "ORDER BY fit_score DESC LIMIT 40"):
            t.add_row(str(r["id"]), r["name"], r["role"], r["university"], str(r["fit_score"]), r["variant"], r["status"],
                      r["email_source_url"] or "")
        console.print(t)


@app.command()
def followups() -> None:
    """Who is due for YOUR manual follow-up: initial sent, no reply or bounce yet. LabReach never sends follow-ups."""
    from zoneinfo import ZoneInfo

    from .scheduler import business_days_between, followup_reminder_due
    conn = _conn()
    settings = load_settings()
    now = datetime.now(UTC)
    table = Table("name", "address", "university", "sent", "business days", "subject", "due?")
    rows = conn.execute(
        "SELECT t.name, t.email, t.university, t.timezone, e.sent_at, e.subject FROM emails e JOIN targets t ON t.id = e.target_id "
        "WHERE e.kind = 'initial' AND e.sent_at IS NOT NULL AND e.replied_at IS NULL AND e.bounced_at IS NULL "
        "AND t.status = 'active' ORDER BY e.sent_at").fetchall()
    for r in rows:
        sent = datetime.fromisoformat(r["sent_at"]).replace(tzinfo=UTC)
        tz = r["timezone"] or settings["timezone"]
        days = business_days_between(sent.astimezone(ZoneInfo(tz)).date(), now.astimezone(ZoneInfo(tz)).date())
        due = followup_reminder_due(sent, now, tz, settings)
        table.add_row(r["name"], r["email"], r["university"], r["sent_at"][:10], str(days), r["subject"] or "",
                      "[bold]DUE[/bold]" if due else "")
    console.print(table if rows else "No sent emails are waiting on a reply.")
    console.print("LabReach never sends follow-ups: you write and send them. Check Gmail Drafts first; replies appear there.")


@app.command("needs-human")
def needs_human() -> None:
    """Everything the gates refused, with reasons. Nothing here is sent or silently fixed."""
    conn = _conn()
    rows = conn.execute("SELECT e.id, e.kind, e.gate_results_json, t.name, t.university, t.status, t.notes FROM emails e "
                        "JOIN targets t ON t.id = e.target_id WHERE e.state = 'needs_human'").fetchall()
    for r in rows:
        data = json.loads(r["gate_results_json"] or "{}")
        reasons = data.get("reasons") or [f"{k}: {v['detail']}" for k, v in data.items() if isinstance(v, dict) and not v["ok"]]
        console.print(f"[bold]#{r['id']} {r['name']} ({r['university']}) {r['kind']}[/bold]")
        for reason in reasons:
            console.print(f"   - {reason}")
    for r in conn.execute("SELECT name, university, notes FROM targets WHERE status = 'needs_human' AND id NOT IN "
                          "(SELECT target_id FROM emails)").fetchall():
        console.print(f"[bold]{r['name']} ({r['university']})[/bold]: {r['notes']}")
    if not rows:
        console.print("Nothing waiting for a human.")


@app.command("approve-pilot")
def approve_pilot() -> None:
    """After reading out/pilot/*.txt: turn on live auto-sending (caps apply)."""
    conn = _conn()
    if not is_live() or not run_mod.get_state(conn, run_mod.LIVE_CONFIRMED_KEY):
        console.print("Live mode is not confirmed (LABREACH_MODE=live and `labreach go-live`).")
        raise typer.Exit(1)
    pilot = conn.execute("SELECT COUNT(*) FROM emails WHERE kind='initial' AND state='pilot'").fetchone()[0]
    console.print(f"{pilot} pilot emails are in out/pilot/. Read them before approving.")
    if typer.prompt('Type "APPROVE PILOT" to enable live auto-sending') != "APPROVE PILOT":
        raise typer.Exit(1)
    set_state(conn, run_mod.PILOT_KEY, datetime.now(UTC).isoformat())
    set_state(conn, LIVE_START_KEY, date.today().isoformat())
    log_event(conn, "pilot_approved", pilot_emails=pilot)
    console.print("[green]Live auto-sending enabled (5/day for two weeks, then up to 10/day).[/green]")


@app.command("smoke-test")
def smoke_test(yes: bool = typer.Option(False, "--yes")) -> None:
    """Send ONE email to your own address, confirm it is in Sent, then send an in-thread follow-up and confirm threading."""
    from .compose import mime
    conn = _conn()
    settings = load_settings()
    stu = settings["student"]
    if not is_live():
        console.print("Set LABREACH_MODE=live to run the smoke test (it sends two real emails to yourself).")
        raise typer.Exit(1)
    if not yes and typer.prompt(f'Type "SEND TO {stu["gmail"]}" to send two test emails to yourself') != f"SEND TO {stu['gmail']}":
        raise typer.Exit(1)
    password = auth_mod.get_password(stu["gmail"])
    sender = SmtpSender(stu["gmail"], password)
    now = datetime.now(UTC)
    first = mime.build_message(from_name=stu["full_name"], from_addr=stu["gmail"], to_addr=stu["gmail"],
                               subject="[LabReach smoke test] initial", body="This is a LabReach smoke test.",
                               signature=stu["full_name"], now=now, attachment=REPO_ROOT / stu["resume_pdf"],
                               attachment_name=stu["resume_attachment_name"])
    sender.send(first.message)
    follow = mime.build_message(from_name=stu["full_name"], from_addr=stu["gmail"], to_addr=stu["gmail"],
                                subject=mime.followup_subject("[LabReach smoke test] initial"), body="Smoke test follow-up.",
                                signature=stu["full_name"], now=now, in_reply_to=first.message_id,
                                references=[first.message_id])
    sender.send(follow.message)
    import time
    time.sleep(10)
    with ImapMailbox(stu["gmail"], password) as box:
        a = box.find_in_sent(message_id=first.message_id, subject="[LabReach smoke test] initial", to=stu["gmail"])
        b = box.find_in_sent(message_id=follow.message_id, subject="Re: [LabReach smoke test] initial", to=stu["gmail"])
    console.print(f"initial in Sent: {a.found} (Message-ID kept: {a.message_id == first.message_id})")
    threaded = b.found and (b.in_reply_to or "") in (first.message_id, a.message_id or "")
    console.print(f"follow-up in Sent: {b.found}; threads to the initial: {threaded}")
    log_event(conn, "smoke_test", initial=a.found, followup=b.found, threaded=threaded,
              message_id_kept=a.message_id == first.message_id)
    if not (a.found and b.found and threaded):
        raise typer.Exit(1)


@programs_app.command("list")
def programs_list() -> None:
    conn = _conn()
    t = Table("name", "deadline", "status", "url")
    for r in conn.execute("SELECT * FROM programs ORDER BY deadline"):
        t.add_row(r["name"], r["deadline"] or "", r["status"] or "", r["url"] or "")
    console.print(t)


@programs_app.command("add")
def programs_add(name: str, url: str = "", deadline: str = "", status: str = "to-check") -> None:
    conn = _conn()
    conn.execute("INSERT INTO programs (name, url, deadline, status) VALUES (?,?,?,?)", (name, url, deadline, status))
    conn.commit()
    console.print(f"Added program: {name}")


if __name__ == "__main__":
    app()
