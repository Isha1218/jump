import json

from jump import monitor
from jump.monitor import Monitor, Tab


class FakeChrome:
    """Scripted Chrome: set `.tab` to what's on screen and `.text` to what the page shows."""

    def __init__(self):
        self.tab, self.text_error = None, None
        self.text = {"url": "", "headings": [], "snippet": ""}

    def active_tab(self):
        return self.tab

    def page_text(self):
        if self.text_error:
            raise RuntimeError(self.text_error)
        return self.text


def _visits(conn):
    return [dict(r) for r in conn.execute("SELECT url, ts, duration_s, from_url FROM visits ORDER BY ts")]


def test_records_visits_with_time_on_page_and_referrer(conn):
    chrome = FakeChrome()
    m = Monitor(conn, chrome=chrome, log=lambda *_: None)
    chrome.tab = Tab("1", False, "https://www.google.com/search?q=cse+452", "cse 452 - Search")
    m.tick(0)
    chrome.tab = Tab("1", False, "https://courses.cs.washington.edu/courses/cse452/26au/", "CSE 452")
    m.tick(2)
    m.tick(30)
    chrome.tab = None                                   # switched to another app
    m.tick(40)
    v = _visits(conn)
    assert [x["url"] for x in v] == ["https://www.google.com/search?q=cse+452",
                                     "https://courses.cs.washington.edu/courses/cse452/26au/"]
    assert v[0]["duration_s"] == 2 and v[1]["duration_s"] == 38
    assert v[1]["from_url"] == v[0]["url"]              # same tab -> referrer, so hub detection still works
    title = conn.execute("SELECT title FROM pages WHERE url = ?", (v[1]["url"],)).fetchone()[0]
    assert title == "CSE 452"


def test_captures_page_text_after_a_few_seconds(conn):
    chrome = FakeChrome()
    m = Monitor(conn, chrome=chrome, log=lambda *_: None)
    url = "https://edstem.org/us/courses/106640/discussion/"
    chrome.tab = Tab("7", False, url, "Ed Discussion")
    chrome.text = {"url": url, "headings": ["CSE 452 Distributed Systems"], "snippet": "Lab 2 questions"}
    m.tick(0)
    assert conn.execute("SELECT snippet FROM pages WHERE url = ?", (url,)).fetchone() is None
    m.tick(monitor.CAPTURE_AT_S[0])
    row = conn.execute("SELECT headings, snippet FROM pages WHERE url = ?", (url,)).fetchone()
    assert json.loads(row["headings"]) == ["CSE 452 Distributed Systems"] and row["snippet"] == "Lab 2 questions"


def test_reads_text_again_later_when_the_page_had_none(conn):
    chrome = FakeChrome()
    m = Monitor(conn, chrome=chrome, log=lambda *_: None)
    url = "https://x.com/home"
    chrome.tab = Tab("1", False, url, "Home / X")
    chrome.text = {"url": url, "headings": [], "snippet": ""}       # still loading
    calls = []
    real = chrome.page_text
    chrome.page_text = lambda: calls.append(1) or real()
    m.tick(0)
    m.tick(monitor.CAPTURE_AT_S[0])
    chrome.text = {"url": url, "headings": [], "snippet": "Cowboys win in overtime against the Eagles"}
    m.tick(monitor.CAPTURE_AT_S[0] + 2)                               # not yet
    m.tick(monitor.CAPTURE_AT_S[1])
    m.tick(monitor.CAPTURE_AT_S[1] + 10)                              # done; no more reads
    assert len(calls) == 2
    snippet = conn.execute("SELECT snippet FROM pages WHERE url = ?", (url,)).fetchone()[0]
    assert snippet.startswith("Cowboys win")


def test_page_js_falls_back_to_innermost_divs():
    js = monitor._TEXT_JS
    assert "if (!blocks.length) for (const el of root.querySelectorAll('div'))" in js
    assert "el.querySelector('div, p, li, td, section, article')" in js   # innermost only, no repeats


def test_skips_incognito_and_non_web_tabs(conn):
    chrome = FakeChrome()
    m = Monitor(conn, chrome=chrome, log=lambda *_: None)
    for tab in [Tab("1", True, "https://bank.example/account", "Bank"), Tab("2", False, "chrome://newtab/", "New Tab")]:
        chrome.tab = tab
        m.tick(0)
        m.tick(10)
    m.close(20)
    assert _visits(conn) == []


def test_visit_kept_when_page_text_is_unavailable(conn):
    chrome = FakeChrome()
    logs = []
    m = Monitor(conn, chrome=chrome, log=logs.append)
    chrome.tab = Tab("1", False, "https://example.com/a", "A")
    chrome.text_error = "Executing JavaScript through AppleScript is turned off."
    m.tick(0)
    m.tick(5)
    m.close(9)
    assert len(_visits(conn)) == 1
    assert any("Allow JavaScript from Apple Events" in line for line in logs)


def test_text_js_is_valid_inside_an_applescript_string():
    js = monitor._TEXT_JS.replace("\\", "\\\\").replace('"', '\\"')
    assert '"' not in js.replace('\\"', "")             # nothing would end the AppleScript string early
    assert "\\\\s+" in js                                # the regex backslash survives AppleScript escaping


def test_compose_snippet_puts_description_and_breadcrumb_first():
    s = monitor.compose_snippet("Discussion board for CSE 123", "CSE 123 › Discussion", "Is lab 2 due Friday?")
    assert s == "Description: Discussion board for CSE 123 | Breadcrumb: CSE 123 › Discussion | Is lab 2 due Friday?"
    assert monitor.compose_snippet("", "", "only text") == "only text"
    assert len(monitor.compose_snippet("x" * 2000, "", "")) == monitor.SNIPPET_CHARS


def test_page_js_skips_clutter_and_samples_each_section():
    js = monitor._TEXT_JS
    for skipped in ("nav", "header", "footer", "aside", "button", "form", "[aria-hidden=true]"):
        assert skipped in js
    assert "meta[name=" in js and "breadcrumb" in js         # (1) the page's own description
    assert "split(' ').length < 5" in js                     # (2) real text blocks only
    assert "perSection >= 2" in js                           # (3) first blocks under every heading
    assert "\n" not in js                                    # one line, for the AppleScript string


def test_unreadable_page_script_output_does_not_crash(conn, monkeypatch):
    monkeypatch.setattr(monitor, "_osascript", lambda script: "missing value")   # Chrome's answer when the script throws
    try:
        monitor.Chrome().page_text()
        raised = False
    except RuntimeError:
        raised = True
    assert raised                                         # turned into the error _capture already handles
    logs = []
    m = Monitor(conn, chrome=monitor.Chrome(), log=logs.append)
    m.current = {"tab_id": "1", "url": "https://www.linkedin.com/in/x/", "title": "x", "start": 0,
                 "from_url": None, "captures": [3]}
    m._capture(m.current)
    assert any("can't read page text" in line for line in logs)


def test_monitor_keeps_running_after_an_error(conn):
    import threading

    class Flaky(FakeChrome):
        calls = 0

        def active_tab(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise ValueError("boom")
            if Flaky.calls > 3:
                stop.set()
            return None

    stop, logs = threading.Event(), []
    Monitor(conn, chrome=Flaky(), log=logs.append).run(stop, poll_s=0)
    assert Flaky.calls > 1 and any("monitor error" in line for line in logs)


def test_meta_selector_values_are_quoted():
    assert 'meta[name="' in monitor._TEXT_JS               # og:description has a colon; unquoted it's invalid CSS


def test_page_you_are_on_is_queued_for_crawling_once(conn):
    import time
    chrome = FakeChrome()
    m = Monitor(conn, chrome=chrome, log=lambda *_: None)
    url = "https://courses.cs.washington.edu/courses/cse452/26au/"
    links = [[url + "lectures/l05.html", "notes", "Lecture 5 · RPC"]]
    chrome.tab = Tab("1", False, url, "CSE 452")
    chrome.text = {"url": url, "headings": [], "snippet": "Welcome", "links": links}
    m.tick(0)
    m.tick(monitor.CAPTURE_AT_S[0])
    m.close(10)
    m.tick(20)                                             # same page again
    m.tick(20 + monitor.CAPTURE_AT_S[0])
    rows = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs WHERE type = 'crawl'")]
    assert len(rows) == 1 and rows[0]["url"] == url and rows[0]["links"] == links
    conn.execute("UPDATE jobs SET status = 'done'")
    conn.execute("UPDATE pages SET crawled_at = ? WHERE url = ?", (time.time(), url))
    m.close(30)
    m.tick(40)
    m.tick(40 + monitor.CAPTURE_AT_S[0])                   # crawled lately: not queued again
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'crawl'").fetchone()[0] == 1


def test_page_js_reads_links_for_the_crawler():
    js = monitor._TEXT_JS
    assert "querySelectorAll('a[href]')" in js and "l: links" in js
    assert "getAttribute('href')" in js                      # bare emails aren't links
