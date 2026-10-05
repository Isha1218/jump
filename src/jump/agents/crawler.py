"""Crawler agent: drains `crawl` jobs. Each job is a page you're on (posted by the Monitor with the links read
from your tab); the crawler saves those links and fetches the pages they lead to, one hop, for their own text."""
import re
import sqlite3
import time

from .. import config, db, jobs, urls
from ..crawl import fetch as fetch_mod
from ..crawl.parse import parse

CONTENT_KINDS = {"application/pdf": "pdf"}
LOGIN_FORM = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.I)


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
            res = crawl_page(conn, p["url"], p["links"], fetch=fetch, delay=delay)
        except Exception as e:  # one bad page must not stop the worker
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            summary["failed"] += 1
            jobs.finish(conn, job["id"], error=f"{type(e).__name__}: {e}")
            continue
        summary["fetched"] += res["fetched"]
        summary["discovered"] += res["discovered"]
        jobs.finish(conn, job["id"])
    return summary


def crawl_page(conn: sqlite3.Connection, url: str, links: list, fetch=None, delay: float | None = None,
               budget: int | None = None) -> dict:
    """Save `links` ([target, anchor, context] read from your tab) from `url`, then fetch the same-site HTML pages
    they lead to (deeper ones first) that haven't been read or crawled lately. Returns {fetched, discovered}."""
    robots = fetch_mod.default_robots if fetch is None else fetch_mod.Robots(fetch)
    fetch = fetch or fetch_mod.fetch
    delay = config.CRAWL_DELAY_S if delay is None else delay
    budget = config.PAGE_BUDGET if budget is None else budget
    origin = urls.origin(url)
    place = conn.execute("SELECT pl.id, pl.origin, pl.path_prefix FROM pages p JOIN places pl ON pl.id = p.place_id "
                         "WHERE p.url = ?", (url,)).fetchone()
    now = time.time()

    targets: dict[str, tuple[str, str]] = {}
    for href, anchor, context in links:
        t = urls.normalize(href)
        if t and t != url and (t not in targets or (not targets[t][0] and anchor)):
            targets[t] = (anchor, context)

    discovered = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        from_id = db.upsert_page(conn, url, crawled_at=now)
        for t, (anchor, context) in targets.items():
            to_id = db.page_id(conn, t)
            if to_id is None:
                inside = place and urls.in_scope(t, place["origin"], place["path_prefix"])
                to_id = db.upsert_page(conn, t, kind=urls.kind_from_url(t), **({"place_id": place["id"]} if inside else {}))
                discovered += 1
            db.add_link(conn, from_id, to_id, anchor, context)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    def worth_fetching(t: str) -> bool:
        if urls.origin(t) != origin or urls.is_action_url(t) or urls.kind_from_url(t) != "html":
            return False
        r = conn.execute("SELECT visited, snippet, crawled_at FROM pages WHERE url = ?", (t,)).fetchone()
        if r and ((r["visited"] and r["snippet"]) or (r["crawled_at"] and now - r["crawled_at"] < config.REFRESH_AFTER_S)):
            return False               # its text is already read from your tab, or fresh
        return robots.allowed(t)

    order = sorted(targets, key=lambda t: urls.relation(url, t, origin, "/") != "deeper")   # stable: page order
    fetched = 0
    last_request = 0.0
    for t in order:
        if fetched >= budget:
            break
        if not worth_fetching(t):
            continue
        if (wait := last_request + delay - time.monotonic()) > 0:
            time.sleep(wait)
        res = fetch(t)
        last_request = time.monotonic()
        fetched += 1
        _record(conn, t, res)
    return {"fetched": fetched, "discovered": discovered}


def _record(conn, url, res) -> None:
    """Store one fetched page's own text (its links aren't followed)."""
    page = parse(res.text, res.final_url or url) if res.status == 200 and res.is_html and res.text is not None else None
    status = res.status
    if page and LOGIN_FORM.search(res.text):
        page, status = None, 401        # a sign-in page stands in for the real one: keep nothing from it
    fields = {"crawled_at": time.time(), "fetch_status": status}
    if page:
        # text read from your own tab (logged in, rendered) beats what a crawler sees
        read = conn.execute("SELECT 1 FROM pages WHERE url = ? AND visited = 1 AND COALESCE(snippet, '') != ''",
                            (url,)).fetchone()
        fields.update({"kind": "html"} if read else
                      {"kind": "html", "title": page.title, "snippet": page.snippet, "headings": page.headings})
    elif status == 200 and (ctype := res.content_type.split(";")[0].strip().lower()):
        fields["kind"] = CONTENT_KINDS.get(ctype, "file")
    db.upsert_page(conn, url, **fields)
