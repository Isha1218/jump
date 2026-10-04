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
        # pages you've opened before count extra; hide anything Jev considers unlikely
        order = sorted((i for i in order if probs[i] >= MIN_PROBABILITY),
                       key=lambda i: -probs[i] * (1 + cands[i]["picks"]))   # stable: ties keep retriever order
    return [{"page_id": cands[i]["page_id"], "url": cands[i]["url"], "label": cands[i]["label"],
             "kind": cands[i]["kind"], "probability": probs[i] if probs else None}
            for i in order[:config.RESULTS]]


def record_pick(conn: sqlite3.Connection, query: str, page_id: int) -> None:
    conn.execute("INSERT INTO picks(query, page_id, ts) VALUES (?, ?, ?)", (query, page_id, time.time()))


def open_in_browser(url: str) -> None:
    """Open in a new Chrome tab on macOS; otherwise (or if that fails) the default browser."""
    if sys.platform == "darwin":
        try:
            if subprocess.run(["open", "-a", "Google Chrome", url], check=False).returncode == 0:
                return
        except OSError:
            pass
    webbrowser.open_new_tab(url)
