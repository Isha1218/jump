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
    assert [(r["page_id"], r["probability"]) for r in res] == [(g["dropped"], 0.9)]   # < 0.1 hidden
    assert len(sent["body"]["questions"]["page"]["criteria"]) == len(cands)


def _jev(probs: dict):
    def handler(req):
        return httpx.Response(200, json={"answers": {"page": {"type": "choice", "probabilities": probs}}})
    return mock(handler)


def test_nothing_shown_when_jev_is_unsure_about_everything(conn):
    build_graph(conn)
    n = len(retriever.retrieve(conn, "452 rpc lecture"))
    assert pipeline.search(conn, "452 rpc lecture", api_key="k", client=_jev({f"c{i}": 0.05 for i in range(n)})) == []


def test_opened_pages_rank_higher(conn):
    build_graph(conn)
    cands = retriever.retrieve(conn, "452 rpc lecture")
    a, b = cands[0]["page_id"], cands[1]["page_id"]
    probs = {"c0": 0.4, "c1": 0.3}
    assert pipeline.search(conn, "452 rpc lecture", api_key="k", client=_jev(probs))[0]["page_id"] == a
    pipeline.record_pick(conn, "452 rpc lecture", b)
    cands = retriever.retrieve(conn, "452 rpc lecture")
    probs = {f"c{i}": {a: 0.4, b: 0.3}.get(c["page_id"], 0) for i, c in enumerate(cands)}
    assert pipeline.search(conn, "452 rpc lecture", api_key="k", client=_jev(probs))[0]["page_id"] == b


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
