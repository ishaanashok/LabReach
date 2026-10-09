"""Read a professor's official profile/lab page: research areas, openness signals, hard exclusions (with quotes),
lab members, and the fit score. Exclusions are quoted verbatim with the URL so a human can audit them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .email_extract import page_text

NEGATIVE = [
    r"\b(?:do(?:es)? not|don'?t|not|no longer|cannot|can'?t|unable to)\b.{0,40}\b(?:accept|take|consider|respond to|"
    r"answer|host|hire|entertain)\b.{0,40}\b(?:unsolicited|inquir|high[- ]school|summer students?|pre-?college|"
    r"minors?|interns?)",
    r"\bunsolicited\b.{0,60}\b(?:not|cannot|can'?t|will not|won'?t|ignored|discard)",
    r"\bno\b.{0,20}\b(?:high[- ]school|summer|pre-?college|minors?)\b.{0,15}\b(?:students?|interns?|applicants?)",
    r"\bnot (?:currently )?accepting\b.{0,40}\b(?:inquir|applications|emails|students)",
    r"\b(?:must|need to|have to) be (?:at least )?(?:18|eighteen)\b", r"\b18 (?:years )?(?:of age )?(?:or older|and (?:over|older))\b",
    r"\bdo not (?:contact|email) (?:mentors|faculty|professors|us) directly\b", r"\bdirect (?:contact|outreach) (?:with|to) "
    r"(?:mentors|faculty)\b.{0,30}\b(?:not|discouraged)",
    r"\bscience internship program\b", r"\bUCSC SIP\b",
]
POSITIVE_HS = [r"\bhigh[- ]school (?:students?|interns?|researchers?|volunteers?)\b", r"\bpre-?college\b",
               r"\bsummer (?:research )?(?:students?|interns?|program)\b", r"\bundergraduate and high[- ]school\b",
               r"\bK-?12\b", r"\bsteam outreach\b"]
OUTREACH = [r"\boutreach\b", r"\bbroadening participation\b", r"\bdiversity and (?:inclusion|outreach)\b",
            r"\bpre-?college\b", r"\bk-?12\b"]
PRIOR_INTERNS = [r"\b(?:past|former|previous|alumni)\b.{0,40}\b(?:high[- ]school|summer)\b.{0,15}\b(?:students?|interns?)",
                 r"\bhigh[- ]school (?:interns?|students?)\b.{0,40}\b(?:20\d\d|worked|joined|mentored|alumni)",
                 r"\bhigh[- ]school (?:interns?|students?|alumni)\s*(?::|-)\s*\w"]
LAB_MANAGER = re.compile(r"\blab(?:oratory)? manager\b.{0,80}\b(?:join|prospective|minor|volunteer|visit)|"
                         r"\b(?:join|prospective|minor|volunteer|visit)\b.{0,80}\blab(?:oratory)? manager\b", re.I)
JOIN_RE = re.compile(r"(join (?:us|the lab|our group)|prospective students?|openings?|opportunities|we are (?:looking|hiring))", re.I)
COMPUTATIONAL = re.compile(r"\b(machine learning|deep learning|computer vision|simulation|computational|data|software|"
                           r"algorithm|robotics|embedded|signal processing|control|modeling|optimization|ai)\b", re.I)
VARIANT_KEYWORDS = {
    "initial_a": r"mechanical|robot|prosthe|biomechan|manufactur|materials|actuator|soft robot|mechatronic|design|rehabilit|fluid",
    "initial_b": r"electrical|embedded|sensor|circuit|signal|power|wireless|mems|photonic|iot|instrumentation|antenna|energy|"
                 r"environment",
    "initial_c": r"machine learning|deep learning|vision|artificial intelligence|\bai\b|software|computer|data|algorithm|"
                 r"network|language|learning|security",
}
INTEREST = re.compile("|".join(VARIANT_KEYWORDS.values()), re.I)


@dataclass
class Evidence:
    quote: str
    url: str


@dataclass
class ProfileInfo:
    url: str
    text: str
    research_areas: str = ""
    join_text: str = ""
    exclusions: list[Evidence] = field(default_factory=list)
    hs_positive: list[Evidence] = field(default_factory=list)
    outreach: list[Evidence] = field(default_factory=list)
    prior_interns: list[Evidence] = field(default_factory=list)
    lab_manager_note: str = ""
    members: list[tuple[str, str, str]] = field(default_factory=list)   # (name, group, profile_url or "")
    informal: bool = False


def sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\s{2,}", flat) if 20 <= len(s.strip()) <= 400]


def _first_match(patterns: list[str], sents: list[str], url: str) -> list[Evidence]:
    out = []
    for s in sents:
        if any(re.search(p, s, re.I) for p in patterns):
            out.append(Evidence(s, url))
    return out[:3]


def research_areas_from(text: str, soup: BeautifulSoup) -> str:
    for h in soup.find_all(re.compile("^h[1-5]$")):
        if re.search(r"research (?:interests|areas|focus)|areas of (?:research|expertise)|interests", h.get_text(), re.I):
            nxt = h.find_next(["p", "ul", "div"])
            if nxt:
                return re.sub(r"\s+", " ", nxt.get_text(" ", strip=True))[:400]
    m = re.search(r"research (?:interests|areas|focus)[:\s]+([^\n]{20,300})", text, re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


MEMBER_HEADS = re.compile(r"(graduate students?|ph\.?d\.? students?|doctoral|postdoc|post-?doctoral|current (?:members|students)|"
                          r"lab members|people|team)", re.I)
PERSON = re.compile(r"^[A-Z][\w'’\-\.]+(?:\s+[A-Z][\w'’\-\.]+){1,3}$")


def extract_members(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str, str]]:
    from urllib.parse import urljoin
    out: list[tuple[str, str, str]] = []
    for h in soup.find_all(re.compile("^h[1-4]$")):
        label = h.get_text(" ", strip=True)
        if not MEMBER_HEADS.search(label):
            continue
        group = "postdoc" if re.search(r"post", label, re.I) else "grad_student"
        for sib in h.find_next_siblings():
            if sib.name and re.match(r"^h[1-4]$", sib.name):
                break
            for a in sib.find_all("a", href=True) if hasattr(sib, "find_all") else []:
                t = a.get_text(" ", strip=True)
                if PERSON.match(t) and not re.search(r"lab|group|university|department|school|home|news", t, re.I):
                    out.append((t, group, urljoin(base_url, a["href"]).split("#")[0]))
            for li in sib.find_all(["li", "strong", "b"]) if hasattr(sib, "find_all") else []:
                t = li.get_text(" ", strip=True).split(",")[0].split("(")[0].strip()
                if PERSON.match(t) and not any(t == n for n, _, _ in out):
                    out.append((t, group, ""))
    seen, uniq = set(), []
    for item in out:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def parse_profile(html: str, url: str) -> ProfileInfo:
    soup = BeautifulSoup(html, "html.parser")
    text = page_text(html)
    sents = sentences(text)
    info = ProfileInfo(url=url, text=text)
    info.research_areas = research_areas_from(text, soup)
    join = JOIN_RE.search(text)
    if join:
        info.join_text = re.sub(r"\s+", " ", text[max(0, join.start() - 40):join.end() + 200]).strip()
    info.exclusions = _first_match(NEGATIVE, sents, url)
    info.hs_positive = [e for e in _first_match(POSITIVE_HS, sents, url) if e not in info.exclusions]
    info.outreach = _first_match(OUTREACH, sents, url)
    info.prior_interns = _first_match(PRIOR_INTERNS, sents, url)
    m = LAB_MANAGER.search(text)
    info.lab_manager_note = re.sub(r"\s+", " ", m.group(0)) if m else ""
    info.members = extract_members(soup, url)
    lowered = text.lower()
    info.informal = bool(re.search(r"\b(i'm|i am|hi,|my name is|i like|in my free time|hobbies)\b", lowered)) or \
        "!" in text[:2000]
    return info


def merge_profiles(*infos: ProfileInfo) -> ProfileInfo:
    """Combine a profile page and its lab page: exclusions anywhere win."""
    base = infos[0]
    for other in infos[1:]:
        base.exclusions += other.exclusions
        base.hs_positive += other.hs_positive
        base.outreach += other.outreach
        base.prior_interns += other.prior_interns
        base.members += [m for m in other.members if m[0] not in {x[0] for x in base.members}]
        base.research_areas = base.research_areas or other.research_areas
        base.join_text = base.join_text or other.join_text
        base.lab_manager_note = base.lab_manager_note or other.lab_manager_note
        base.text += "\n" + other.text
    return base


def choose_variant(text: str) -> str | None:
    """Pick template A/B/C by keyword weight over research areas; None if nothing matches our interests."""
    scores = {k: len(re.findall(p, text, re.I)) for k, p in VARIANT_KEYWORDS.items()}
    best = max(scores.values())
    if best == 0:
        return None
    for k in ("initial_a", "initial_b", "initial_c"):
        if scores[k] == best:
            return k
    return None


def fit_score(info: ProfileInfo, *, has_recent_paper: bool, local: bool, hs_open_member_only: bool = False) -> tuple[float, dict]:
    """Spec section 3: prior HS interns +3, outreach/pre-college +2, recent paper +2, Bay Area +1,
    computational sub-task plausible +1, large lab with named grad students +1."""
    parts = {
        "prior_hs_interns": 3 if info.prior_interns else 0,
        "outreach_or_precollege": 2 if (info.outreach or info.hs_positive) else 0,
        "recent_paper": 2 if has_recent_paper else 0,
        "bay_area": 1 if local else 0,
        "computational_task": 1 if COMPUTATIONAL.search(info.research_areas or info.text[:3000]) else 0,
        "large_lab": 1 if len([m for m in info.members if m[1] == "grad_student"]) >= 4 else 0,
    }
    return float(sum(parts.values())), parts
