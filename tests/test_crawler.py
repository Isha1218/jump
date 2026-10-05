from fakesite import FakeFetch

from jump import config, db, jobs
from jump.agents import crawler
from jump.crawl.fetch import FetchResult
from jump.crawl.priority import rarity

O = "https://cs.test"
C = O + "/c/"
NAV = f'<nav><a href="{C}">Home</a> <a href="{C}staff.html">Staff</a> <a href="{C}syllabus.html">Syllabus</a></nav>'


def page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{NAV}<h1>{title}</h1>{body}</body></html>"


def lecture(n: int, prev: int | None, nxt: int | None) -> str:
    links = f'<a href="l{prev:02d}.html">Previous</a>' if prev else ""
    links += f' <a href="l{nxt:02d}.html">Next</a>' if nxt else ""
    return page(f"Lecture {n}", f'<p>Notes for lecture {n}. <a href="l{n:02d}.pdf">slides</a></p>{links}')


ROWS = [(3, "Intro"), (4, "Clocks"), (5, "RPC")]
COURSE = {
    C: page("CSE 452", '<p>Welcome to CSE 452. Code is on <a href="https://github.com/">GitHub</a>.</p>'
                       "<h2>Schedule</h2><table>" + "".join(
        f'<tr><td>Lecture {n}</td><td>{t}</td><td><a href="lectures/l{n:02d}.pdf">slides</a></td></tr>'
        for n, t in ROWS) + "</table>"),
    C + "lectures/l03.html": lecture(3, None, 4),
    C + "lectures/l04.html": lecture(4, 3, 5),
    C + "lectures/l05.html": lecture(5, 4, None),
    C + "staff.html": page("Staff", "<p>TAs</p>"),
    C + "syllabus.html": page("Syllabus", "<p>Grading</p>"),
}


def make_place(conn, origin=O, prefix="/c/", revisit=0.9, visited=()):
    pid = db.upsert_place(conn, origin, prefix, revisit=revisit, status="active")
    for url, rv in visited:
        db.upsert_page(conn, url, place_id=pid, visited=1, revisit=rv)
    return pid


def row(conn, url):
    return conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()


def test_course_site(conn):
    visited = [(C, 0.9), (C + "lectures/l03.html", 0.9), (C + "lectures/l04.html", 0.9)]
    pid = make_place(conn, visited=visited)
    f = FakeFetch(COURSE)
    res = crawler.crawl_place(conn, pid, budget=50, fetch=f, delay=0)

    order = f.pages_fetched
    assert res["stopped_reason"] == "frontier_empty"
    assert set(order) == set(COURSE) and len(order) == len(COURSE)
    assert order.index(C + "lectures/l05.html") < order.index(C + "staff.html")
    assert res["fetched"] == len(COURSE)

    # links are only read from the 3 visited pages: nav links are on all of them -> rarity ~0
    n_pages = len(visited)
    def linking(url):
        return conn.execute("SELECT COUNT(*) FROM links WHERE to_id = ?", (db.page_id(conn, url),)).fetchone()[0]
    assert rarity(n_pages, linking(C + "staff.html")) < 0.25
    assert rarity(n_pages, linking(C + "syllabus.html")) < 0.25
    assert rarity(n_pages, linking(C + "lectures/l05.html")) > 0.5

    pdf = row(conn, C + "lectures/l05.pdf")
    assert pdf["kind"] == "pdf" and pdf["place_id"] == pid and pdf["fetch_status"] is None
    ctx = conn.execute("SELECT context FROM links WHERE to_id = ? AND context LIKE 'Lecture%'", (pdf["id"],)).fetchone()
    assert ctx["context"] == "Lecture 5 · RPC · slides"
    gh = row(conn, "https://github.com/")
    assert gh["place_id"] is None and gh["kind"] == "html" and gh["crawled_at"] is None
    assert not any(".pdf" in u or "github" in u for u in f.calls)

    l05 = row(conn, C + "lectures/l05.html")
    assert l05["title"] == "Lecture 5" and l05["fetch_status"] == 200 and l05["kind"] == "html"
    # l05 is one link away: fetched for its text, but its own links are not followed or saved
    assert conn.execute("SELECT COUNT(*) FROM links WHERE from_id = ?", (l05["id"],)).fetchone()[0] == 0
    assert "Notes for lecture 5" in l05["snippet"] and l05["place_id"] == pid
    l03 = row(conn, C + "lectures/l03.html")
    assert l03["visited"] == 1 and l03["revisit"] == 0.9 and l03["crawled_at"]   # watcher columns untouched
    assert conn.execute("SELECT last_crawled FROM places WHERE id = ?", (pid,)).fetchone()[0]


def test_nothing_crawled_without_visited_pages(conn):
    pid = make_place(conn)
    f = FakeFetch(COURSE)
    res = crawler.crawl_place(conn, pid, budget=10, fetch=f, delay=0)
    assert f.pages_fetched == [] and res["stopped_reason"] == "frontier_empty"


def test_only_recent_visits_are_crawled(conn, monkeypatch):
    import time
    monkeypatch.setattr(config, "RECENT_DAYS", 30)
    pid = make_place(conn)
    db.upsert_page(conn, C + "lectures/l03.html", place_id=pid, visited=1, revisit=0.9, last_visit=time.time())
    db.upsert_page(conn, C + "lectures/l04.html", place_id=pid, visited=1, revisit=0.9,
                   last_visit=time.time() - 60 * 86400)
    f = FakeFetch(COURSE)
    crawler.crawl_place(conn, pid, budget=10, fetch=f, delay=0)
    assert f.pages_fetched[0] == C + "lectures/l03.html" and C + "lectures/l05.html" not in f.pages_fetched


def test_budget_on_open_ended_site(conn):
    g = O + "/gen/"
    def gen(url):
        if url.startswith(g) and url.endswith(".html"):
            n = int(url[len(g):-5])
            return "<html><body>" + "".join(f'<a href="{n + k}.html">p{n + k}</a>' for k in range(1, 6)) + "</body></html>"
    pid = make_place(conn, prefix="/gen/", visited=[(g + "1.html", 0.8)])
    f = FakeFetch({}, fallback=gen)
    res = crawler.crawl_place(conn, pid, budget=3, fetch=f, delay=0)
    assert res["stopped_reason"] == "budget"
    assert res["fetched"] == 3 == len(f.pages_fetched)


def test_stops_one_link_away_on_a_deep_chain(conn):
    pages = {}
    url = O + "/d/"
    for i in range(30):
        nxt = f"{url}x{chr(97 + i)}/"
        pages[url] = f'<a href="{nxt}">deeper</a>'
        url = nxt
    pid = make_place(conn, prefix="/d/", revisit=0.8, visited=[(O + "/d/", 0.8)])
    f = FakeFetch(pages)
    res = crawler.crawl_place(conn, pid, budget=100, fetch=f, delay=0)
    assert f.pages_fetched == [O + "/d/", O + "/d/xa/"] and res["stopped_reason"] == "frontier_empty"


def test_robots_and_action_urls(conn):
    pages = {
        O + "/robots.txt": "User-agent: *\nDisallow: /c/private/\n",
        C: '<a href="private/grades.html">Grades</a> <a href="/c/logout">Log out</a> <a href="ok.html">ok</a>',
        C + "ok.html": "<p>fine</p>",
        C + "private/grades.html": "<p>secret</p>",
        C + "logout": "<p>bye</p>",
    }
    pid = make_place(conn, visited=[(C, 0.9)])
    f = FakeFetch(pages)
    res = crawler.crawl_place(conn, pid, budget=10, fetch=f, delay=0)
    assert f.pages_fetched == [C, C + "ok.html"]
    assert res["stopped_reason"] == "frontier_empty"
    for u in (C + "private/grades.html", C + "logout"):
        r = row(conn, u)
        assert r["place_id"] == pid and r["crawled_at"] is None


def test_records_fetch_status_and_non_html(conn):
    pages = {
        C: '<a href="gone.html">gone</a> <a href="doc">doc</a>',
        C + "doc": FetchResult(200, "application/pdf", C + "doc", None),
    }
    pid = make_place(conn, visited=[(C, 0.9)])
    crawler.crawl_place(conn, pid, budget=10, fetch=FakeFetch(pages), delay=0)
    assert row(conn, C + "gone.html")["fetch_status"] == 404
    doc = row(conn, C + "doc")
    assert doc["fetch_status"] == 200 and doc["kind"] == "pdf"


def test_delay_between_requests(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)
    pid = make_place(conn, visited=[(C, 0.9)])
    crawler.crawl_place(conn, pid, budget=3, fetch=FakeFetch(COURSE), delay=5)
    assert len(sleeps) == 2 and all(0 < s <= 5 for s in sleeps)


def test_run_pending(conn):
    good = make_place(conn, visited=[(C, 0.9)])
    jobs.post(conn, "crawl", {"place_id": good, "budget": 2}, dedupe_key=str(good))
    jobs.post(conn, "crawl", {"place_id": 999, "budget": 2}, dedupe_key="999")
    jobs.post(conn, "crawl", {"place_id": good, "budget": 2})
    res = crawler.run_pending(conn, max_jobs=2, fetch=FakeFetch(COURSE), delay=0)
    assert (res["jobs"], res["failed"], res["fetched"]) == (2, 1, 2) and res["discovered"] > 0
    st = [r[0] for r in conn.execute("SELECT status FROM jobs ORDER BY id")]
    assert st == ["done", "failed", "pending"]
    assert "no place 999" in conn.execute("SELECT error FROM jobs WHERE id = 2").fetchone()[0]
    assert crawler.run_pending(conn, fetch=FakeFetch(COURSE), delay=0)["jobs"] == 1


def test_sign_in_pages_and_your_own_tab_text_are_not_overwritten(conn):
    login = '<html><head><title>NetID sign-in</title></head><body><form><input type="password"></form></body></html>'
    site = {C: page("CSE 452", f'<p><a href="grades.html">Grades</a> <a href="tsai@cs.test">tsai@cs.test</a></p>'),
            C + "grades.html": login}
    pid = make_place(conn, visited=[(C, 0.9)])
    db.upsert_page(conn, C, snippet="Read from your tab", title="CSE 452 Home")
    crawler.crawl_place(conn, pid, budget=10, fetch=FakeFetch(site), delay=0)
    home, grades = row(conn, C), row(conn, C + "grades.html")
    assert home["snippet"] == "Read from your tab" and home["title"] == "CSE 452 Home"
    assert home["crawled_at"] is not None                                # links were still read
    assert grades["fetch_status"] == 401 and not grades["snippet"]       # sign-in wall: nothing kept
    assert row(conn, C + "tsai@cs.test") is None                         # bare email isn't a link
