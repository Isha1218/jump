import http.server
import threading
from functools import partial

import pytest

from jump import db, extract

PAGE = """<html><head><meta charset="utf-8"><title>Raw title</title></head><body>
<nav><h2>Site menu</h2>Home Courses Staff</nav>
<div id="app"></div>
<script>
  document.title = "Lecture 5 · RPC";
  document.getElementById("app").innerHTML =
    "<main><h1>CSE 452</h1><h2>Remote procedure calls</h2><p>Slides and recording for lecture five.</p></main>";
</script></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "l05.html").write_text(PAGE)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_reads_text_rendered_by_javascript(site, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "PROFILE", tmp_path / "browser")
    pid = db.upsert_place(conn, site, "/", status="active", revisit=0.9)
    url = f"{site}/l05.html"
    db.upsert_page(conn, url, place_id=pid, visited=1, title="Raw title")
    db.upsert_page(conn, f"{site}/logout", place_id=pid, visited=1)

    summary = extract.run(conn)

    row = conn.execute("SELECT title, headings, snippet FROM pages WHERE url = ?", (url,)).fetchone()
    assert row["title"] == "Lecture 5 · RPC"
    assert "Remote procedure calls" in row["headings"]
    assert "Slides and recording for lecture five." in row["snippet"]
    assert "Site menu" not in row["headings"] and "Home Courses" not in row["snippet"]
    assert summary["extracted"] == 1        # the logout URL is never opened
    assert extract.run(conn)["candidates"] == 1   # done pages aren't revisited; only logout remains
