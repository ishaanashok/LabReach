import os
import stat
import sys

import pytest
from typer.testing import CliRunner

from labreach.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LABREACH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LABREACH_MODE", raising=False)
    monkeypatch.setenv("LABREACH_CLAUDE_BIN", "/nonexistent/claude")    # tests must never reach a real model
    monkeypatch.chdir(tmp_path)


def run(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def test_init_status_and_profile_gate():
    assert run("init").exit_code == 0
    out = run("status").output
    assert "dry-run" in out and "profile approved" in out
    shown = run("profile", "show").output
    assert "F_RESTEP" in out + shown and "F_BIOWRAP" in shown
    result = run("run")
    assert result.exit_code == 1 and "not approved" in result.output            # no run before the facts are approved
    approved = run("profile", "approve").output
    assert "Profile approved" in approved and "none" in approved          # nothing is waiting for confirmation


def test_dry_run_on_empty_queue_is_a_clean_no_op():
    run("init")
    run("profile", "approve")
    result = run("run")
    assert result.exit_code == 0 and "mode: dry_run" in result.output and "sent 0" in result.output
    assert "nothing was guessed" in result.output          # discovery could not reach claude: stopped cleanly


def test_stop_resume_and_dnc_and_reports():
    run("init")
    assert "engaged" in run("stop", "testing").output
    assert "testing" in run("status").output
    run("profile", "approve")
    assert run("run").exit_code == 2                                          # engaged: stops, tells you why
    assert "Kill switch cleared" in run("resume").output
    assert "Added" in run("dnc", "add", "someone@stanford.edu", "--reason", "asked").output
    assert run("dnc", "add").exit_code != 0
    assert run("report").exit_code == 0 and run("needs-human").exit_code == 0 and run("budget").exit_code == 0
    assert "Claude calls today: 0/60" in run("budget").output


def test_live_commands_refuse_without_live_mode():
    run("init")
    assert run("go-live").exit_code == 1
    assert run("approve-pilot").exit_code == 1
    assert run("smoke-test").exit_code == 1


def test_go_live_needs_the_typed_confirmation(monkeypatch):
    monkeypatch.setenv("LABREACH_MODE", "live")
    run("init")
    assert run("go-live", input="yes\n").exit_code == 1
    assert run("go-live", input="I CONFIRM\n").exit_code == 0


def test_run_in_live_mode_requires_locked_templates(monkeypatch):
    monkeypatch.setenv("LABREACH_MODE", "live")
    run("init")
    run("profile", "approve")
    result = run("run")
    assert result.exit_code == 1 and "not locked" in result.output


def test_doctor_checks_claude_flags(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\nprint('--print --output-format --tools --allowedTools --json-schema "
                    "--no-session-persistence --setting-sources --disable-slash-commands')\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LABREACH_CLAUDE_BIN", str(fake))
    assert run("doctor").exit_code == 0
    fake.write_text(f"#!{sys.executable}\nprint('--print only')\n")
    result = run("doctor")
    assert result.exit_code == 1 and "lacks flags" in result.output
    monkeypatch.setenv("LABREACH_CLAUDE_BIN", "/nonexistent/claude")
    assert run("doctor").exit_code == 1


def test_api_key_warning(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert "stripped" in run("budget").output
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"      # we strip it from subprocesses only


def test_followups_lists_who_is_due_and_sends_nothing(tmp_path):
    import sqlite3
    run("init")
    db = sqlite3.connect(tmp_path / "data" / "labreach.db")
    db.execute("INSERT INTO targets (id, name, name_norm, university, university_norm, email, status, timezone) VALUES "
               "(1,'Alex Rivera','alexrivera','Stanford University','stanford university','rivera@stanford.edu','active','America/Los_Angeles'),"
               "(2,'Quinn Brightwell','quinnbrightwell','MIT','mit','b@mit.edu','replied','America/New_York')")
    db.execute("INSERT INTO emails (target_id, kind, state, subject, sent_at, message_id) VALUES (1,'initial','sent','Question about X','2026-01-13T16:00:00','<a@gmail.com>')")
    db.execute("INSERT INTO emails (target_id, kind, state, subject, sent_at, message_id, replied_at) VALUES (2,'initial','sent','Hi','2026-01-13T16:00:00','<b@gmail.com>','2026-01-15T10:00:00')")
    db.commit()
    out = runner.invoke(app, ["followups"], env={"COLUMNS": "220"}).output
    assert "Rivera" in out and "DUE" in out and "Brightwell" not in out and "never sends follow-ups" in out
