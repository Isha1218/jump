"""Builds a synthetic Chrome History SQLite file (real Chrome schema subset) for tests."""
import sqlite3
from pathlib import Path

from jump.history import to_chrome

SCHEMA = """
CREATE TABLE urls(id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR, visit_count INTEGER DEFAULT 0,
  typed_count INTEGER DEFAULT 0, last_visit_time INTEGER, hidden INTEGER DEFAULT 0);
CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER NOT NULL, visit_time INTEGER NOT NULL,
  from_visit INTEGER, transition INTEGER DEFAULT 0, segment_id INTEGER, visit_duration INTEGER DEFAULT 0,
  opener_visit INTEGER DEFAULT 0);
"""


class FakeHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.executescript(SCHEMA)

    def visit(self, url: str, ts: float, transition: int = 0, duration_s: float = 60, from_visit: int = 0,
              title: str | None = None, opener_visit: int = 0) -> int:
        """Record a visit at unix time `ts`; returns the Chrome visit id."""
        row = self.conn.execute("SELECT id FROM urls WHERE url = ?", (url,)).fetchone()
        if row:
            uid = row[0]
        else:
            uid = self.conn.execute("INSERT INTO urls(url, title) VALUES (?, ?)", (url, title)).lastrowid
        typed = int((transition & 0xFF) == 1)
        self.conn.execute("UPDATE urls SET visit_count = visit_count + 1, typed_count = typed_count + ?, "
                          "last_visit_time = ? WHERE id = ?", (typed, to_chrome(ts), uid))
        return self.conn.execute(
            "INSERT INTO visits(url, visit_time, from_visit, transition, visit_duration, opener_visit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, to_chrome(ts), from_visit, transition, int(duration_s * 1e6), opener_visit),
        ).lastrowid
