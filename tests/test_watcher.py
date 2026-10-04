import json

import pytest

from jump import places
from jump.agents import watcher

NOW = 1_790_000_000.0
DAY = 86400
COURSE = "https://courses.cs.washington.edu/courses/cse452/26au/"


class Visits:
    """Writes visits the way the monitor does."""

    def __init__(self, conn):
        self.conn = conn

    def visit(self, url, ts, transition=0, duration_s=60, from_url=None, title=None):
        self.conn.execute("INSERT INTO visits(url, ts, transition, duration_s, from_url) VALUES (?, ?, ?, ?, ?)",
                          (url, ts, transition, duration_s, from_url))
        if title:
            self.conn.execute("INSERT INTO pages(url, title) VALUES (?, ?) ON CONFLICT(url) DO NOTHING", (url, title))
        return url


@pytest.fixture
def hist(conn):
    h = Visits(conn)
    for k, d in enumerate(range(0, 16, 2)):                          # a course, visited every other day
        home = h.visit(COURSE, NOW - d * DAY, transition=1, title="CSE 452")
        for j in range(3):
            h.visit(f"{COURSE}lectures/l{(k * 3 + j) % 20:02d}.html", NOW - d * DAY + 60 * (j + 1),
                    from_url=home, title=f"Lecture {j}")
    for i, site in enumerate(["https://a.com/x", "https://b.org/y", "https://c.net/z"]):  # searching around
        s = h.visit(f"https://search.example/s?q=thing{i}", NOW - i * DAY - 3600, transition=1)
        h.visit(site, NOW - i * DAY - 3500, from_url=s, duration_s=3)
    return h


def _places(conn):
    return {r["scope"]: dict(r) for r in conn.execute("SELECT * FROM places")}


def _pending(conn):
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'rescored' AND status = 'pending'").fetchone()[0]


def test_run_once_builds_places_hubs_and_pages(conn, hist):
    s = watcher.run_once(conn, NOW)
    assert json.loads(json.dumps(s)) == s
    assert s["visits"] == 38 and s["hubs"] == 1 and s["active"] == 1
    got = _places(conn)
    course = got[COURSE]
    assert course["status"] == "active" and course["revisit"] > 0.9 and course["alias"] == "cse452"
    assert json.loads(course["features"])["n_days"] == 8
    assert got["https://search.example/"]["status"] == "hub"
    assert got["https://a.com/"]["status"] == "dropped"
    assert json.loads(got["https://a.com/"]["features"])["from_search"] == 1.0

    page = conn.execute("SELECT * FROM pages WHERE url = ?", (COURSE,)).fetchone()
    assert page["visited"] == 1 and page["visit_count"] == 8 and page["title"] == "CSE 452"
    assert page["place_id"] == course["id"] and page["kind"] == "html" and page["revisit"] > 0.5
    assert conn.execute("SELECT kind FROM pages WHERE url LIKE '%l01.html'").fetchone()[0] == "html"
    assert not conn.execute("SELECT 1 FROM pages WHERE url LIKE 'https://search.example/%'").fetchone()
    assert _pending(conn) == len(got) == s["rescored_jobs"]


def test_run_once_is_idempotent(conn, hist):
    watcher.run_once(conn, NOW)
    conn.execute("UPDATE jobs SET status = 'done'")
    conn.execute("UPDATE pages SET fts_dirty = 0")
    s = watcher.run_once(conn, NOW)
    assert s["rescored_jobs"] == 0 and s["pages_updated"] == 0
    assert _pending(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE fts_dirty = 1").fetchone()[0] == 0


def test_rescore_only_on_meaningful_change(conn, hist):
    watcher.run_once(conn, NOW)
    conn.execute("UPDATE jobs SET status = 'done'")
    before = _places(conn)["https://b.org/"]["revisit"]
    for d in range(1, 6):                                           # b.org becomes a habit
        hist.visit("https://b.org/y", NOW + d * DAY, transition=1, duration_s=300)
    s = watcher.run_once(conn, NOW + 5 * DAY)
    after = _places(conn)["https://b.org/"]
    assert after["revisit"] > before + 0.05
    payloads = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs WHERE status = 'pending'")]
    assert {"place_id": after["id"], "_key": str(after["id"])} in payloads


def test_probation_expires(conn):
    h = Visits(conn)
    for d in (0, 1):
        h.visit("https://wiki.example.com/team/page", NOW - d * DAY, transition=1, duration_s=120)
    watcher.run_once(conn, NOW)
    assert _places(conn)["https://wiki.example.com/team/"]["status"] == "probation"
    watcher.run_once(conn, NOW + 20 * DAY)
    assert _places(conn)["https://wiki.example.com/team/"]["status"] == "dropped"


def test_failure_rolls_back(conn, hist, monkeypatch):
    def boom(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(places, "group", boom)
    with pytest.raises(RuntimeError):
        watcher.run_once(conn, NOW)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 0
