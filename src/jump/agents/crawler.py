"""Crawler agent: drains `crawl` jobs, best-first crawling each place within its budget."""
import sqlite3
import time

from .. import config, db, jobs, urls
from ..crawl import fetch as fetch_mod
from ..crawl.parse import parse
from ..crawl.priority import Frontier, Patterns, link_base

CONTENT_KINDS = {"application/pdf": "pdf"}


def run_pending(conn: sqlite3.Connection, max_jobs: int | None = None, fetch=None, delay: float | None = None) -> dict:
    """Claim and run `crawl` jobs one at a time until none are left (or `max_jobs`)."""
    summary = {"jobs": 0, "failed": 0, "fetched": 0, "discovered": 0}
    while max_jobs is None or summary["jobs"] < max_jobs:
        job = jobs.claim(conn, ["crawl"])
        if not job:
            break
        summary["jobs"] += 1
        p = job["payload"]
        try:
            res = crawl_place(conn, p["place_id"], p.get("budget", config.MAX_BUDGET), fetch=fetch, delay=delay)
        except Exception as e:  # one bad place must not stop the worker
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            summary["failed"] += 1
            jobs.finish(conn, job["id"], error=f"{type(e).__name__}: {e}")
            continue
        summary["fetched"] += res["fetched"]
        summary["discovered"] += res["discovered"]
        jobs.finish(conn, job["id"])
    return summary


def crawl_place(conn: sqlite3.Connection, place_id: int, budget: int, fetch=None, delay: float | None = None) -> dict:
    """Best-first crawl of one place. Returns {fetched, discovered, stopped_reason}."""
    place = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if place is None:
        raise ValueError(f"no place {place_id}")
    origin, prefix = place["origin"], place["path_prefix"]
    robots = fetch_mod.default_robots if fetch is None else fetch_mod.Robots(fetch)
    fetch = fetch or fetch_mod.fetch
    delay = config.CRAWL_DELAY_S if delay is None else delay

    visited = conn.execute("SELECT url, revisit FROM pages WHERE place_id = ? AND visited = 1 ORDER BY revisit DESC",
                           (place_id,)).fetchall()
    pattern = Patterns([v["url"] for v in visited])

    def fetchable(url: str) -> bool:
        return (urls.in_scope(url, origin, prefix) and not urls.is_action_url(url)
                and urls.kind_from_url(url) == "html" and robots.allowed(url))

    frontier = Frontier()
    seeds = [(v["url"], v["revisit"] or place["revisit"]) for v in visited] or [(origin + prefix, place["revisit"])]
    for url, prio in seeds:
        if fetchable(url):
            frontier.add_seed(url, prio)

    fetched = discovered = 0
    last_request = 0.0
    while True:
        best = frontier.pop()
        if best is None:
            reason = "frontier_empty"
            break
        url, prio = best
        if prio < config.MIN_PRIORITY:
            reason = "low_priority"
            break
        if fetched >= budget:
            reason = "budget"
            break
        if (wait := last_request + delay - time.monotonic()) > 0:
            time.sleep(wait)
        res = fetch(url)
        last_request = time.monotonic()
        fetched += 1
        discovered += _record(conn, place_id, url, res, prio, frontier, fetchable, pattern, origin, prefix)

    conn.execute("UPDATE places SET last_crawled = ? WHERE id = ?", (time.time(), place_id))
    return {"fetched": fetched, "discovered": discovered, "stopped_reason": reason}


def _record(conn, place_id, url, res, prio, frontier, fetchable, pattern, origin, prefix) -> int:
    """Write one fetched page and its outgoing links (one transaction); feed new links to the frontier."""
    final = res.final_url or url
    if final != url:
        frontier.mark_done(final)
    page = parse(res.text, final) if res.status == 200 and res.is_html and res.text is not None else None
    now = time.time()
    new = 0
    links = []  # (target, anchor, context, in_scope, fetchable) -- robots lookups happen outside the transaction
    for t, a, c in (page.links if page else []):
        if t not in (url, final):
            inside = urls.in_scope(t, origin, prefix)
            links.append((t, a, c, inside, inside and fetchable(t)))
    conn.execute("BEGIN IMMEDIATE")
    try:
        if page is None:
            ctype = res.content_type.split(";")[0].strip().lower()
            fields = {"kind": CONTENT_KINDS.get(ctype, "file")} if res.status == 200 and ctype else {}
            db.upsert_page(conn, url, place_id=place_id, crawled_at=now, fetch_status=res.status, **fields)
            conn.execute("COMMIT")
            return 0
        from_id = db.upsert_page(conn, url, place_id=place_id, title=page.title, snippet=page.snippet,
                                 headings=page.headings, kind="html", crawled_at=now, fetch_status=res.status)
        frontier.observe({t for t, _, _, _, _ in links})
        for target, anchor, context, inside, ok in links:
            to_id = db.page_id(conn, target)
            if to_id is None:
                fields = {"place_id": place_id} if inside else {}
                to_id = db.upsert_page(conn, target, kind=urls.kind_from_url(target), **fields)
                new += 1
            elif inside:
                conn.execute("UPDATE pages SET place_id = ? WHERE id = ? AND place_id IS NULL", (place_id, to_id))
            db.add_link(conn, from_id, to_id, anchor, context)
            if ok:
                frontier.offer(target, link_base(prio, urls.relation(final, target, origin, prefix), pattern(target)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return new
