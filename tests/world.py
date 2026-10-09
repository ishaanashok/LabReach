"""Builders for pipeline tests: ready-to-draft targets with verifiable sources, and a RunContext with fakes."""

from datetime import UTC, datetime

from labreach.mail_imap import ImapMailbox
from labreach.mail_smtp import SmtpSender
from labreach.models import Claim, DraftSlots
from labreach.personalize.verify_claims import Source
from labreach.run import RunContext

LASTS = ["Abernathy", "Brightwell", "Castellano", "Dellinger", "Eastwood", "Fairbanks", "Galloway", "Hargrove", "Ingersoll",
         "Jarrett", "Kingsley", "Lockhart"]
FIRSTS = ["Pat", "Quinn", "Robin", "Sasha", "Taylor", "Uma", "Val", "Wren", "Xi", "Yael", "Zane", "Alex"]
A = ["Adaptive", "Compliant", "Hydraulic", "Magnetic", "Piezoelectric", "Biomimetic", "Origami", "Elastomeric", "Cable-Driven",
     "Pneumatic", "Modular", "Thermal"]
B = ["Socket Interfaces", "Joint Actuators", "Gripper Mechanisms", "Sensor Arrays", "Gait Controllers", "Exoskeleton Frames",
     "Wrist Mechanisms", "Spine Modules", "Valve Assemblies", "Bearing Designs", "Linkage Systems", "Hinge Structures"]
C = ["low-cost prosthetic limbs", "wearable rehabilitation", "surgical tool handling", "soft robot locomotion",
     "underwater inspection", "wind turbine blades", "portable diagnostics", "prosthetic hands", "warehouse picking",
     "orthopedic braces", "pipe crawling", "tremor suppression"]
D = ["fatigue", "alignment", "calibration drift", "payload limits", "wear", "noise", "latency", "tolerance stack-ups",
     "thermal load", "assembly time", "slip", "backlash"]
UNIS = [("Stanford University", "Mechanical Engineering"), ("University of California, Berkeley", "Electrical Engineering"),
        ("San Jose State University", "Computer Science"), ("Santa Clara University", "Bioengineering"),
        ("UCSF", "Bioengineering Research"), ("University of California, Santa Cruz", "Robotics"),
        ("University of California, Davis", "Materials")]


def make_ready_target(conn, i, *, status="eligible", role="professor", variant="initial_a", lab_key=None, tz="America/Los_Angeles",
                      local=1, univ=None):
    uni, dept = univ or UNIS[i % len(UNIS)]
    first, last = FIRSTS[i], LASTS[i]
    email = f"{last.lower()}@stanford.edu"
    page = f"https://me.stanford.edu/people/{last.lower()}"
    title, topic = f"{A[i]} {B[i]}", C[i]
    snippet = (f"We present {A[i].lower()} {B[i].lower()} that improve {topic} while reducing {D[i]} in field deployments "
               f"and report results across several benchmark tasks.")
    conn.execute(
        "INSERT INTO targets (id, name, name_norm, first_name, last_name, role, university, university_norm, department, lab, "
        "lab_key, email, email_source_url, email_source_snippet, status, timezone, local, campus, variant, fit_score, informal) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (i + 1, f"{first} {last}", f"{first}{last}".lower(), first, last, role, uni, uni.lower(), dept, f"{last} Lab",
         lab_key or f"lab-{i}", email, page, f"Contact: {email}", status, tz, local, "Stanford" if local else None, variant, 8, 0))
    cur = conn.execute("INSERT INTO sources (target_id, type, title, year, url, snippet) VALUES (?,?,?,?,?,?)",
                       (i + 1, "paper", title, 2025, f"{page}/paper", snippet))
    conn.commit()
    source = Source(cur.lastrowid, "paper", title, 2025, f"{page}/paper", snippet)
    return i + 1, source, slots_for(i, source)


def slots_for(i, source, task="CAD or fixture design", credentials=("F_RESTEP", "F_FTC_CAD")):
    p1 = f'I read your 2025 paper "{A[i]} {B[i]}" on {B[i].lower()} for {C[i]}.'
    p2 = (f"Because our ReStep design is also a low-cost adjustable limb, I wondered how your {A[i].lower()} approach "
          f"handles {D[i]}.")
    return DraftSlots(
        subject_formula="S2", subject_topic=C[i], subject_year=None, p1=p1, p2=p2, credential_fact_ids=list(credentials),
        task=task, confidence=0.9,
        claims=[Claim(text=f'your 2025 paper "{A[i]} {B[i]}" on {B[i].lower()} for {C[i]}', source_id=source.id),
                Claim(text="our ReStep design is also a low-cost adjustable limb", profile_fact_id="F_RESTEP"),
                Claim(text=f"how your {A[i].lower()} approach handles {D[i]}", source_id=source.id)])


def asker_for(slots_by_source: dict):
    """A fake `ask`: finds the [source_id=N] in the prompt and returns the prepared slots."""
    import re

    def ask(prompt, schema, tools=None, purpose=None):
        sid = int(re.search(r"\[source_id=(\d+)\]", prompt).group(1))
        return slots_by_source[sid]
    return ask


class Clock:
    def __init__(self, when):
        self.when = when

    def __call__(self):
        return self.when

    def set(self, *args):
        self.when = datetime(*args, tzinfo=UTC)


def make_ctx(conn, settings, profile, templates, tmp_path, *, clock, smtp=None, imap=None, ask=None, resume=True, **kw):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    settings = {**settings, "limits": {**settings["limits"], "ngram_overlap_threshold": 1.0}}
    sender = SmtpSender("ishaan.ashok123@gmail.com", "pw", factory=smtp) if smtp is not None else None
    factory = (lambda: ImapMailbox("ishaan.ashok123@gmail.com", "pw", factory=imap)) if imap is not None else None
    return RunContext(conn=conn, settings=settings, profile=profile, templates=templates, stop_file=tmp_path / "STOP",
                      out_dir=tmp_path / "out", mailbox_factory=factory, sender=sender, now=clock, sleep=lambda s: None,
                      resume_pdf=pdf if resume else None, enforce_lock=False, ask_override=ask, **kw)
