"""End to end over real HTTP: local http.server + the default fetch."""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jump import db
from jump.agents import crawler, planner

SITE = Path(__file__).parent / "fixtures" / "site"


class Handler(SimpleHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def log_message(self, *args):
        pass

    def send_head(self):
        Handler.requests.append((self.command, self.path))
        return super().send_head()


@pytest.fixture
def server():
    Handler.requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(SITE)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_plan_and_crawl_over_http(conn, server):
    pid = db.upsert_place(conn, server, "/c/", revisit=0.9, status="active")
    db.upsert_page(conn, server + "/c/", place_id=pid, visited=1, revisit=0.9)
    assert planner.run_once(conn)["crawl_jobs_posted"] == 1
    res = crawler.run_pending(conn, delay=0)
    assert res["jobs"] == 1 and res["failed"] == 0

    paths = [p for _, p in Handler.requests]
    assert all(m == "GET" for m, _ in Handler.requests)
    assert paths[0] == "/robots.txt"
    assert set(paths[1:]) == {"/c/", "/c/lectures/l01.html", "/c/lectures/l02.html", "/c/staff.html", "/c/old.html"}

    def page(path):
        return conn.execute("SELECT * FROM pages WHERE url = ?", (server + path,)).fetchone()
    l02 = page("/c/lectures/l02.html")
    assert l02["title"] == "Lecture 2" and l02["fetch_status"] == 200 and l02["place_id"] == pid
    assert page("/c/old.html")["fetch_status"] == 404
    assert page("/c/lectures/l02.pdf")["kind"] == "pdf"
    assert page("/c/private/grades.html")["crawled_at"] is None
    ctx = conn.execute("SELECT context FROM links WHERE to_id = ?", (page("/c/lectures/l02.pdf")["id"],)).fetchone()
    assert ctx["context"] == "Lecture 2 · RPC · notes · slides"
    assert conn.execute("SELECT status FROM jobs WHERE type = 'crawl'").fetchone()[0] == "done"
    assert planner.run_once(conn)["crawl_jobs_posted"] == 0      # just crawled
