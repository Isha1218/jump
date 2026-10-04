"""Runs the background agents: Monitor (active Chrome tab) → Watcher → Planner every few minutes → Crawler workers.

Agents only share the database; this module just decides *when* each one runs.
"""
import logging
import signal
import sqlite3
import threading

from . import db, jobs
from .monitor import Monitor

CYCLE_S = 300            # regroup/rescore places and plan crawls this often
WORKER_IDLE_S = 5        # crawler poll interval when the queue is empty
CRAWL_WORKERS = 2

log = logging.getLogger("jump.daemon")


def db_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def watch_cycle(conn: sqlite3.Connection) -> dict:
    """One Watcher run, then the Planner and the Namer."""
    from .agents import namer, planner, watcher

    return {"watcher": watcher.run_once(conn), "planner": planner.run_once(conn), "namer": namer.run_once(conn)}


def crawl_worker(path: str, stop: threading.Event, idle_s: float | None = None) -> None:
    """Drain `crawl` jobs one at a time with its own connection until `stop` is set."""
    from .agents import crawler

    idle_s = WORKER_IDLE_S if idle_s is None else idle_s
    conn = db.connect(path)
    while not stop.is_set():
        if not conn.execute("SELECT 1 FROM jobs WHERE type = 'crawl' AND status = 'pending' LIMIT 1").fetchone():
            stop.wait(idle_s)
            continue
        try:
            log.info("crawl %s", crawler.run_pending(conn, max_jobs=1))
        except Exception:
            log.exception("crawl worker error")
            stop.wait(idle_s)
    conn.close()


def monitor_worker(path: str, stop: threading.Event, chrome=None, poll_s: float | None = None) -> None:
    conn = db.connect(path)
    kwargs = {} if poll_s is None else {"poll_s": poll_s}
    Monitor(conn, chrome=chrome, log=log.info).run(stop, **kwargs)
    conn.close()


def run(conn: sqlite3.Connection, stop: threading.Event | None = None, workers: int = CRAWL_WORKERS,
        cycle_s: float = CYCLE_S, chrome=None, poll_s: float | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())

    requeued = jobs.requeue_stale(conn)
    if requeued:
        log.info("requeued %d stale jobs", requeued)

    path = db_path(conn)
    threads = [threading.Thread(target=monitor_worker, args=(path, stop, chrome, poll_s), daemon=True, name="monitor")]
    threads += [threading.Thread(target=crawl_worker, args=(path, stop), daemon=True, name=f"crawler-{i}")
                for i in range(workers)]
    for t in threads:
        t.start()
    log.info("daemon started: monitoring Chrome, %d crawl workers", workers)

    try:
        while not stop.is_set():
            try:
                log.info("cycle %s", watch_cycle(conn))
            except Exception:
                log.exception("watch cycle failed")
            stop.wait(cycle_s)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
        log.info("daemon stopped")
