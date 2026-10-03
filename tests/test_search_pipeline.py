import json

import httpx

from jump.search import pipeline, retriever
from test_search_retriever import build_graph


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_local_order_without_key(conn, monkeypatch):
    monkeypatch.setattr(pipeline.config, "JEV_API_KEY", None)
    g = build_graph(conn)
    res = pipeline.search(conn, "452 rpc lecture")
    assert len(res) <= 5 and res[0]["page_id"] == g["l05"]
    assert res[0]["probability"] is None and res[0]["kind"] == "pdf"
    assert set(res[0]) == {"page_id", "url", "label", "kind", "probability"}


def test_jev_reorders(conn):
    g = build_graph(conn)
    cands = retriever.retrieve(conn, "452 rpc lecture")
    want = next(i for i, c in enumerate(cands) if c["page_id"] == g["dropped"])
    sent = {}

    def handler(req):
        sent["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"page": {"type": "choice", "choice": f"c{want}", "confidence": 0.9,
                                                               "probabilities": {f"c{want}": 0.9, "c0": 0.05}}}})

    res = pipeline.search(conn, "452 rpc lecture", api_key="k", client=mock(handler))
    assert res[0]["page_id"] == g["dropped"] and res[0]["probability"] == 0.9
    assert res[1]["page_id"] == cands[0]["page_id"] and res[1]["probability"] == 0.05
    assert [r["page_id"] for r in res[2:]] == [c["page_id"] for c in cands if c["page_id"] not in
                                               (g["dropped"], cands[0]["page_id"])][:3]   # ties keep retriever order
    assert len(sent["body"]["questions"]["page"]["criteria"]) == len(cands)


def test_jev_failure_falls_back(conn):
    build_graph(conn)
    local = pipeline.search(conn, "452 rpc lecture", use_jev=False)

    def boom(req):
        raise httpx.ConnectTimeout("down", request=req)

    for h in (boom, lambda r: httpx.Response(503), lambda r: httpx.Response(200, text="{bad")):
        assert pipeline.search(conn, "452 rpc lecture", api_key="k", client=mock(h)) == local
    assert all(r["probability"] is None for r in local)


def test_use_jev_false_skips_network(conn):
    build_graph(conn)

    def fail(req):
        raise AssertionError("Jev must not be called")

    pipeline.search(conn, "stripe webhooks", use_jev=False, api_key="k", client=mock(fail))


def test_record_pick_boosts_later_searches(conn, monkeypatch):
    monkeypatch.setattr(pipeline.config, "JEV_API_KEY", None)
    g = build_graph(conn)
    assert pipeline.search(conn, "webhooks")[0]["page_id"] == g["hooks"]
    for _ in range(3):
        pipeline.record_pick(conn, "webhooks", g["gh_hooks"])
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 3
    assert pipeline.search(conn, "webhooks")[0]["page_id"] == g["gh_hooks"]


def test_open_in_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline.sys, "platform", "darwin")
    monkeypatch.setattr(pipeline.subprocess, "run", lambda cmd, check: calls.append(cmd) or type("R", (), {"returncode": 0}))
    monkeypatch.setattr(pipeline.webbrowser, "open_new_tab", lambda u: calls.append(("web", u)))
    pipeline.open_in_browser("https://x.edu/a")
    assert calls == [["open", "-a", "Google Chrome", "https://x.edu/a"]]

    def missing(cmd, check):
        raise FileNotFoundError

    calls.clear()
    monkeypatch.setattr(pipeline.subprocess, "run", missing)
    pipeline.open_in_browser("https://x.edu/a")
    assert calls == [("web", "https://x.edu/a")]
