"""Watcher agent: Chrome history -> visits -> hubs, places, revisit scores -> `rescored` jobs."""
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from .. import config, db, history, jobs, places, scoring, urls


def _status(revisit: float, last_visit: float, now: float) -> str:
    if revisit >= config.ACTIVE_THRESHOLD:
        return "active"
    if revisit >= config.PROBATION_THRESHOLD and now - last_visit <= config.PROBATION_DAYS * 86400:
        return "probation"
    return "dropped"


def _hub_places(hub_urls: list[str], taken: set[str]) -> list[places.Place]:
    """One place per origin+directory holding only hub URLs, unless a regular place owns that scope."""
    by_scope: dict[tuple[str, str], list[str]] = defaultdict(list)
    for u in hub_urls:
        by_scope[(urls.origin(u), places.directory(u))].append(u)
    return [places.Place(o, p, sorted(us), places.alias(o, p))
            for (o, p), us in by_scope.items() if o + p not in taken]


def _rescore(conn, place_id: int, prev, revisit: float, status: str) -> int:
    """Post a `rescored` job if the place is new, its status changed or its score moved enough."""
    if prev is not None and prev["status"] == status and abs(revisit - prev["revisit"]) < config.RESCORE_DELTA:
        return 0
    return int(jobs.post(conn, "rescored", {"place_id": place_id}, dedupe_key=str(place_id)) is not None)


def run_once(conn: sqlite3.Connection, history_path: Path | str | None = None, now: float | None = None) -> dict:
    """Import new Chrome visits, regroup and rescore places, upsert visited pages. One transaction."""
    now = time.time() if now is None else now
    path = Path(history_path) if history_path else config.CHROME_HISTORY
    conn.execute("BEGIN IMMEDIATE")
    try:
        summary = _run(conn, path, now)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return summary


def _run(conn: sqlite3.Connection, path: Path, now: float) -> dict:
    new, titles = history.import_visits(conn, path, now)
    visits = [dict(r) for r in conn.execute(
        "SELECT url, ts, transition, duration_s, from_url FROM visits WHERE ts >= ?",
        (now - config.HISTORY_WINDOW_DAYS * 86400,))]
    hubs = places.find_hubs(visits)

    def is_hub(u: str) -> bool:
        return places.path_key(u) in hubs

    by_url: dict[str, list[dict]] = defaultdict(list)
    for v in visits:
        by_url[v["url"]].append(v)
    regular = places.group({u: {scoring.day_of(v["ts"]) for v in vs} for u, vs in by_url.items() if not is_hub(u)})
    hub_only = _hub_places([u for u in by_url if is_hub(u)], {p.scope for p in regular})

    old = {r["scope"]: r for r in conn.execute("SELECT id, scope, revisit, status, first_seen FROM places")}
    old_pages = {r["url"]: tuple(r)[1:] for r in conn.execute(
        "SELECT url, place_id, visit_count, last_visit, revisit FROM pages WHERE visited = 1")}
    counts: Counter = Counter()
    for i, place in enumerate(regular + hub_only):
        hub_place = i >= len(regular)
        vs = [v for u in place.urls for v in by_url[u]]
        members = set(place.urls)
        feats, revisit = scoring.score_place(vs, now, inside=members.__contains__, from_hub=is_hub)
        revisit = round(revisit, 4)
        first, last = min(v["ts"] for v in vs), max(v["ts"] for v in vs)
        prev = old.get(place.scope)
        if prev is not None and prev["first_seen"]:
            first = min(first, prev["first_seen"])
        status = "hub" if hub_place else _status(revisit, last, now)
        pid = db.upsert_place(conn, place.origin, place.path_prefix, alias=place.alias, revisit=revisit,
                              features=feats, first_seen=first, last_visit=last, status=status)
        counts[status] += 1
        counts["rescored_jobs"] += _rescore(conn, pid, prev, revisit, status)
        if hub_place:
            continue
        for u in place.urls:
            _, page_rev = scoring.score_page(by_url[u], now, from_hub=is_hub)
            fields = {"place_id": pid, "visit_count": len(by_url[u]),
                      "last_visit": max(v["ts"] for v in by_url[u]), "revisit": round(page_rev, 4)}
            if old_pages.get(u) == tuple(fields.values()) and u not in titles:
                continue  # unchanged: don't mark it dirty for re-indexing
            if u in titles:
                fields["title"] = titles[u]
            db.upsert_page(conn, u, visited=1, kind=urls.kind_from_url(u), **fields)
            counts["pages_updated"] += 1

    seen = {p.scope for p in regular + hub_only}
    for scope, prev in old.items():  # places that no longer exist after regrouping
        if scope not in seen and (prev["status"] != "dropped" or prev["revisit"] != 0):
            conn.execute("UPDATE places SET status = 'dropped', revisit = 0, updated_at = ? WHERE id = ?",
                         (now, prev["id"]))
            counts["retired"] += 1
            counts["rescored_jobs"] += _rescore(conn, prev["id"], prev, 0.0, "dropped")
    return {
        "new_visits": new, "visits": len(visits), "places": len(regular),
        "active": counts["active"], "probation": counts["probation"], "dropped": counts["dropped"],
        "hubs": len(hub_only), "hub_urls": len(hubs), "pages_updated": counts["pages_updated"],
        "retired": counts["retired"], "rescored_jobs": counts["rescored_jobs"],
    }
