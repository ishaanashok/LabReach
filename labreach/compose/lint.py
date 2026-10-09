"""Email lint rules, applied to every generated email (not just the templates)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import LintIssue, LintReport

GENERIC_SUBJECTS = {
    "research opportunity", "inquiry", "internship request", "internship", "research inquiry",
    "research position", "summer research", "question", "hello", "request", "opportunity",
    "interested in your research", "research opportunities",
}
BANNED_PHRASES = [
    "i know you're busy", "i know you are busy", "i hope this email finds you", "i hope this finds you",
    "to whom it may concern", "i would be honored", "i would be so grateful", "i am begging", "please give me a chance",
    "any opportunity", "i will do anything", "dream of", "life-changing", "i have always been passionate",
    "my name is", "i am writing to", "i'm writing to",
]
EXAGGERATION = [
    "expert", "world-class", "world class", "groundbreaking", "ground-breaking", "fascinating", "amazing",
    "incredible", "revolutionary", "brilliant", "renowned", "esteemed", "impressive", "prodigy", "genius",
    "cutting-edge", "passionate", "unparalleled", "remarkable",
]
GENERIC_OPENERS = [
    r"\bi am very interested in your research\b", r"\bi'?m very interested in your research\b",
    r"\bi am (?:extremely |really )?interested in your (?:work|research)\b", r"\bi came across your\b",
    r"\bi hope this\b", r"\bmy passion for\b",
]
ASK_PATTERNS = [r"\bwould you\b", r"\bcould you\b", r"\bcan you\b", r"\bmay i\b", r"\bwould it be possible\b",
                r"\bi'?d like to ask\b", r"\bplease (?:let me know|consider)\b"]
URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|edu|org|io|net|dev|ai|us)\b(?:/\S*)?)",
                    re.IGNORECASE)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐✅]")
TITLE_NAME_RE = re.compile(r"\b(?:Professor|Prof\.|Dr\.)\s+([A-Z][A-Za-z'\-]+)")


@dataclass
class LintContext:
    kind: str = "initial"                       # initial | fu1 | fu2
    recipient_last: str = ""
    other_names: list[str] = field(default_factory=list)      # other people's names (other targets, lab members)
    subject_terms: list[str] = field(default_factory=list)    # at least one must appear in the subject
    link_allowlist: list[str] = field(default_factory=list)
    allow_links: bool = False
    attachments: list[tuple[str, int]] = field(default_factory=list)   # (filename, size_bytes)
    max_attachments: int = 2
    max_attachment_bytes: int = 2_000_000
    banned_claims: list[str] = field(default_factory=list)    # e.g. "published" unless the resume verifies it
    recent_personalization: list[str] = field(default_factory=list)   # last-30-days personalization texts
    personalization: str = ""                    # this email's model-written text (overlap is measured on it)
    ngram_threshold: float = 0.40
    ngram_n: int = 4


def count_words(text: str) -> int:
    return sum(1 for token in text.split() if re.search(r"\w", token))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def ngram_overlap(text: str, other: str, n: int = 4) -> float:
    """Share of `text`'s n-grams that also occur in `other`."""
    def grams(t: str) -> set[tuple[str, ...]]:
        words = re.findall(r"[a-z0-9']+", t.lower())
        return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}
    a, b = grams(text), grams(other)
    return len(a & b) / len(a) if a else 0.0


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE) is not None


def lint_email(subject: str | None, body: str, ctx: LintContext) -> LintReport:
    """`body` excludes the signature. Returns every issue found (no short-circuit) so reviewers see them all."""
    issues: list[LintIssue] = []
    add = lambda rule, detail: issues.append(LintIssue(rule=rule, detail=detail))  # noqa: E731
    words = count_words(body)
    lowered = body.lower()

    # length
    if ctx.kind == "initial":
        if words > 200:
            add("hard_max_words", f"{words} words exceeds the hard max of 200")
        elif not 120 <= words <= 180:
            add("word_count", f"{words} words; initial emails must be 120-180")
    else:
        # strip the greeting line first, then count sentences
        content = re.sub(r"^(?:Dear|Hi)[^\n]*,\s*", "", body.strip())
        sentences = _sentences(content)
        lo, hi = (2, 4) if ctx.kind == "fu1" else (1, 2)
        if not lo <= len(sentences) <= hi:
            add("followup_length", f"{len(sentences)} sentences; {ctx.kind} must be {lo}-{hi}")

    # subject
    if subject is not None:
        s = subject.strip()
        if ctx.kind == "initial" and re.match(r"^(re|fwd?):", s, re.IGNORECASE):
            add("subject_prefix", "initial subject must not start with Re:/Fwd:")
        if s.lower().strip(" ?.!") in GENERIC_SUBJECTS:
            add("subject_generic", f"'{s}' is a generic subject")
        if len(s) > 100:
            add("subject_length", f"{len(s)} characters; keep subjects to 100 or fewer")
        if ctx.kind == "initial":
            if not ctx.subject_terms or not any(t and t.lower() in s.lower() for t in ctx.subject_terms):
                add("subject_specificity", "subject must name a specific topic from the recipient's own work")

    # tone
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            add("banned_phrase", phrase)
    for word in EXAGGERATION:
        if _contains_word(body, word):
            add("exaggeration", word)
    for word in ctx.banned_claims:
        if _contains_word(body, word):
            add("unsupported_claim_word", f"'{word}' is not supported by the resume")
    for pattern in GENERIC_OPENERS:
        if re.search(pattern, lowered):
            add("generic_opener", pattern)
    if EMOJI_RE.search(body):
        add("emoji", "emoji are not allowed")
    if body.count("!") > 1:
        add("exclamations", f"{body.count('!')} exclamation marks; at most 1")
    if re.search(r"\b[A-Z]{3,}\b(?:\s+\b[A-Z]{3,}\b){2,}", body):
        add("all_caps", "run of ALL-CAPS words")

    # exactly one ask
    asks = sum(len(re.findall(p, lowered)) for p in ASK_PATTERNS)
    if asks > 1 or body.count("?") > 1:
        add("multiple_asks", f"{asks} ask phrases / {body.count('?')} question marks; exactly one ask allowed")
    if ctx.kind == "initial" and asks == 0:
        add("no_ask", "initial email has no ask")

    # other people's names
    for m in TITLE_NAME_RE.finditer(body):
        if m.group(1).lower() != ctx.recipient_last.lower():
            add("other_person", f"mentions '{m.group(0)}'")
    for name in ctx.other_names:
        if name and _contains_word(body, name):
            add("other_person", f"mentions '{name}'")

    # links
    for m in URL_RE.finditer(body):
        url = m.group(0).rstrip(".,);")
        if not ctx.allow_links:
            add("link_not_allowed", f"'{url}' (initial emails carry no links; the resume has them)")
        elif not any(url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
                     .startswith(a.lower()) for a in ctx.link_allowlist):
            add("link_off_allowlist", url)

    # attachments
    if len(ctx.attachments) > ctx.max_attachments:
        add("attachments_count", f"{len(ctx.attachments)} attachments; max {ctx.max_attachments}")
    if sum(size for _, size in ctx.attachments) > ctx.max_attachment_bytes:
        add("attachments_size", "attachments exceed the size limit")
    if ctx.kind != "initial" and ctx.attachments:
        add("attachments_followup", "follow-ups carry no attachments")

    # copy-paste overlap, measured on the model-written text only (the locked skeleton is shared by design)
    if ctx.personalization:
        for other in ctx.recent_personalization:
            overlap = ngram_overlap(ctx.personalization, other, ctx.ngram_n)
            if overlap > ctx.ngram_threshold:
                add("ngram_overlap", f"{overlap:.0%} {ctx.ngram_n}-gram overlap with a recent email")
                break

    return LintReport(ok=not issues, word_count=words, issues=issues)
