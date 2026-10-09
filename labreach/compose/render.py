"""Render emails from LOCKED skeletons. Only declared slots can change; everything else is fixed text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..config import student_age
from ..models import DraftSlots, StudentProfile
from .templates import verify_lock

SLOT_RE = re.compile(r"\{\{(\w+)\}\}")


class RenderError(ValueError):
    pass


@dataclass
class Recipient:
    first: str
    last: str
    role: str                  # professor | postdoc | staff | grad_student | lab_manager
    university: str
    campus: str = ""
    local: bool = False        # Bay Area (commutable)
    informal: bool = False     # grad student whose page is informal


@dataclass
class Rendered:
    subject: str | None
    body: str                  # no signature
    signature: str
    personalization: str       # the only model-written text (p1 + p2)
    slot_values: dict[str, str]
    template_id: str

    @property
    def full_text(self) -> str:
        return f"{self.body}\n\n{self.signature}"


def fill(skeleton: str, values: dict[str, str], allowed: set[str]) -> str:
    used = set(SLOT_RE.findall(skeleton))
    if not used <= allowed:
        raise RenderError(f"skeleton uses undeclared slots: {sorted(used - allowed)}")
    missing = used - set(values)
    if missing:
        raise RenderError(f"missing slot values: {sorted(missing)}")
    return SLOT_RE.sub(lambda m: values[m.group(1)], skeleton).strip()


def signature(profile: StudentProfile) -> str:
    return f"{profile.name}\n{profile.grade.capitalize()}, {profile.school} ({profile.city}, CA)\n{profile.gmail}"


def greeting(template: dict, r: Recipient) -> str:
    if r.role == "professor":
        key = "professor"
    elif r.role in ("postdoc", "staff"):
        key = "doctor"
    elif r.role == "grad_student":
        key = "informal_grad" if r.informal else "formal_grad"
    else:
        raise RenderError(f"role '{r.role}' needs a human-written note (routed to needs_human)")
    return template["fixed_options"]["greeting"][key].format(first=r.first, last=r.last)


def _day(d: date) -> str:
    return f"{d:%B} {d.day}"


def logistics(template: dict, r: Recipient, settings: dict, today: date) -> str:
    stu = settings["student"]
    if "born" not in stu:
        raise RenderError("student.born is missing; set it in data/private/contact.yaml")
    key = "logistics_local" if r.local else "logistics_remote"
    return template["fixed_options"][key].format(
        hours=stu["school_year_hours_per_week"],
        start=_day(date.fromisoformat(stu["summer_start"])),
        end=_day(date.fromisoformat(stu["summer_end"])),
        age=student_age(stu["born"], today),
        campus=r.campus,
    )


def _check_choices(template: dict, slots: DraftSlots, profile: StudentProfile) -> None:
    if slots.task not in template["task_pool"]:
        raise RenderError(f"task '{slots.task}' is not in this template's task pool")
    for fid in slots.credential_fact_ids:
        if fid not in template["credential_pool"]:
            raise RenderError(f"fact {fid} is not allowed in template {template['id']}")
        if profile.usable(fid) is None:
            raise RenderError(f"fact {fid} is unknown or not yet confirmed")


def render_subject(template: dict, slots: DraftSlots) -> str:
    formula = template["subject_formulas"][slots.subject_formula]
    if "{year}" in formula and not slots.subject_year:
        raise RenderError("subject formula S1 needs a year")
    return formula.format(year=slots.subject_year, topic=slots.subject_topic, task=slots.task)


def render_initial(template: dict, slots: DraftSlots, r: Recipient, profile: StudentProfile, settings: dict,
                   today: date, *, enforce_lock: bool = True) -> Rendered:
    if enforce_lock:
        verify_lock(template)
    _check_choices(template, slots, profile)
    credentials = " ".join(profile.usable(fid).email_phrase for fid in slots.credential_fact_ids)
    values = {
        "greeting": greeting(template, r),
        "student_name": profile.name, "grade": profile.grade, "school": profile.school,
        "school_city": profile.city,
        "p1": slots.p1.strip(), "p2": slots.p2.strip(),
        "credentials": credentials,
        "ask": template["fixed_options"]["ask"]["pi" if r.role == "professor" else "junior"].format(task=slots.task),
        "logistics": logistics(template, r, settings, today),
    }
    allowed = set(values)
    body = fill(template["skeleton"], values, allowed)
    return Rendered(subject=render_subject(template, slots), body=body, signature=signature(profile),
                    personalization=f"{values['p1']} {values['p2']}", slot_values=values,
                    template_id=template["id"])


def _skeleton_regex(skeleton: str) -> re.Pattern[str]:
    parts = SLOT_RE.split(skeleton.strip())
    pattern = "".join(re.escape(t) if i % 2 == 0 else f"(?P<{t}>.+?)" for i, t in enumerate(parts))
    return re.compile(f"^{pattern}$", re.DOTALL)


def _option_regex(option: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{\w+\\\}", ".+?", re.escape(option)) + "$", re.DOTALL)


def extract_slots(template: dict, body: str) -> dict[str, str] | None:
    """Recover slot values from a stored body; None if the fixed skeleton text was altered."""
    m = _skeleton_regex(template["skeleton"]).match(body.strip())
    return m.groupdict() if m else None


def conforms(template: dict, body: str, profile: StudentProfile | None = None,
             context: tuple[Recipient, dict, date] | None = None) -> tuple[bool, list[str]]:
    """Gate 1. The body must match the locked skeleton, and every fixed-option slot must be one of the
    approved options (greeting, ask, logistics) or approved fact phrases (credentials). Only p1/p2 are free."""
    slots = extract_slots(template, body)
    if slots is None:
        return False, ["fixed skeleton text was altered"]
    problems: list[str] = []
    options = template.get("fixed_options", {})
    for slot, key in (("greeting", "greeting"), ("ask", "ask")):
        if slot in slots and not any(_option_regex(o).match(slots[slot]) for o in options[key].values()):
            problems.append(f"{slot} is not an approved option")
    if "logistics" in slots and not any(
            _option_regex(options[k]).match(slots["logistics"]) for k in ("logistics_local", "logistics_remote")):
        problems.append("logistics is not an approved option")
    if "ask" in slots:
        task = next((t for t in template.get("task_pool", []) if t in slots["ask"]), None)
        if task is None:
            problems.append("ask names a task outside the task pool")
    if "credentials" in slots and profile is not None:
        phrases = [profile.usable(f).email_phrase for f in template["credential_pool"] if profile.usable(f)]
        remaining = slots["credentials"]
        used = [p for p in phrases if p in remaining]
        for p in used:
            remaining = remaining.replace(p, "")
        if not used or remaining.strip():
            problems.append("credentials contain text that is not an approved fact phrase")
    if context is not None and not problems and "logistics" in slots:
        # strict: recompute every fixed slot from trusted inputs (recipient, settings, today) and compare exactly
        recipient, settings, today = context
        task = next(t for t in template["task_pool"] if t in slots["ask"])
        expected = dict(slots)
        expected.update(
            greeting=greeting(template, recipient),
            ask=options["ask"]["pi" if recipient.role == "professor" else "junior"].format(task=task),
            logistics=logistics(template, recipient, settings, today))
        if fill(template["skeleton"], expected, set(expected)) != body.strip():
            problems.append("fixed slots differ from values recomputed from recipient, settings and today")
    return not problems, problems
