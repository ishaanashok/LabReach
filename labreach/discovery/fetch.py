"""Polite page fetching: robots.txt, 3-5 s per-domain spacing, descriptive User-Agent, disk cache,
no login-walled pages, no CAPTCHA circumvention. Public pages only."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_BYTES = 2_000_000
LOGIN_HINTS = re.compile(r"(login|signin|sign-in|sso|shibboleth|/cas/|/auth/|okta|duosecurity|accounts\.google)", re.I)
CAPTCHA_HINTS = re.compile(r"(captcha|are you a robot|verify you are human|unusual traffic)", re.I)


class FetchBlocked(RuntimeError):
    """Not fetched on purpose: robots.txt, login wall, CAPTCHA, non-HTML, too large, or an error status."""


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    text: str
    from_cache: bool


class Fetcher:
    def __init__(self, settings: dict, cache_dir: Path, *, client: httpx.Client | None = None, sleep=time.sleep,
                 clock=time.monotonic, rng: random.Random | None = None, cache_days: int = 30):
        cfg = settings["fetch"]
        self.ua = cfg["user_agent"]
        self.min_delay, self.max_delay = cfg["min_delay_seconds"], cfg["max_delay_seconds"]
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=20, follow_redirects=True, max_redirects=5,
                                             headers={"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml"})
        self._sleep, self._clock, self._rng = sleep, clock, rng or random.Random()
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.cache_days = cache_days
        self.requests_made = 0

    # -- cache ---------------------------------------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _read_cache(self, url: str) -> Page | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if datetime.fromisoformat(data["fetched_at"]) < datetime.now(UTC) - timedelta(days=self.cache_days):
            return None
        return Page(url, data["final_url"], data["status"], data["text"], True)

    def _write_cache(self, page: Page) -> None:
        self._cache_path(page.url).write_text(json.dumps(
            {"url": page.url, "final_url": page.final_url, "status": page.status, "text": page.text,
             "fetched_at": datetime.now(UTC).isoformat()}))

    # -- politeness ----------------------------------------------------------------------------------
    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wanted = self._rng.uniform(self.min_delay, self.max_delay)
            elapsed = self._clock() - last
            if elapsed < wanted:
                self._sleep(wanted - elapsed)
        self._last_hit[host] = self._clock()

    def _robots_ok(self, url: str) -> bool:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            self._wait_turn(parts.netloc)
            try:
                resp = self.client.get(f"{origin}/robots.txt")
                self.requests_made += 1
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif resp.status_code >= 500:
                    parser.disallow_all = True      # cannot read the rules: be conservative
                elif resp.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(resp.text.splitlines())
                self._robots[origin] = parser
            except httpx.HTTPError:
                parser.disallow_all = True
                self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.ua, url)

    # -- public --------------------------------------------------------------------------------------
    def get(self, url: str) -> Page:
        if urlparse(url).scheme not in ("http", "https"):
            raise FetchBlocked(f"unsupported URL scheme: {url}")
        cached = self._read_cache(url)
        if cached:
            return cached
        if not self._robots_ok(url):
            raise FetchBlocked(f"robots.txt disallows {url}")
        self._wait_turn(urlparse(url).netloc)
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            raise FetchBlocked(f"fetch failed for {url}: {exc}") from exc
        self.requests_made += 1
        final = str(resp.url)
        if resp.status_code in (401, 403) or LOGIN_HINTS.search(urlparse(final).path + "?" + urlparse(final).query) \
                or LOGIN_HINTS.search(urlparse(final).netloc):
            raise FetchBlocked(f"login-walled or forbidden: {url}")
        if resp.status_code >= 400:
            raise FetchBlocked(f"HTTP {resp.status_code} for {url}")
        ctype = resp.headers.get("content-type", "text/html")
        if not re.search(r"html|xml|text/plain", ctype):
            raise FetchBlocked(f"not an HTML page ({ctype}): {url}")
        if len(resp.content) > MAX_BYTES:
            raise FetchBlocked(f"page too large: {url}")
        if CAPTCHA_HINTS.search(resp.text[:5000]) and len(resp.text) < 20000:
            raise FetchBlocked(f"CAPTCHA/bot check at {url}; skipped (never circumvented)")
        page = Page(url, final, resp.status_code, resp.text, False)
        self._write_cache(page)
        return page
