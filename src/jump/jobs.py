"""Job queue on the `jobs` table: how agents coordinate without calling each other."""
import json
import sqlite3
import time


def post(conn: sqlite3.Connection, type_: str, payload: dict, dedupe_key: str | None = None) -> int | None:
    """Queue a job. With `dedupe_key`, skip if a pending job with the same type+key exists."""
    if dedupe_key is not None:
        payload = {**payload, "_key": dedupe_key}
        dup = conn.execute(
            "SELECT id FROM jobs WHERE type = ? AND status = 'pending' AND json_extract(payload, '$._key') = ?",
            (type_, dedupe_key),
        ).fetchone()
        if dup:
            return None
    row = conn.execute(
        "INSERT INTO jobs(type, payload, created_at) VALUES (?, ?, ?) RETURNING id",
        (type_, json.dumps(payload), time.time()),
    ).fetchone()
    return row["id"]


def claim(conn: sqlite3.Connection, types: list[str]) -> dict | None:
    """Atomically take the oldest pending job of the given types. Returns {id, type, payload} or None."""
    marks = ",".join("?" * len(types))
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            f"UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 "
            f"WHERE id = (SELECT id FROM jobs WHERE status = 'pending' AND type IN ({marks}) ORDER BY id LIMIT 1) "
            f"RETURNING id, type, payload",
            (time.time(), *types),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if not row:
        return None
    return {"id": row["id"], "type": row["type"], "payload": json.loads(row["payload"])}


def finish(conn: sqlite3.Connection, job_id: int, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        ("failed" if error else "done", error, time.time(), job_id),
    )


def requeue_stale(conn: sqlite3.Connection, older_than_s: float = 3600) -> int:
    """Return jobs stuck in `running` (e.g. after a crash) to `pending`."""
    cur = conn.execute(
        "UPDATE jobs SET status = 'pending' WHERE status = 'running' AND started_at < ?",
        (time.time() - older_than_s,),
    )
    return cur.rowcount
