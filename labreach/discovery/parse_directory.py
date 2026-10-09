"""Faculty-directory parsing. Heuristics first (free); a tool-free Claude call only when a directory defeats them.
Every person the model returns is re-checked by code against the page's real links and text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

NAME_RE = re.compile(r"^(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?[A-Z][\w'’\-\.]+(?:\s+[A-Z][\w'’\-\.]+){1,3}$")
NOT_NAMES = re.compile(r"\b(faculty|research|contact|people|directory|read more|home|department|school|college|"
                       r"university|engineering|science|apply|news|events|about|students?|staff|publications?|"
                       r"laborator|institute|center|program|lab|group|view|profile|more|website|learn)\b", re.I)
TITLE_RE = re.compile(r"((?:Assistant|Associate|Full|Distinguished|Chair|Endowed|Named)?\s*(?:Professor)[^\n|,;]{0,60})", re.I)
EXCLUDED_TITLES = re.compile(r"emerit|adjunct|visiting|lecturer|affiliate|courtesy|in memoriam|retired", re.I)


@dataclass
class Person:
    name: str
    title: str
    profile_url: str

    @property
    def first(self) -> str:
        return self.name.split()[0]

    @property
    def last(self) -> str:
        return self.name.split()[-1]


def clean_name(text: str) -> str:
    text = re.sub(r"^(?:Prof(?:essor)?\.?|Dr\.?)\s+", "", text.strip())
    text = re.sub(r"\s*,\s*(?:Ph\.?D\.?|PhD|Sc\.?D\.?).*$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_name(text: str) -> bool:
    text = text.strip()
    if not 5 <= len(text) <= 48 or any(ch.isdigit() for ch in text) or NOT_NAMES.search(text):
        return False
    return bool(NAME_RE.match(text.replace(",", "")))


def _title_near(anchor) -> tuple[str, str]:
    """(title, surrounding text); exclusions are checked on the surrounding text so 'Adjunct Professor' is caught."""
    node = anchor
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if len(text) > 400:
            break
        m = TITLE_RE.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(), text
    return "", ""


def parse_directory_heuristic(html: str, base_url: str) -> list[Person]:
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc.split(".", 1)[-1]
    seen: dict[str, Person] = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if "," in text and text.count(",") == 1:                        # "Last, First" style
            last, first = [t.strip() for t in text.split(",")]
            text = f"{first} {last}"
        if not _looks_like_name(text):
            continue
        url = urljoin(base_url, a["href"]).split("#")[0]
        if not url.startswith("http") or host not in urlparse(url).netloc:
            continue
        title, context = _title_near(a)
        if not title or EXCLUDED_TITLES.search(context):
            continue
        seen.setdefault(url, Person(clean_name(text), title, url))
    return list(seen.values())


class _LlmPerson(BaseModel):
    name: str
    title: str
    profile_url: str


class _LlmDirectory(BaseModel):
    people: list[_LlmPerson]


def parse_directory_with_claude(html: str, base_url: str, ask) -> list[Person]:
    """`ask(prompt, schema)` is injected (budgeted ask_claude). Output is validated against the page itself."""
    from ..claude_cli import wrap_untrusted
    soup = BeautifulSoup(html, "html.parser")
    links = {urljoin(base_url, a["href"]).split("#")[0]: a.get_text(" ", strip=True) for a in soup.find_all("a", href=True)}
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    lines = [f"{label} -> {url}" for url, label in links.items() if label][:400]
    prompt = ("From this faculty directory page, list professors (assistant/associate/full; skip emeritus, adjunct, "
              "visiting, lecturers) with their exact name, title, and profile URL copied from the links list. "
              "JSON: {people:[{name,title,profile_url}]}\n\n" + wrap_untrusted("directory", "LINKS:\n" + "\n".join(lines)
                                                                              + "\n\nTEXT:\n" + text[:6000]))
    result = ask(prompt, _LlmDirectory)
    people = []
    for p in result.people:
        name = clean_name(p.name)
        if p.profile_url in links and name.split()[-1].lower() in text.lower() and not EXCLUDED_TITLES.search(p.title) \
                and "professor" in p.title.lower():
            people.append(Person(name, p.title, p.profile_url))
    return people


def parse_directory(html: str, base_url: str, ask=None, min_heuristic: int = 8) -> list[Person]:
    people = parse_directory_heuristic(html, base_url)
    if len(people) >= min_heuristic or ask is None:
        return people
    return parse_directory_with_claude(html, base_url, ask) or people
