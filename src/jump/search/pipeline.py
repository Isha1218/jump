"""Foreground search: retrieve -> Jev (optional) -> top RESULTS; record picks; open in Chrome."""
import sqlite3
import subprocess
import sys
import time
import webbrowser

import httpx

from .. import config
from . import jev, retriever

MIN_PROBABILITY = 0.1


def search(conn: sqlite3.Connection, query: str, use_jev: bool = True, api_key: str | None = None,
           client: httpx.Client | None = None) -> list[dict]:
    """Top results: dicts with page_id, url, label, kind, probability (None when Jev wasn't used or failed)."""
    cands = retriever.retrieve(conn, query)
    probs = None
    key = api_key or config.JEV_API_KEY
    if use_jev and key and len(cands) > 1:
        probs = jev.rank(query, cands, key, client=client)
    order = list(range(len(cands)))
    if probs:
        # pages you've opened before count extra, then hide anything still unlikely
        score = [p * (1 + c["picks"]) for p, c in zip(probs, cands)]
        order = sorted((i for i in order if score[i] >= MIN_PROBABILITY), key=lambda i: -score[i])  # stable
    return [{"page_id": cands[i]["page_id"], "url": cands[i]["url"], "label": cands[i]["label"],
             "kind": cands[i]["kind"], "probability": probs[i] if probs else None}
            for i in order[:config.RESULTS]]


def record_pick(conn: sqlite3.Connection, query: str, page_id: int) -> None:
    conn.execute("INSERT INTO picks(query, page_id, ts) VALUES (?, ?, ?)", (query, page_id, time.time()))


def evaluate(conn: sqlite3.Connection, use_jev: bool = True, api_key: str | None = None,
             client: httpx.Client | None = None) -> dict:
    """Replay every past pick's query, each with that one pick hidden (so it can't help itself).

    Returns {cases, top1, top5, misses: [{query, picked, got}]}; top1/top5 = share where the picked page ranked there.
    """
    picks = conn.execute("SELECT k.id, k.query, k.page_id, p.name, p.title FROM picks k JOIN pages p ON p.id = k.page_id "
                         "ORDER BY k.id").fetchall()
    top1 = top5 = 0
    misses = []
    for k in picks:
        conn.execute("SAVEPOINT eval")
        try:
            conn.execute("DELETE FROM picks WHERE id = ?", (k["id"],))
            got = search(conn, k["query"], use_jev=use_jev, api_key=api_key, client=client)
        finally:
            conn.execute("ROLLBACK TO eval")
            conn.execute("RELEASE eval")
        ids = [r["page_id"] for r in got]
        top1 += ids[:1] == [k["page_id"]]
        top5 += k["page_id"] in ids
        if ids[:1] != [k["page_id"]]:
            misses.append({"query": k["query"], "picked": k["name"] or k["title"],
                           "got": got[0]["label"] if got else None})
    n = len(picks)
    return {"cases": n, "top1": round(top1 / n, 2) if n else 0.0, "top5": round(top5 / n, 2) if n else 0.0,
            "misses": misses}


def open_in_browser(url: str) -> None:
    """Open in a new Chrome tab on macOS; otherwise (or if that fails) the default browser."""
    if sys.platform == "darwin":
        try:
            if subprocess.run(["open", "-a", "Google Chrome", url], check=False).returncode == 0:
                return
        except OSError:
            pass
    webbrowser.open_new_tab(url)
