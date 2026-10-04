"""SQLite "blackboard" shared by every agent. WAL mode so readers never block writers."""
import json
import sqlite3
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
  id INTEGER PRIMARY KEY,
  scope TEXT UNIQUE NOT NULL,          -- origin + path_prefix
  origin TEXT NOT NULL,
  path_prefix TEXT NOT NULL,           -- always ends with '/'
  alias TEXT,
  revisit REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'probation',  -- probation | active | dropped | hub
  budget INTEGER NOT NULL DEFAULT 0,
  features TEXT,                       -- JSON of the inputs behind `revisit`
  first_seen REAL,
  last_visit REAL,
  last_crawled REAL,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  place_id INTEGER REFERENCES places(id),
  title TEXT,
  snippet TEXT,                        -- title + headings + first ~300 chars of text
  headings TEXT,                       -- JSON list
  kind TEXT NOT NULL DEFAULT 'html',   -- html | pdf | slides | video | file
  visited INTEGER NOT NULL DEFAULT 0,
  visit_count INTEGER NOT NULL DEFAULT 0,
  last_visit REAL,
  revisit REAL NOT NULL DEFAULT 0,
  crawled_at REAL,
  fetch_status INTEGER,                -- HTTP status of last fetch, NULL if never fetched
  fts_dirty INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS pages_place ON pages(place_id);
CREATE INDEX IF NOT EXISTS pages_dirty ON pages(fts_dirty) WHERE fts_dirty = 1;

CREATE TABLE IF NOT EXISTS links (
  from_id INTEGER NOT NULL REFERENCES pages(id),
  to_id INTEGER NOT NULL REFERENCES pages(id),
  anchor TEXT,
  context TEXT,                        -- text of the enclosing row / list item
  PRIMARY KEY (from_id, to_id)
);
CREATE INDEX IF NOT EXISTS links_to ON links(to_id);

-- Visits recorded by the monitor (one row per time you looked at a page).
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  ts REAL NOT NULL,                    -- unix seconds
  duration_s REAL NOT NULL DEFAULT 0,
  from_url TEXT                        -- previous page in the same tab, if any
);
CREATE INDEX IF NOT EXISTS visits_url ON visits(url);
CREATE INDEX IF NOT EXISTS visits_ts ON visits(ts);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,                  -- rescored | crawl
  payload TEXT NOT NULL,               -- JSON
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, type);

CREATE TABLE IF NOT EXISTS picks (
  id INTEGER PRIMARY KEY,
  query TEXT NOT NULL,
  page_id INTEGER NOT NULL REFERENCES pages(id),
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS picks_page ON picks(page_id);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the database. One connection per thread."""
    path = Path(path) if path else config.DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def upsert_page(conn: sqlite3.Connection, url: str, **fields) -> int:
    """Insert or update a page by URL; returns its id. Marks it for FTS re-indexing."""
    if "headings" in fields and isinstance(fields["headings"], list):
        fields["headings"] = json.dumps(fields["headings"])
    fields["fts_dirty"] = 1
    cols = ", ".join(["url", *fields])
    marks = ", ".join("?" * (len(fields) + 1))
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    row = conn.execute(
        f"INSERT INTO pages ({cols}) VALUES ({marks}) ON CONFLICT(url) DO UPDATE SET {updates} RETURNING id",
        (url, *fields.values()),
    ).fetchone()
    return row["id"]


def page_id(conn: sqlite3.Connection, url: str) -> int | None:
    row = conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
    return row["id"] if row else None


def add_link(conn: sqlite3.Connection, from_id: int, to_id: int, anchor: str = "", context: str = "") -> None:
    """Record a link; the target's searchable text changes, so mark it dirty."""
    conn.execute(
        "INSERT INTO links(from_id, to_id, anchor, context) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(from_id, to_id) DO UPDATE SET anchor = excluded.anchor, context = excluded.context",
        (from_id, to_id, anchor, context),
    )
    conn.execute("UPDATE pages SET fts_dirty = 1 WHERE id = ?", (to_id,))


def upsert_place(conn: sqlite3.Connection, origin: str, path_prefix: str, **fields) -> int:
    scope = origin + path_prefix
    fields.setdefault("updated_at", time.time())
    if "features" in fields and isinstance(fields["features"], dict):
        fields["features"] = json.dumps(fields["features"])
    cols = ", ".join(["scope", "origin", "path_prefix", *fields])
    marks = ", ".join("?" * (len(fields) + 3))
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields) or "scope = scope"
    row = conn.execute(
        f"INSERT INTO places ({cols}) VALUES ({marks}) ON CONFLICT(scope) DO UPDATE SET {updates} RETURNING id",
        (scope, origin, path_prefix, *fields.values()),
    ).fetchone()
    return row["id"]
