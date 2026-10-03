"""Incremental import of Chrome history into the `visits` table.

Chrome locks its History file while running, so we copy it (plus `History-journal`) to a temp dir
and open the copy read-only. Times are microseconds since 1601-01-01 UTC.
"""
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import config, db, urls

CHROME_EPOCH_OFFSET_S = 11644473600
SUBFRAMES = {3, 4}                      # AUTO_SUBFRAME, MANUAL_SUBFRAME
CHAIN_START, CHAIN_END = 0x10000000, 0x20000000
REDIRECTS = 0x40000000 | 0x80000000     # CLIENT_REDIRECT | SERVER_REDIRECT


def to_unix(chrome_us: int) -> float:
    return chrome_us / 1e6 - CHROME_EPOCH_OFFSET_S


def to_chrome(unix_s: float) -> int:
    return int((unix_s + CHROME_EPOCH_OFFSET_S) * 1e6)


@contextmanager
def open_copy(path: Path):
    """Read-only connection to a temp copy of a (possibly locked) Chrome History file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Chrome history not found: {path}")
    with tempfile.TemporaryDirectory(prefix="jump-history-") as tmp:
        dst = Path(tmp) / "History"
        shutil.copy2(path, dst)
        journal = path.with_name(path.name + "-journal")
        if journal.exists():
            shutil.copy2(journal, Path(tmp) / "History-journal")
        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _is_redirect_hop(transition: int) -> bool:
    """Intermediate hop of a redirect chain (the user only 'visited' the chain end)."""
    return not transition & CHAIN_END and bool(transition & (CHAIN_START | REDIRECTS))


def _referrer(hc: sqlite3.Connection, from_visit: int) -> str | None:
    """URL the user came from, walking back over redirect hops to the page that started the chain."""
    for _ in range(20):
        row = hc.execute("SELECT v.from_visit, v.transition, u.url FROM visits v JOIN urls u ON u.id = v.url "
                         "WHERE v.id = ?", (from_visit,)).fetchone()
        if not row:
            return None
        if _is_redirect_hop(row["transition"]) and row["from_visit"]:
            from_visit = row["from_visit"]
            continue
        return urls.normalize(row["url"])
    return None


def import_visits(conn: sqlite3.Connection, history_path: Path, now: float) -> tuple[int, dict[str, str]]:
    """Copy Chrome visits newer than `meta['history_last_id']` (and inside the window) into `visits`.

    Returns (number imported, {normalized url: title}) for the imported visits.
    """
    last_id = int(db.get_meta(conn, "history_last_id", "0"))
    cutoff = to_chrome(now - config.HISTORY_WINDOW_DAYS * 86400)
    titles: dict[str, str] = {}
    count = 0
    with open_copy(history_path) as hc:
        max_id = hc.execute("SELECT COALESCE(MAX(id), 0) FROM visits").fetchone()[0]
        if max_id < last_id:  # history was cleared and ids restarted
            last_id = 0
        rows = hc.execute(
            "SELECT v.id, v.visit_time, v.from_visit, v.transition, v.visit_duration, "
            "u.url, u.title, u.typed_count FROM visits v JOIN urls u ON u.id = v.url "
            "WHERE v.id > ? AND v.visit_time >= ? ORDER BY v.id",
            (last_id, cutoff),
        ).fetchall()
        for r in rows:
            core = r["transition"] & 0xFF
            if core in SUBFRAMES or _is_redirect_hop(r["transition"]):
                continue
            url = urls.normalize(r["url"])
            if not url:
                continue
            from_url = _referrer(hc, r["from_visit"]) if r["from_visit"] else None
            conn.execute(
                "INSERT OR REPLACE INTO visits(chrome_id, url, ts, transition, duration_s, from_url, typed_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["id"], url, to_unix(r["visit_time"]), core, (r["visit_duration"] or 0) / 1e6, from_url,
                 r["typed_count"] or 0),
            )
            if r["title"]:
                titles[url] = r["title"]
            count += 1
    db.set_meta(conn, "history_last_id", str(max(max_id, last_id)))
    return count, titles
