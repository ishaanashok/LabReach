"""Email addresses are taken ONLY from the person's official page text: mailto links or plainly published text,
including 'name [at] school [dot] edu'. Nothing is guessed, constructed, or looked up elsewhere."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from bs4 import BeautifulSoup

PLAIN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}")
AT = r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*"
DOT = r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*"
OBFUSCATED_RE = re.compile(
    rf"[A-Za-z0-9._%+\-]+(?:{AT})(?:[A-Za-z0-9\-]+(?:{DOT}|\.))+[A-Za-z]{{2,}}", re.IGNORECASE)


@dataclass
class EmailHit:
    address: str
    snippet: str          # exact text from the page containing it (plain or obfuscated form)
    kind: str             # mailto | plain | obfuscated


def deobfuscate(text: str) -> str:
    text = re.sub(AT, "@", text, flags=re.IGNORECASE)
    return re.sub(DOT, ".", text, flags=re.IGNORECASE)


def address_in_snippet(address: str, snippet: str) -> bool:
    """Gate-4 helper: the address must appear literally in the stored snippet (or in its de-obfuscated form)."""
    address = address.lower()
    return address in snippet.lower() or address in deobfuscate(snippet).lower().replace(" ", "")


def domain_allowed(address: str, allowed_domains: list[str]) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in allowed_domains)


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n"))


def _window(text: str, start: int, end: int, pad: int = 40) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - pad):end + pad]).strip()


def extract_emails(html: str, allowed_domains: list[str]) -> list[EmailHit]:
    """All addresses published on the page, on allowlisted domains, each with its source snippet."""
    soup = BeautifulSoup(html, "html.parser")
    hits: dict[str, EmailHit] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = unquote(href[7:].split("?")[0]).strip().lower()
            if PLAIN_RE.fullmatch(addr) and domain_allowed(addr, allowed_domains):
                hits.setdefault(addr, EmailHit(addr, f'<a href="mailto:{addr}">{a.get_text(" ", strip=True)}</a>', "mailto"))
    text = page_text(html)
    for m in PLAIN_RE.finditer(text):
        addr = m.group(0).lower()
        if domain_allowed(addr, allowed_domains):
            hits.setdefault(addr, EmailHit(addr, _window(text, m.start(), m.end()), "plain"))
    for m in OBFUSCATED_RE.finditer(text):
        addr = re.sub(r"\s+", "", deobfuscate(m.group(0))).lower()
        if PLAIN_RE.fullmatch(addr) and domain_allowed(addr, allowed_domains):
            hits.setdefault(addr, EmailHit(addr, _window(text, m.start(), m.end()), "obfuscated"))
    return list(hits.values())


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def pick_address_for(person_last: str, person_first: str, hits: list[EmailHit]) -> EmailHit | None:
    """Choose among addresses PUBLISHED on the page, by matching the person's own name in the local part.
    An unmatched or ambiguous address is never taken (the caller marks needs_manual_email)."""
    last, first = normalize_name(person_last), normalize_name(person_first)
    matching = [h for h in hits if len(last) >= 3 and last in normalize_name(h.address.split("@")[0])]
    if len(matching) == 1:
        return matching[0]
    if not matching and first and len(first) >= 3:
        by_first = [h for h in hits if first in normalize_name(h.address.split("@")[0])]
        if len(by_first) == 1:
            return by_first[0]
    return None
