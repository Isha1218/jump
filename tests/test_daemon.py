import plistlib
import sys
import threading
import time
import types

import jump.agents
from jump import daemon, db, jobs, service


def test_debouncer_waits_for_quiet_then_runs():
    d = daemon.Debouncer(debounce_s=30, fallback_s=600, now=0)
    assert not d.due(10)
    d.touch(10)
    assert not d.due(30)          # only 20s of quiet
    d.touch(35)                   # another write in the burst
    assert not d.due(60)
    assert d.due(65)
    d.ran(65)
    assert not d.due(100)
    assert d.due(665)             # fallback timer


def test_change_during_a_run_is_not_lost():
    d = daemon.Debouncer(debounce_s=30, fallback_s=600, now=0)
    d.touch(0)
    started = 30                  # run begins
    d.touch(31)                   # Chrome writes History while the run is in progress
    d.ran(started)
    assert d.last_change == 31
    assert d.due(61)


def _stub_agents(monkeypatch, calls):
    watcher = types.ModuleType("jump.agents.watcher")
    planner = types.ModuleType("jump.agents.planner")
    crawler = types.ModuleType("jump.agents.crawler")

    def w_run(conn, history_path=None, now=None):
        calls.append("watch")
        return {}

    def p_run(conn, now=None):
        calls.append("plan")
        jobs.post(conn, "crawl", {"place_id": 1, "budget": 3}, dedupe_key="1")
        time.sleep(0.5)           # slow cycle: the History write below lands mid-run (the CI race)
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
        monkeypatch.setattr(sys.modules["jump.agents"], name, mod, raising=False)


def test_run_cycles_agents_and_drains_crawl_jobs(tmp_path, monkeypatch):
    calls = []
    _stub_agents(monkeypatch, calls)
    conn = db.connect(tmp_path / "g.db")
    history = tmp_path / "History"
    history.write_bytes(b"")
    monkeypatch.setattr(daemon, "WORKER_IDLE_S", 0.05)
    stop = threading.Event()
    t = threading.Thread(target=daemon.run, kwargs=dict(
        conn=conn, history_path=history, stop=stop, workers=2,
        debouncer=daemon.Debouncer(debounce_s=0.2, fallback_s=60), tick_s=0.05))
    t.start()
    deadline = time.time() + 5
    while "crawl" not in calls and time.time() < deadline:
        time.sleep(0.05)
    history.write_bytes(b"changed")          # simulate Chrome writing History
    while calls.count("watch") < 2 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=10)
    assert not t.is_alive()
    assert calls[:2] == ["watch", "plan"]
    assert "crawl" in calls
    assert calls.count("watch") >= 2, "History change should trigger another watcher run"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type='crawl' AND status='done'").fetchone()[0] >= 1


def test_stale_running_jobs_are_requeued(tmp_path):
    conn = db.connect(tmp_path / "g.db")
    jid = jobs.post(conn, "crawl", {"place_id": 1})
    jobs.claim(conn, ["crawl"])
    conn.execute("UPDATE jobs SET started_at = 0 WHERE id = ?", (jid,))
    assert jobs.requeue_stale(conn) == 1


def test_service_plist(tmp_path, monkeypatch):
    path = service.install(path=tmp_path / "x.plist")
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == service.LABEL
    assert data["ProgramArguments"][-1] == "daemon"
    assert data["KeepAlive"] and data["RunAtLoad"]
