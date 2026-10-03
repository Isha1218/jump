from fakesite import FakeFetch

from jump.crawl.fetch import FetchResult, Robots, fetch


def test_fetch_never_raises():
    r = fetch("http://127.0.0.1:1/nothing")
    assert r.status == 0 and r.text is None
    assert fetch("http://[bad").status == 0


def test_is_html():
    assert FetchResult(200, "text/html; charset=utf-8").is_html
    assert FetchResult(200, "application/xhtml+xml").is_html
    assert not FetchResult(200, "application/pdf").is_html


def test_robots_rules_and_cache():
    f = FakeFetch({"https://x.edu/robots.txt": "User-agent: *\nDisallow: /private/\n"})
    rb = Robots(f)
    assert rb.allowed("https://x.edu/c/a.html")
    assert not rb.allowed("https://x.edu/private/a.html")
    assert f.calls.count("https://x.edu/robots.txt") == 1


def test_robots_agent_specific_and_status():
    f = FakeFetch({
        "https://a.edu/robots.txt": "User-agent: JumpBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
        "https://b.edu/robots.txt": FetchResult(403, "text/html", "", ""),
        "https://c.edu/robots.txt": FetchResult(503, "text/html", "", ""),
    })
    rb = Robots(f)
    assert not rb.allowed("https://a.edu/x")
    assert not rb.allowed("https://b.edu/x")
    assert not rb.allowed("https://c.edu/x")      # unknown -> not fetched, retried next time
    assert not rb.allowed("https://c.edu/y")
    assert f.calls.count("https://c.edu/robots.txt") == 2
    assert rb.allowed("https://d.edu/x")          # 404 -> allow all
