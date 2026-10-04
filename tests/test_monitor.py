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
    m.tick(monitor.CAPTURE_AFTER_S)
    row = conn.execute("SELECT headings, snippet FROM pages WHERE url = ?", (url,)).fetchone()
    assert json.loads(row["headings"]) == ["CSE 452 Distributed Systems"] and row["snippet"] == "Lab 2 questions"


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
