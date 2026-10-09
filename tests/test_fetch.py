import httpx
import pytest

from labreach.discovery.fetch import FetchBlocked, Fetcher


def make(settings, tmp_path, routes, sleeps=None, clock=None):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path, request.headers.get("user-agent")))
        key = (request.url.host, request.url.path)
        r = routes.get(key) or routes.get(request.url.path)
        if r is None:
            return httpx.Response(404)
        if callable(r):
            return r(request)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, text=r, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                          headers={"User-Agent": settings["fetch"]["user_agent"]})
    t = [0.0]
    fetcher = Fetcher(settings, tmp_path / "cache", client=client, sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                      clock=clock or (lambda: t[0]))
    return fetcher, seen, t


def test_robots_disallow_means_page_is_never_requested(settings, tmp_path):
    routes = {"/robots.txt": "User-agent: *\nDisallow: /private", "/private/x": "secret"}
    fetcher, seen, _ = make(settings, tmp_path, routes)
    with pytest.raises(FetchBlocked, match="robots"):
        fetcher.get("https://me.stanford.edu/private/x")
    assert all(path == "/robots.txt" for _, path, _ in seen)


def test_robots_allow_and_descriptive_user_agent(settings, tmp_path):
    fetcher, seen, _ = make(settings, tmp_path, {"/robots.txt": "User-agent: *\nDisallow: /private", "/ok": "<p>hi</p>"})
    assert fetcher.get("https://me.stanford.edu/ok").text == "<p>hi</p>"
    assert all("LabReachBot" in ua and "ishaan.ashok123@gmail.com" in ua for _, _, ua in seen)


def test_unreadable_robots_is_conservative(settings, tmp_path):
    fetcher, seen, _ = make(settings, tmp_path, {"/robots.txt": httpx.Response(500), "/ok": "x"})
    with pytest.raises(FetchBlocked):
        fetcher.get("https://me.stanford.edu/ok")
    fetcher2, _, _ = make(settings, tmp_path / "b", {"/ok": "x"})       # robots.txt 404 means no rules
    assert fetcher2.get("https://me.stanford.edu/ok").text == "x"


def test_per_domain_spacing_3_to_5_seconds(settings, tmp_path):
    sleeps = []
    fetcher, _, _ = make(settings, tmp_path, {"/a": "a", "/b": "b", "/c": "c"}, sleeps)
    fetcher.get("https://me.stanford.edu/a")
    fetcher.get("https://me.stanford.edu/b")        # same host, no time elapsed: must wait
    assert sleeps and all(3 <= s <= 5 for s in sleeps)
    n = len(sleeps)
    fetcher.get("https://ee.berkeley.edu/c")        # new host: robots.txt goes first, the page then waits its turn
    assert len(sleeps) == n + 1 and 3 <= sleeps[-1] <= 5


def test_cache_prevents_second_request(settings, tmp_path):
    fetcher, seen, _ = make(settings, tmp_path, {"/a": "a"})
    fetcher.get("https://me.stanford.edu/a")
    count = len(seen)
    again = fetcher.get("https://me.stanford.edu/a")
    assert again.from_cache and len(seen) == count


def test_login_walls_captcha_and_non_html_are_skipped(settings, tmp_path):
    routes = {
        "/profile": lambda r: httpx.Response(302, headers={"location": "https://me.stanford.edu/cas/login?service=x"}),
        "/cas/login": "<form>Login</form>",
        "/captcha": "<html>Please complete the CAPTCHA to continue</html>",
        "/forbidden": httpx.Response(403),
        "/file.pdf": httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
    }
    fetcher, _, _ = make(settings, tmp_path, routes)
    for path in ("/profile", "/captcha", "/forbidden", "/file.pdf"):
        with pytest.raises(FetchBlocked):
            fetcher.get(f"https://me.stanford.edu{path}")
