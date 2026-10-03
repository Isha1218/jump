import threading

from jump import db, jobs, urls


def test_upsert_page_and_links(conn):
    a = db.upsert_page(conn, "https://x.edu/a", title="A")
    b = db.upsert_page(conn, "https://x.edu/b")
    assert db.upsert_page(conn, "https://x.edu/a", title="A2") == a
    conn.execute("UPDATE pages SET fts_dirty = 0")
    db.add_link(conn, a, b, "Lecture 5", "Oct 3 · RPC · slides")
    assert conn.execute("SELECT fts_dirty FROM pages WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT title FROM pages WHERE id = ?", (a,)).fetchone()[0] == "A2"


def test_upsert_place(conn):
    pid = db.upsert_place(conn, "https://x.edu", "/c/", revisit=0.9, features={"days": 3})
    assert db.upsert_place(conn, "https://x.edu", "/c/", revisit=0.5) == pid
    row = conn.execute("SELECT * FROM places WHERE id = ?", (pid,)).fetchone()
    assert row["revisit"] == 0.5 and row["scope"] == "https://x.edu/c/"


def test_jobs_dedupe_claim_finish(conn):
    assert jobs.post(conn, "crawl", {"place_id": 1}, dedupe_key="1")
    assert jobs.post(conn, "crawl", {"place_id": 1}, dedupe_key="1") is None
    job = jobs.claim(conn, ["crawl"])
    assert job["payload"]["place_id"] == 1
    assert jobs.claim(conn, ["crawl"]) is None
    jobs.finish(conn, job["id"])
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "done"


def test_claim_is_exclusive_across_threads(tmp_path):
    path = tmp_path / "g.db"
    c = db.connect(path)
    for i in range(50):
        jobs.post(c, "crawl", {"i": i})
    got, lock = [], threading.Lock()

    def worker():
        wc = db.connect(path)
        while (j := jobs.claim(wc, ["crawl"])):
            with lock:
                got.append(j["id"])

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(got) == list(range(1, 51))


def test_normalize():
    assert urls.normalize("/b#x", "https://X.edu:443/a/") == "https://x.edu/b"
    assert urls.normalize("https://x.edu/p?utm_source=a&id=3") == "https://x.edu/p?id=3"
    assert urls.normalize("mailto:a@b.c") is None
    assert urls.normalize("javascript:void(0)") is None


def test_kind_and_template():
    assert urls.kind_from_url("https://x.edu/l04.pdf") == "pdf"
    assert urls.kind_from_url("https://docs.google.com/presentation/d/1") == "slides"
    assert urls.kind_from_url("https://www.youtube.com/watch?v=1") == "video"
    assert urls.url_template("https://x.edu/lectures/l04.html") == "x.edu/lectures/l{n}.html"
    assert urls.url_template("https://x.edu/lectures/l05.html") == urls.url_template("https://x.edu/lectures/l04.html")


def test_scope_relation():
    o, p = "https://x.edu", "/c/452/"
    assert urls.relation("https://x.edu/c/452/index.html", "https://x.edu/c/452/lec/l5", o, p) == "deeper"
    assert urls.relation("https://x.edu/c/452/lec/", "https://x.edu/c/452/staff", o, p) == "sideways"
    assert urls.relation("https://x.edu/c/452/", "https://x.edu/c/451/", o, p) == "outside"
    assert urls.in_scope("https://x.edu/c/452", o, p)


def test_url_words():
    assert urls.url_words("https://x.edu/cse452/L04-rpc.pdf") == "x edu cse 452 l 04 rpc pdf"
