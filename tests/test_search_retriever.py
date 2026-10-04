import time

from jump import db
from jump.search import retriever

COURSE = "https://courses.cs.washington.edu"


def build_graph(conn):
    """Active course (cse452), a dropped course that also mentions RPC, and Stripe docs."""
    c = db.upsert_place(conn, COURSE, "/courses/cse452/", alias="cse452", revisit=0.9, status="active")
    sched = db.upsert_page(conn, f"{COURSE}/courses/cse452/26au/schedule.html", place_id=c, title="CSE 452 Calendar",
                           visited=1, revisit=0.8, snippet="Course calendar for distributed systems")
    lectures = [("Sep 26", 1, "Introduction"), ("Sep 30", 4, "Consensus"), ("Oct 3", 5, "RPC"), ("Oct 6", 6, "Paxos")]
    ids = {}
    for date, n, topic in lectures:
        url = f"{COURSE}/courses/cse452/26au/l{n:02d}.pdf"
        ids[n] = db.upsert_page(conn, url, place_id=c, kind="pdf")
        db.add_link(conn, sched, ids[n], "slides", f"{date} · Lecture {n} · {topic}")
    d = db.upsert_place(conn, COURSE, "/courses/cse333/", alias="cse333", revisit=0.05, status="dropped")
    dropped = db.upsert_page(conn, f"{COURSE}/courses/cse333/rpc.html", place_id=d, title="Lecture: RPC and sockets",
                             snippet="RPC lecture notes: remote procedure call over sockets", visited=1)
    s = db.upsert_place(conn, "https://docs.stripe.com", "/", alias="stripe", revisit=0.7, status="active")
    hooks = db.upsert_page(conn, "https://docs.stripe.com/webhooks", place_id=s, title="Receive Stripe events in your webhook endpoint",
                           headings=["Webhooks", "Verify signatures"], visited=1)
    gh = db.upsert_place(conn, "https://github.com", "/acme/app/", alias="acme app", revisit=0.6, status="active")
    gh_hooks = db.upsert_page(conn, "https://github.com/acme/app/blob/main/webhooks.py", place_id=gh,
                              title="app/webhooks.py at main", kind="file")
    return {"sched": sched, "l05": ids[5], "l04": ids[4], "dropped": dropped, "hooks": hooks, "gh_hooks": gh_hooks}


def ids_of(results):
    return [r["page_id"] for r in results]


def test_course_lecture_query(conn):
    g = build_graph(conn)
    res = retriever.retrieve(conn, "452 rpc lecture")
    order = ids_of(res)
    assert g["l05"] in order[:3]
    assert order.index(g["l05"]) < order.index(g["dropped"])
    top = next(r for r in res if r["page_id"] == g["l05"])
    assert top["label"] == "Oct 3 · Lecture 5 · RPC — slides"
    assert top["kind"] == "pdf" and "cse452" in top["description"] and len(top["description"]) <= 400


def test_lecture_number_matches_url_digits(conn):
    g = build_graph(conn)
    conn.execute("DELETE FROM links WHERE to_id = ?", (g["l05"],))
    conn.execute("UPDATE pages SET fts_dirty = 1 WHERE id = ?", (g["l05"],))
    # Only the URL (".../l05.pdf") says 5 now; "lecture 5" must still find it via L05 -> "l 5" + lec synonym.
    db.upsert_page(conn, f"{COURSE}/courses/cse452/26au/L05.html", place_id=1, title="L05")
    res = retriever.retrieve(conn, "lecture 5")
    assert any(r["url"].endswith("L05.html") for r in res[:3])


def test_stripe_webhooks(conn):
    g = build_graph(conn)
    assert ids_of(retriever.retrieve(conn, "stripe webhooks"))[0] == g["hooks"]


def test_nasty_input_does_not_raise(conn):
    build_graph(conn)
    for q in ['"', "*", "AND", "(", ")", "NEAR(a b)", "a OR", 'rpc" OR "x', "col:rpc", "^rpc", "日本語 é", "💥", "", "   ", "-", "''"]:
        assert isinstance(retriever.retrieve(conn, q), list)


def test_new_incoming_link_makes_target_findable(conn):
    g = build_graph(conn)
    assert g["l04"] not in ids_of(retriever.retrieve(conn, "quorum"))
    db.add_link(conn, g["hooks"], g["l04"], "Quorum systems explained")
    assert retriever.sync_fts(conn) == 1
    res = retriever.retrieve(conn, "quorum")
    assert ids_of(res)[0] == g["l04"] and res[0]["label"] == "Quorum systems explained"


def test_picks_boost(conn):
    g = build_graph(conn)
    order = ids_of(retriever.retrieve(conn, "webhooks"))
    assert order.index(g["hooks"]) < order.index(g["gh_hooks"])
    for _ in range(3):
        conn.execute("INSERT INTO picks(query, page_id, ts) VALUES ('webhooks', ?, 0)", (g["gh_hooks"],))
    assert ids_of(retriever.retrieve(conn, "webhooks"))[0] == g["gh_hooks"]


def test_outside_pdf_inherits_linking_place_revisit(conn):
    g = build_graph(conn)
    ext = db.upsert_page(conn, "https://papers.example.org/birrell-rpc.pdf", kind="pdf")
    db.add_link(conn, g["sched"], ext, "Implementing Remote Procedure Calls", "Oct 3 · Reading")
    res = retriever.retrieve(conn, "remote procedure calls")
    order = ids_of(res)
    assert order.index(ext) < order.index(g["dropped"])
    assert "cse452" in res[order.index(ext)]["description"]


def test_labels():
    page = {"title": None, "url": "https://x.edu/a/My%20Notes.pdf", "kind": "pdf", "snippet": None}
    assert retriever.label_for(page, ["Lecture 5: RPC", "slides"], []) == "Lecture 5: RPC"
    assert retriever.label_for(page, ["[pdf]"], ["Oct 3 · RPC"]) == "Oct 3 · RPC — [pdf]"
    assert retriever.label_for({**page, "title": "Title"}, ["here"], []) == "Title"
    assert retriever.label_for(page, ["12"], []) == "My Notes.pdf"


def test_dedupe_by_url(conn):
    build_graph(conn)
    db.upsert_page(conn, "https://docs.stripe.com/webhooks/", place_id=3, title="Stripe webhooks")
    urls_ = [r["url"].rstrip("/") for r in retriever.retrieve(conn, "stripe webhooks")]
    assert len(urls_) == len(set(urls_))


def test_performance_5000_pages(conn):
    words = "rpc paxos raft consensus lecture slides homework project exam schedule billing settings".split()
    conn.execute("BEGIN")
    places = [db.upsert_place(conn, "https://site%d.com" % i, "/", alias=f"site{i}", revisit=(i % 10) / 10) for i in range(50)]
    hub = None
    for i in range(5000):
        pid = db.upsert_page(conn, f"https://site{i % 50}.com/p/{i}/l{i % 30:02d}.html", place_id=places[i % 50],
                             title=f"{words[i % 12]} {words[(i * 7) % 12]} {i}", snippet=f"page {i} about {words[(i * 3) % 12]}")
        if i % 50 == 0:
            hub = pid
        elif i % 3 == 0:
            db.add_link(conn, hub, pid, f"Lecture {i % 30}", f"Week {i % 10} · {words[i % 12]}")
    conn.execute("COMMIT")
    retriever.sync_fts(conn)
    t = time.perf_counter()
    res = retriever.retrieve(conn, "site 7 rpc lecture 5 slides")
    elapsed = time.perf_counter() - t
    assert 0 < len(res) <= 200
    assert elapsed < 0.3, elapsed


def test_alias_scoping(conn):
    build_graph(conn)
    db.upsert_place(conn, COURSE, "/courses/cse451/", alias="cse451", revisit=0.9)
    db.upsert_place(conn, COURSE, "/courses/cse461/", alias="cse461", revisit=0.9)
    assert retriever.alias_places(conn, "452 rpc") == [1]
    assert retriever.alias_places(conn, "CSE 452") == [1]
    assert retriever.alias_places(conn, "cse lecture") == []       # 'cse' is shared by many aliases
    assert retriever.alias_places(conn, "stripe webhooks") == [3]


def test_lookalike_pages_collapse_to_one_candidate(conn):
    from jump import db
    pid = db.upsert_place(conn, "https://edstem.org", "/us/courses/106640/", status="active", revisit=0.8, alias="ed")
    db.upsert_page(conn, "https://edstem.org/us/courses/106640/discussion", place_id=pid, visited=1,
                   title="CSE 123 - 26au – Ed Discussion")
    for t in range(8):
        db.upsert_page(conn, f"https://edstem.org/us/courses/106640/discussion/83{t}0682", place_id=pid, visited=1,
                       title=f"Thread {t} – CSE 123 - 26au – Ed Discussion")
    urls_ = [r["url"] for r in retriever.retrieve(conn, "ed cse 123 26au")]
    assert "https://edstem.org/us/courses/106640/discussion" in urls_
    assert sum("/discussion/" in u for u in urls_) == 1          # 8 threads -> one candidate


def test_only_recent_pages_and_one_hop_neighbors(conn, monkeypatch):
    import time
    from jump import config, db
    monkeypatch.setattr(config, "RECENT_DAYS", 30)
    now, old = time.time(), time.time() - 60 * 86400
    pid = db.upsert_place(conn, "https://x.edu", "/c/", status="active", revisit=0.9)
    recent = db.upsert_page(conn, "https://x.edu/c/home", place_id=pid, visited=1, last_visit=now, title="rpc home")
    stale = db.upsert_page(conn, "https://x.edu/c/old", place_id=pid, visited=1, last_visit=old, title="rpc old")
    near = db.upsert_page(conn, "https://x.edu/c/l05.pdf", place_id=pid, kind="pdf")
    far = db.upsert_page(conn, "https://x.edu/c/deep", place_id=pid, title="rpc deep")
    db.add_link(conn, recent, near, "rpc slides")
    db.add_link(conn, near, far, "rpc deeper")          # two links away from a visited page
    db.add_link(conn, stale, far, "rpc again")          # linked only from a stale visit
    got = {r["page_id"] for r in retriever.retrieve(conn, "rpc")}
    assert got == {recent, near}


def test_description_has_host_and_untitled_pages_use_first_heading(conn):
    from jump import db
    pid = db.upsert_place(conn, "https://canvas.uw.edu", "/courses/", status="active", revisit=0.7, alias="courses")
    db.upsert_page(conn, "https://canvas.uw.edu/courses/1916633", place_id=pid, visited=1,
                   headings=["CSE M 553 A Au 26: Datacenter Systems", "Course Summary:"], snippet="Syllabus")
    r = retriever.retrieve(conn, "canvas datacenter")[0]
    assert r["label"] == "CSE M 553 A Au 26: Datacenter Systems"          # not "1916633"
    assert "canvas.uw.edu/courses/1916633" in r["description"]           # Jev can see it's Canvas
