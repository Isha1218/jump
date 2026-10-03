"""Runs the background agents: Watcher (on History changes) → Planner → Crawler workers.

Agents only share the database; this module just decides *when* each one runs.
"""
import logging
import signal
import sqlite3
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config, db, jobs

DEBOUNCE_S = 30          # wait for a burst of History writes to settle
FALLBACK_S = 600         # run the watcher at least this often anyway
WORKER_IDLE_S = 5        # crawler poll interval when the queue is empty
CRAWL_WORKERS = 2

log = logging.getLogger("jump.daemon")


class Debouncer:
    """Due when changes have been quiet for `debounce_s`, or `fallback_s` passed since the last run."""

    def __init__(self, debounce_s: float = DEBOUNCE_S, fallback_s: float = FALLBACK_S, now: float | None = None):
        self.debounce_s, self.fallback_s = debounce_s, fallback_s
        self.last_change: float | None = None
        self.last_run = now if now is not None else time.time()

    def touch(self, now: float) -> None:
        self.last_change = now

    def due(self, now: float) -> bool:
        if self.last_change is not None and now - self.last_change >= self.debounce_s:
            return True
        return now - self.last_run >= self.fallback_s

    def ran(self, started: float) -> None:
        """Mark a run that began at `started`; changes that arrived during the run stay pending."""
        self.last_run = started
        if self.last_change is not None and self.last_change <= started:
            self.last_change = None


class _HistoryHandler(FileSystemEventHandler):
    def __init__(self, names: set[str], debouncer: Debouncer, lock: threading.Lock):
        self.names, self.debouncer, self.lock = names, debouncer, lock

    def on_any_event(self, event):
        if Path(event.src_path).name in self.names:
            with self.lock:
                self.debouncer.touch(time.time())


def db_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def watch_cycle(conn: sqlite3.Connection, history_path: Path | None = None) -> dict:
    """One Watcher run followed by one Planner run."""
    from .agents import planner, watcher

    return {"watcher": watcher.run_once(conn, history_path=history_path), "planner": planner.run_once(conn)}


def crawl_worker(path: str, stop: threading.Event, idle_s: float = WORKER_IDLE_S) -> None:
    """Drain `crawl` jobs one at a time with its own connection until `stop` is set."""
    from .agents import crawler

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


def run(conn: sqlite3.Connection, history_path: Path | None = None, stop: threading.Event | None = None,
        workers: int = CRAWL_WORKERS, debouncer: Debouncer | None = None, tick_s: float = 1.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    history_path = Path(history_path or config.CHROME_HISTORY)
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())

    requeued = jobs.requeue_stale(conn)
    if requeued:
        log.info("requeued %d stale jobs", requeued)

    debouncer = debouncer or Debouncer()
    lock = threading.Lock()
    observer = None
    if history_path.parent.exists():
        observer = Observer()
        names = {history_path.name, history_path.name + "-journal", history_path.name + "-wal"}
        observer.schedule(_HistoryHandler(names, debouncer, lock), str(history_path.parent), recursive=False)
        observer.start()
    else:
        log.warning("history folder %s not found; relying on the fallback timer", history_path.parent)

    threads = [threading.Thread(target=crawl_worker, args=(db_path(conn), stop), daemon=True, name=f"crawler-{i}")
               for i in range(workers)]
    for t in threads:
        t.start()
    log.info("daemon started: %d crawl workers, watching %s", workers, history_path)

    first = True
    try:
        while not stop.is_set():
            with lock:
                due = first or debouncer.due(time.time())
            if due:
                first = False
                started = time.time()
                try:
                    log.info("cycle %s", watch_cycle(conn, history_path))
                except Exception:
                    log.exception("watch cycle failed")
                with lock:
                    debouncer.ran(started)
            stop.wait(tick_s)
    finally:
        stop.set()
        if observer:
            observer.stop()
            observer.join()
        for t in threads:
            t.join(timeout=10)
        log.info("daemon stopped")
