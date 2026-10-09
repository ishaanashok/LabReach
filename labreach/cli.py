"""LabReach command line. Dry-run is the default; nothing here contacts anyone yet."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import budget as budget_mod
from . import killswitch
from .claude_cli import api_key_present, missing_flags
from .compose import lint as lint_mod
from .compose import render as render_mod
from .compose import templates as tpl
from .config import REPO_ROOT, data_dir, db_path, is_live, load_settings, stop_file
from .db import connect, log_event
from .logging_setup import setup_logging
from .personalize.samples import SAMPLES
from .profile import approve_profile, is_approved, load_profile

app = typer.Typer(help="LabReach Auto: local outreach agent (dry-run unless LABREACH_MODE=live).", no_args_is_help=True)
profile_app = typer.Typer(help="Student profile facts")
templates_app = typer.Typer(help="Email templates")
dnc_app = typer.Typer(help="Do-not-contact list")
app.add_typer(profile_app, name="profile")
app.add_typer(templates_app, name="templates")
app.add_typer(dnc_app, name="dnc")
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
    for tid in ("followup_1", "followup_2"):
        template = templates[tid]
        sample = SAMPLES["initial_a"]
        r = render_mod.render_followup(template, sample["recipient"], profile, task="CAD or fixture design",
                                       value_fact_id="F_YBVC", enforce_lock=False)
        report = lint_mod.lint_email(None, r.body, lint_mod.LintContext(kind=template["kind"],
                                     recipient_last=sample["recipient"].last, banned_claims=profile.banned_claims))
        out += [f"===== {tid}: {template['description']} =====", r.body, "", r.signature, "",
                f"[lint {'PASS' if report.ok else 'FAIL: ' + '; '.join(i.rule for i in report.issues)}]\n"]
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


if __name__ == "__main__":
    app()
