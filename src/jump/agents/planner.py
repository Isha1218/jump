"""Planner agent: turns revisit scores into crawl budgets and queues `crawl` jobs."""
import sqlite3
import time

from .. import config, jobs

CRAWLED = ("active", "probation")   # probation too, so a place gets crawled before it has days of history


def run_once(conn: sqlite3.Connection, now: float | None = None) -> dict:
    """Consume `rescored` jobs, set every place's budget, queue crawls for stale active/probation places."""
    now = time.time() if now is None else now
    consumed = 0
    while (job := jobs.claim(conn, ["rescored"])):
        jobs.finish(conn, job["id"])
        consumed += 1

    busy = {r[0] for r in conn.execute(
        "SELECT json_extract(payload, '$.place_id') FROM jobs WHERE type = 'crawl' AND status IN ('pending', 'running')")}
    posted = budgets = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for p in conn.execute("SELECT id, status, revisit, budget, last_crawled FROM places").fetchall():
            budget = round(config.MAX_BUDGET * p["revisit"]) if p["status"] in CRAWLED else 0
            if budget != p["budget"]:
                conn.execute("UPDATE places SET budget = ? WHERE id = ?", (budget, p["id"]))
                budgets += 1
            stale = p["last_crawled"] is None or now - p["last_crawled"] > config.REFRESH_AFTER_S
            if budget > 0 and stale and p["id"] not in busy:
                if jobs.post(conn, "crawl", {"place_id": p["id"], "budget": budget}, dedupe_key=str(p["id"])):
                    posted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"rescored_consumed": consumed, "budgets_changed": budgets, "crawl_jobs_posted": posted}
