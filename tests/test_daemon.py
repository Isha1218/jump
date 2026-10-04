import plistlib
import sys
import threading
import time
import types

import jump.agents
from jump import daemon, db, jobs, service


def _stub_agents(monkeypatch, calls):
    watcher = types.ModuleType("jump.agents.watcher")
    planner = types.ModuleType("jump.agents.planner")
    crawler = types.ModuleType("jump.agents.crawler")

    def w_run(conn, now=None):
        calls.append("watch")
        return {}

    def p_run(conn, now=None):
        calls.append("plan")
        jobs.post(conn, "crawl", {"place_id": 1, "budget": 3}, dedupe_key="1")
        return {}

    def c_run(conn, max_jobs=None, fetch=None, delay=None):
        job = jobs.claim(conn, ["crawl"])
        if job:
            calls.append("crawl")
            jobs.finish(conn, job["id"])
        return {"jobs": int(bool(job))}

    watcher.run_once, planner.run_once, crawler.run_pending = w_run, p_run, c_run
    for name, mod in [("watcher", watcher), ("planner", planner), ("crawler", crawler)]:
        monkeypatch.setitem(sys.modules, f"jump.agents.{name}", mod)
        monkeypatch.setattr(jump.agents, name, mod, raising=False)


class NoChrome:
    def active_tab(self):
        return None


def test_run_cycles_agents_and_drains_crawl_jobs(tmp_path, monkeypatch):
    calls = []
    _stub_agents(monkeypatch, calls)
    conn = db.connect(tmp_path / "g.db")
    monkeypatch.setattr(daemon, "WORKER_IDLE_S", 0.05)
    stop = threading.Event()
    t = threading.Thread(target=daemon.run, kwargs=dict(
        conn=conn, stop=stop, workers=2, cycle_s=0.2, chrome=NoChrome(), poll_s=0.05))
    t.start()
    deadline = time.time() + 10
    while ("crawl" not in calls or calls.count("watch") < 2) and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=10)
    assert not t.is_alive()
    assert calls[:2] == ["watch", "plan"]
    assert "crawl" in calls and calls.count("watch") >= 2      # cycles repeat every cycle_s
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type='crawl' AND status='done'").fetchone()[0] >= 1


def test_stale_running_jobs_are_requeued(tmp_path):
    conn = db.connect(tmp_path / "g.db")
    jid = jobs.post(conn, "crawl", {"place_id": 1})
    jobs.claim(conn, ["crawl"])
    conn.execute("UPDATE jobs SET started_at = 0 WHERE id = ?", (jid,))
    assert jobs.requeue_stale(conn) == 1


def test_service_plist(tmp_path):
    path = service.install(path=tmp_path / "x.plist")
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == service.LABEL
    assert data["ProgramArguments"][-1] == "daemon"
    assert data["KeepAlive"] and data["RunAtLoad"]
