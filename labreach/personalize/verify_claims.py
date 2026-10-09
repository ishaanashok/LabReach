"""Gate 3: claim verification. Every statement about the recipient must trace to a stored source;
every statement about the student must trace to an approved profile fact. Code decides, not the model."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import DraftSlots, StudentProfile, VerifyResult

QUOTE_RE = re.compile(r"[\"“]([^\"”]{3,200})[\"”]")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
NUMBER_RE = re.compile(r"\$?\d[\d,\.]*[KkMm%]?")
# A sentence asserts something about the student if it says what he did/has/owns ("I built...", "my team", "our design").
STUDENT_ASSERT_RE = re.compile(
    r"\b(?:I(?:'ve| have)? (?:built|founded|co-founded|designed|led|won|created|developed|presented|worked|made|"
    r"run|ran|managed|started)|my|our|we)\b")
NAMED_TERM_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][A-Za-z0-9\-]*[A-Z0-9][A-Za-z0-9\-]*|[A-Z][a-z]+)\b")
QUESTION_STARTS = ("how", "whether", "why", "what", "if", "when", "where")
STOPWORDS = set("""a an the and or of to in on for with by from at as is are was were be been it its this that
these those their there which who whom what how into over under about than then so but not no can could would
should may might will your you i my me our we us they them he she his her also such more most one two each""".split())
MIN_CONFIDENCE = 0.7
MIN_SUPPORT = 0.6
MIN_SUPPORT_QUESTION = 0.0   # a question asserts nothing; named terms/numbers/years in it are still checked


@dataclass
class Source:
    id: int
    type: str            # paper | lab_news | project | profile
    title: str
    year: int | None
    url: str
    snippet: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.snippet}"


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-']*", _norm(text)) if w not in STOPWORDS and len(w) > 2}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def verify_draft(slots: DraftSlots, sources: dict[int, Source], profile: StudentProfile,
                 template: dict) -> VerifyResult:
    fail: list[str] = []
    if slots.confidence < MIN_CONFIDENCE:
        fail.append(f"confidence {slots.confidence:.2f} below {MIN_CONFIDENCE}")

    # structural choices must come from the locked pools
    if slots.task not in template["task_pool"]:
        fail.append(f"task '{slots.task}' not in task pool")
    for fid in slots.credential_fact_ids:
        if fid not in template["credential_pool"] or profile.usable(fid) is None:
            fail.append(f"credential fact {fid} not allowed or not confirmed")
    if len(set(slots.credential_fact_ids)) != len(slots.credential_fact_ids):
        fail.append("duplicate credential facts")
    for key, limit in (("p1", template["limits"]["p1_max_words"]), ("p2", template["limits"]["p2_max_words"])):
        n = len(getattr(slots, key).split())
        if n > limit:
            fail.append(f"{key} has {n} words; limit {limit}")

    # claims must reference real, approved evidence
    cited_sources: list[Source] = []
    for claim in slots.claims:
        if claim.source_id is not None:
            src = sources.get(claim.source_id)
            if src is None:
                fail.append(f"claim cites unknown source {claim.source_id}")
                continue
            cited_sources.append(src)
            claim_words = _words(QUOTE_RE.sub(" ", claim.text))   # a verbatim quote must not pad the score
            if claim_words:
                support = len(claim_words & _words(src.text)) / len(claim_words)
                needed = MIN_SUPPORT_QUESTION if _norm(claim.text).startswith(QUESTION_STARTS) else MIN_SUPPORT
                if support < needed:
                    fail.append(f"claim not supported by source {src.id} ({support:.0%} of its terms found): {claim.text[:60]}")
        else:
            fact = profile.usable(claim.profile_fact_id or "")
            if fact is None:
                fail.append(f"claim cites unknown or unconfirmed profile fact {claim.profile_fact_id}")
                continue
            claim_words = _words(claim.text)
            allowed = _words(fact.email_phrase + " " + fact.resume_line + " " + " ".join(fact.tags))
            if claim_words and len(claim_words & allowed) / len(claim_words) < 0.5:
                fail.append(f"claim about the student goes beyond fact {fact.id}: {claim.text[:60]}")

    text = f"{slots.p1} {slots.p2}"
    norm_text = _norm(text)

    # every sentence must be covered by a claim; first-person sentences need a profile fact
    for sentence in _sentences(text):
        ns = _norm(sentence)
        covering = [c for c in slots.claims if _norm(c.text) in ns or ns in _norm(c.text)]
        if not covering:
            fail.append(f"sentence has no supporting claim: {sentence[:70]}")
        elif STUDENT_ASSERT_RE.search(sentence) and not any(c.profile_fact_id for c in covering):
            fail.append(f"sentence about the student has no profile fact: {sentence[:70]}")
        elif not STUDENT_ASSERT_RE.search(sentence) and not any(c.source_id is not None for c in covering):
            fail.append(f"sentence about the recipient has no source: {sentence[:70]}")

    # verbatim checks: quoted titles/terms, years, numbers
    source_blob = _norm(" ".join(s.text for s in cited_sources))
    quotes = QUOTE_RE.findall(text)
    if not quotes:
        fail.append("p1/p2 must quote a verbatim title or term from a stored source")
    for q in quotes:
        if _norm(q) not in source_blob:
            fail.append(f"quoted text not found verbatim in cited sources: \"{q[:60]}\"")
    # named methods/proper nouns must come from the cited sources or the student's own approved facts
    student_blob = _norm(" ".join(f.email_phrase + " " + f.resume_line for f in profile.facts))
    unquoted = QUOTE_RE.sub(" ", text)
    for sent in _sentences(unquoted):
        for term in NAMED_TERM_RE.findall(" ".join(sent.split()[1:])):
            if term == "I" or _norm(term) in {"i'm", "i've", "i'd"}:
                continue
            if _norm(term) not in source_blob and _norm(term) not in student_blob:
                fail.append(f"named term '{term}' not found in cited sources or approved facts")
    fact_numbers = " ".join(n for f in profile.facts for n in f.numbers)
    for y in YEAR_RE.findall(text):
        if y not in source_blob and y not in {str(s.year) for s in cited_sources}:
            fail.append(f"year {y} not found in cited sources")
    for num in NUMBER_RE.findall(text):
        if YEAR_RE.fullmatch(num.strip(".,")):
            continue
        if _norm(num) not in source_blob and num not in fact_numbers:
            fail.append(f"number '{num}' appears in neither the cited sources nor the approved facts")

    # subject: the topic must come from a source, the year too
    topic = _norm(slots.subject_topic)
    if topic not in _norm(" ".join(s.text for s in sources.values())):
        fail.append(f"subject topic '{slots.subject_topic}' not found in stored sources")
    if slots.subject_year and str(slots.subject_year) not in {str(s.year) for s in sources.values()}:
        fail.append(f"subject year {slots.subject_year} matches no stored source")
    if norm_text == "":
        fail.append("empty personalization")
    return VerifyResult(ok=not fail, failures=fail)
