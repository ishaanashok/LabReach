"""The eight gates. An email is sent only if every one passes; anything else goes to needs_human with reasons.
Gates re-derive everything from stored data at send time; nothing is trusted from draft time except the text itself."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import killswitch, scheduler
from .compose import lint as lint_mod
from .compose import render as render_mod
from .compose import templates as tpl
from .discovery.email_extract import address_in_snippet, domain_allowed
from .models import DraftSlots, StudentProfile
from .personalize.generate import lint_context_for, recipient_from_row
from .personalize.sources import load_sources
from .personalize.verify_claims import verify_draft

CONTENT_GATES = ("template", "lint", "claims", "provenance", "eligibility")
TIMING_GATES = ("limits", "window", "killswitch")


@dataclass
class GateResults:
    results: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results[name] = (ok, detail)

    def passed(self, names: tuple[str, ...] | None = None) -> bool:
        return all(ok for n, (ok, _) in self.results.items() if names is None or n in names)

    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, (ok, d) in self.results.items() if not ok]

    def as_json(self) -> str:
        return json.dumps({n: {"ok": ok, "detail": d} for n, (ok, d) in self.results.items()})


def is_dnc(conn: sqlite3.Connection, email: str) -> bool:
    email = (email or "").lower()
    domain = email.rsplit("@", 1)[-1]
    return conn.execute("SELECT 1 FROM do_not_contact WHERE email = ? OR (domain IS NOT NULL AND domain = ?)",
                        (email, domain)).fetchone() is not None


def _gate_template(g, conn, email, target, template, profile, settings, now, enforce_lock):
    try:
        if enforce_lock:
            tpl.verify_lock(template)
    except tpl.TemplateNotLocked as exc:
        g.add("template", False, str(exc))
        return
    recipient = recipient_from_row(target)
    local_today = now.astimezone(ZoneInfo(settings["timezone"])).date()
    if email["kind"] == "initial":
        ok, problems = render_mod.conforms(template, email["body"], profile, (recipient, settings, local_today))
    else:
        parts = render_mod.extract_slots(template, email["body"])
        initial = conn.execute("SELECT slots_json FROM emails WHERE target_id = ? AND kind = 'initial'", (target["id"],)).fetchone()
        task = DraftSlots.model_validate_json(initial["slots_json"]).task if initial and initial["slots_json"] else None
        problems = []
        if parts is None:
            problems.append("fixed skeleton text was altered")
        else:
            if parts["greeting"] != render_mod.greeting(template, recipient):
                problems.append("greeting differs from the approved option")
            if parts["task"] != task:
                problems.append("task differs from the one named in the initial email")
            if "new_value" in parts and not any(f.followup_phrase == parts["new_value"] for f in profile.facts if profile.usable(f.id)):
                problems.append("new value is not an approved follow-up phrase")
        ok = not problems
    g.add("template", ok, "; ".join(problems) if not ok else "matches locked skeleton")


def _gate_lint(g, conn, email, target, profile, settings):
    slots = DraftSlots.model_validate_json(email["slots_json"]) if email["slots_json"] else None
    ctx = lint_context_for(conn, target, settings, profile, slots, email["personalization"] or "", email["kind"], email["id"])
    report = lint_mod.lint_email(email["subject"] if email["kind"] == "initial" else None, email["body"], ctx)
    g.add("lint", report.ok, "; ".join(f"{i.rule}: {i.detail}" for i in report.issues) or "clean")


def _gate_claims(g, conn, email, target, template, profile):
    if email["kind"] != "initial":
        g.add("claims", True, "follow-up uses approved fact phrases only")
        return
    if not email["slots_json"]:
        g.add("claims", False, "no stored slots")
        return
    slots = DraftSlots.model_validate_json(email["slots_json"])
    sources = {s.id: s for s in load_sources(conn, target["id"])}
    result = verify_draft(slots, sources, profile, template, target["role"])
    failures = list(result.failures)
    # what was verified must be exactly what will be sent
    parts = render_mod.extract_slots(template, email["body"] or "")
    if parts is None or parts.get("p1") != slots.p1.strip() or parts.get("p2") != slots.p2.strip():
        failures.append("email text differs from the slots that were claim-verified")
    try:
        if render_mod.render_subject(template, slots) != email["subject"]:
            failures.append("subject differs from the verified subject slots")
    except render_mod.RenderError as exc:
        failures.append(str(exc))
    g.add("claims", not failures, "; ".join(failures) or "all claims trace to stored sources and approved facts")


def _gate_provenance(g, target, settings):
    problems = []
    if not target["email"] or not target["email_source_url"] or not target["email_source_snippet"]:
        problems.append("missing address, source URL or snippet")
    else:
        if not domain_allowed(target["email"], settings["email_domain_allowlist"]):
            problems.append("address domain not allowlisted")
        src_host = target["email_source_url"].split("/")[2].lower() if "//" in target["email_source_url"] else ""
        if not any(src_host == d or src_host.endswith("." + d) for d in settings["email_domain_allowlist"]):
            problems.append("source page not on an allowlisted domain")
        if not target["email_source_url"].startswith("https://"):
            problems.append("source URL is not https")
        if not address_in_snippet(target["email"], target["email_source_snippet"]):
            problems.append("address does not literally appear in the stored page snippet")
    g.add("provenance", not problems, "; ".join(problems) or "address found verbatim on an official page")


def _gate_eligibility(g, conn, email, target, now, settings):
    problems = []
    if is_dnc(conn, target["email"]):
        problems.append("on do-not-contact")
    dup = conn.execute(
        "SELECT t.id FROM targets t JOIN emails e ON e.target_id = t.id WHERE t.id != ? AND e.kind = 'initial' "
        "AND e.sent_at IS NOT NULL AND (t.email = ? OR (t.name_norm = ? AND t.university_norm = ?))",
        (target["id"], target["email"], target["name_norm"], target["university_norm"])).fetchone()
    if dup:
        problems.append("already contacted (same address, or same name and university)")
    if target["lab_key"]:
        rival = conn.execute(
            "SELECT t.id FROM targets t JOIN emails e ON e.target_id = t.id WHERE t.id != ? AND t.lab_key = ? "
            "AND e.kind = 'initial' AND e.sent_at IS NOT NULL AND t.status IN ('active', 'replied')",
            (target["id"], target["lab_key"])).fetchone()
        if rival:
            problems.append("another contact in the same lab is active")
    if email["kind"] == "initial":
        if target["status"] not in ("drafted", "eligible", "active"):
            problems.append(f"target status '{target['status']}' does not allow sending")
        if email["sent_at"]:
            problems.append("initial email was already sent")
    else:
        prev = "initial" if email["kind"] == "fu1" else "fu1"
        prev_row = conn.execute("SELECT sent_at, replied_at, bounced_at FROM emails WHERE target_id = ? AND kind = ?",
                                (target["id"], prev)).fetchone()
        if not prev_row or not prev_row["sent_at"]:
            problems.append(f"{prev} has not been sent")
        elif prev_row["replied_at"] or prev_row["bounced_at"]:
            problems.append("recipient replied or address bounced")
        if target["status"] != "active":
            problems.append(f"target status '{target['status']}' stops follow-ups")
        replied = conn.execute("SELECT 1 FROM emails WHERE target_id = ? AND (replied_at IS NOT NULL OR bounced_at IS NOT NULL)",
                               (target["id"],)).fetchone()
        if replied:
            problems.append("a reply or bounce is recorded for this person")
        count = conn.execute("SELECT COUNT(*) FROM emails WHERE target_id = ? AND sent_at IS NOT NULL", (target["id"],)).fetchone()[0]
        if count >= 3 or (conn.execute("SELECT 1 FROM emails WHERE target_id=? AND kind=? AND sent_at IS NOT NULL",
                                       (target["id"], email["kind"])).fetchone()):
            problems.append("sequence limit (1 initial + 2 follow-ups) reached")
        if prev_row and prev_row["sent_at"] and not scheduler.followup_due(
                email["kind"], datetime.fromisoformat(prev_row["sent_at"]).replace(tzinfo=UTC), now,
                target["timezone"] or "America/Los_Angeles", settings):
            problems.append("follow-up is not due yet")
    g.add("eligibility", not problems, "; ".join(problems) or "clear to contact")



def run_gates(conn: sqlite3.Connection, email: sqlite3.Row, settings: dict, profile: StudentProfile, templates: dict[str, dict],
              now: datetime, stop_file: Path, enforce_lock: bool = True) -> GateResults:
    """All eight gates for one stored email at `now` (UTC)."""
    g = GateResults()
    target = conn.execute("SELECT * FROM targets WHERE id = ?", (email["target_id"],)).fetchone()
    template_id = email["template_version"].split("@")[0] if email["template_version"] else None
    template = templates.get(template_id or "")
    if template is None or not email["body"]:
        g.add("template", False, "no template or body stored")
        for name in CONTENT_GATES[1:]:
            g.add(name, False, "skipped: no template or body")
    else:
        _gate_template(g, conn, email, target, template, profile, settings, now, enforce_lock)
        _gate_lint(g, conn, email, target, profile, settings)
        _gate_claims(g, conn, email, target, template, profile)
        _gate_provenance(g, target, settings)
        _gate_eligibility(g, conn, email, target, now, settings)
    if email["kind"] == "initial":
        problems = scheduler.cap_problems(conn, settings, now, target["university_norm"], target["department"])
        g.add("limits", not problems, "; ".join(problems) or "within daily, university and department caps")
    else:
        g.add("limits", True, "follow-ups are not counted against new-contact caps")
    tz = target["timezone"] or settings["timezone"]
    in_window = scheduler.in_send_window(now, tz, settings, target["university"])
    g.add("window", in_window, "inside recipient-local send window" if in_window else
          f"outside the Tue-Thu 8-10am window in {tz} (or holiday/blackout)")
    engaged = killswitch.is_engaged(conn, stop_file)
    g.add("killswitch", not engaged, (killswitch.reason(conn, stop_file) or "engaged") if engaged else "off")
    return g
