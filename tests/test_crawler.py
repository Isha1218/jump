import time

from fakesite import FakeFetch

from jump import config, db, jobs
from jump.agents import crawler
from jump.crawl.fetch import FetchResult

O = "https://cs.test"
C = O + "/c/"


def page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"


SITE = {
    C + "lectures/l05.html": page("Lecture 5", "<p>Notes for lecture 5: remote procedure calls.</p>"),
    C + "staff.html": page("Staff", "<p>TAs</p>"),
    O + "/other/news.html": page("News", "<p>Department news</p>"),
}
# what the page script reads from your tab on the course home page: [url, link text, row]
LINKS = [
    [O + "/other/news.html", "News", ""],
    [C + "lectures/l05.html", "notes", "Lecture 5 · RPC · notes · slides"],
    [C + "lectures/l05.pdf", "slides", "Lecture 5 · RPC · notes · slides"],
    ["https://github.com/", "GitHub", "Code is on GitHub"],
    [C + "logout", "Log out", ""],
]


def row(conn, url):
    return conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()


def visited_home(conn):
    pid = db.upsert_place(conn, O, "/c/", revisit=0.9, status="probation")
    db.upsert_page(conn, C, place_id=pid, visited=1, title="CSE 452", snippet="Read from your tab")
    return pid


def test_crawls_one_hop_from_the_page_you_are_on(conn):
    pid = visited_home(conn)
    f = FakeFetch(SITE)
    res = crawler.crawl_page(conn, C, LINKS, fetch=f, delay=0)

    # same-site HTML only, deeper links first; never PDFs, other sites or action URLs
    assert f.pages_fetched == [C + "lectures/l05.html", O + "/other/news.html"]
    assert res == {"fetched": 2, "discovered": 5}
    l05 = row(conn, C + "lectures/l05.html")
    assert l05["title"] == "Lecture 5" and "remote procedure calls" in l05["snippet"] and l05["place_id"] == pid
    assert conn.execute("SELECT COUNT(*) FROM links WHERE from_id = ?", (l05["id"],)).fetchone()[0] == 0
    pdf = row(conn, C + "lectures/l05.pdf")
    assert pdf["kind"] == "pdf" and pdf["place_id"] == pid and pdf["crawled_at"] is None
    ctx = conn.execute("SELECT context FROM links WHERE to_id = ?", (pdf["id"],)).fetchone()[0]
    assert ctx == "Lecture 5 · RPC · notes · slides"
    assert row(conn, "https://github.com/")["place_id"] is None
    assert row(conn, O + "/other/news.html")["place_id"] is None          # outside the place
    home = row(conn, C)
    assert home["crawled_at"] and home["snippet"] == "Read from your tab"  # links read from the tab, not refetched


def test_skips_pages_already_read_or_crawled_lately(conn):
    visited_home(conn)
    db.upsert_page(conn, C + "staff.html", visited=1, snippet="Read from your tab")
    db.upsert_page(conn, O + "/other/news.html", crawled_at=time.time() - 60)
    links = [[C + "staff.html", "Staff", ""], [O + "/other/news.html", "News", ""], [C + "lectures/l05.html", "", ""]]
    f = FakeFetch(SITE)
    crawler.crawl_page(conn, C, links, fetch=f, delay=0)
    assert f.pages_fetched == [C + "lectures/l05.html"]


def test_budget_robots_and_status(conn, monkeypatch):
    monkeypatch.setattr(config, "PAGE_BUDGET", 2)
    site = {O + "/robots.txt": "User-agent: *\nDisallow: /c/private/\n",
            C + "doc": FetchResult(200, "application/pdf", C + "doc", None)}
    links = [[C + "private/grades.html", "", ""], [C + "gone.html", "", ""], [C + "doc", "", ""], [C + "more.html", "", ""]]
    f = FakeFetch(site)
    crawler.crawl_page(conn, C, links, fetch=f, delay=0)
    assert f.pages_fetched == [C + "gone.html", C + "doc"]                # private disallowed; budget 2
    assert row(conn, C + "gone.html")["fetch_status"] == 404
    assert row(conn, C + "doc")["kind"] == "pdf"
    assert row(conn, C + "private/grades.html")["crawled_at"] is None


def test_sign_in_pages_keep_nothing(conn):
    login = '<html><head><title>NetID sign-in</title></head><body><form><input type="password"></form></body></html>'
    crawler.crawl_page(conn, C, [[C + "grades.html", "Grades", ""]], fetch=FakeFetch({C + "grades.html": login}), delay=0)
    grades = row(conn, C + "grades.html")
    assert grades["fetch_status"] == 401 and not grades["snippet"] and not grades["title"]


def test_delay_between_requests(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)
    crawler.crawl_page(conn, C, LINKS, fetch=FakeFetch(SITE), delay=5)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 5


def test_run_pending(conn):
    jobs.post(conn, "crawl", {"url": C, "links": LINKS}, dedupe_key=C)
    jobs.post(conn, "crawl", {"url": C}, dedupe_key="broken")
    res = crawler.run_pending(conn, fetch=FakeFetch(SITE), delay=0)
    assert (res["jobs"], res["failed"], res["fetched"], res["discovered"]) == (2, 1, 2, 5)
    assert [r[0] for r in conn.execute("SELECT status FROM jobs ORDER BY id")] == ["done", "failed"]
