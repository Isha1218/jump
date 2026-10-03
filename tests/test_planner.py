from jump import config, db, jobs
from jump.agents import planner

NOW = 1_800_000_000.0


def places(conn):
    return {
        "active": db.upsert_place(conn, "https://a.edu", "/c/", revisit=0.8, status="active"),
        "probation": db.upsert_place(conn, "https://b.edu", "/c/", revisit=0.3, status="probation"),
        "hub": db.upsert_place(conn, "https://google.com", "/", revisit=0.9, status="hub"),
        "fresh": db.upsert_place(conn, "https://c.edu", "/c/", revisit=0.6, status="active", last_crawled=NOW - 60),
    }


def crawl_jobs(conn, status="pending"):
    return list(conn.execute(
        "SELECT json_extract(payload, '$.place_id') AS pid, json_extract(payload, '$.budget') AS budget "
        "FROM jobs WHERE type = 'crawl' AND status = ? ORDER BY id", (status,)))


def test_budgets_and_jobs(conn):
    ids = places(conn)
    jobs.post(conn, "rescored", {"place_id": ids["active"]}, dedupe_key=str(ids["active"]))
    jobs.post(conn, "rescored", {"place_id": ids["probation"]}, dedupe_key=str(ids["probation"]))
    res = planner.run_once(conn, now=NOW)
    assert res == {"rescored_consumed": 2, "budgets_changed": 2, "crawl_jobs_posted": 1}
    budget = dict(conn.execute("SELECT id, budget FROM places").fetchall())
    assert budget[ids["active"]] == round(config.MAX_BUDGET * 0.8)
    assert budget[ids["fresh"]] == round(config.MAX_BUDGET * 0.6)
    assert budget[ids["probation"]] == budget[ids["hub"]] == 0
    assert [(r["pid"], r["budget"]) for r in crawl_jobs(conn)] == [(ids["active"], budget[ids["active"]])]
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'rescored' AND status != 'done'").fetchone()[0] == 0


def test_dedupe_pending_and_running(conn):
    ids = places(conn)
    planner.run_once(conn, now=NOW)
    assert planner.run_once(conn, now=NOW)["crawl_jobs_posted"] == 0     # already pending
    jobs.claim(conn, ["crawl"])
    assert planner.run_once(conn, now=NOW)["crawl_jobs_posted"] == 0     # running
    assert len(crawl_jobs(conn, "running")) == 1 and not crawl_jobs(conn)


def test_refresh_after(conn):
    ids = places(conn)
    conn.execute("UPDATE places SET status = 'dropped' WHERE id != ?", (ids["fresh"],))
    assert planner.run_once(conn, now=NOW)["crawl_jobs_posted"] == 0
    later = NOW - 60 + config.REFRESH_AFTER_S + 1
    assert planner.run_once(conn, now=later)["crawl_jobs_posted"] == 1
    assert [r["pid"] for r in crawl_jobs(conn)] == [ids["fresh"]]


def test_demotion_zeroes_budget(conn):
    ids = places(conn)
    planner.run_once(conn, now=NOW)
    conn.execute("UPDATE places SET status = 'dropped' WHERE id = ?", (ids["active"],))
    res = planner.run_once(conn, now=NOW)
    assert res["budgets_changed"] == 1
    assert conn.execute("SELECT budget FROM places WHERE id = ?", (ids["active"],)).fetchone()[0] == 0
