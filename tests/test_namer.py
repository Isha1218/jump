import json

import httpx

from jump import db
from jump.agents import namer
from jump.search import pipeline


def _graph(conn):
    pid = db.upsert_place(conn, "https://www.amazon.com", "/", status="active", revisit=0.6, alias="amazon")
    db.upsert_page(conn, "https://www.amazon.com/gp/css/order-history", place_id=pid, visited=1, revisit=0.5,
                   title="Your Orders", snippet="Orders placed in the past 3 months")
    db.upsert_page(conn, "https://www.amazon.com/cart", place_id=pid, visited=1, revisit=0.2, title="Cart")
    dropped = db.upsert_place(conn, "https://old.example", "/", status="dropped", revisit=0.1)
    db.upsert_page(conn, "https://old.example/x", place_id=dropped, visited=1, title="Old")


def _gemini(reply="Amazon Order History", status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        body = {"candidates": [{"content": {"parts": [{"text": reply}]}}]} if status == 200 else {"error": {}}
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_names_active_pages_and_search_shows_the_name(conn):
    _graph(conn)
    seen = []
    s = namer.run_once(conn, api_key="k", client=_gemini('"Amazon Order History."\n', seen=seen))
    assert s == {"named": 2, "failed": 0}                     # dropped place's page is skipped
    prompt = seen[0]["contents"][0]["parts"][0]["text"]
    assert "no pronouns" in prompt and "Your Orders" in prompt and "past 3 months" in prompt
    name = conn.execute("SELECT name FROM pages WHERE url LIKE '%order-history'").fetchone()[0]
    assert name == "Amazon Order History"                     # quotes, period and extra lines stripped
    top = pipeline.search(conn, "amazon order history", use_jev=False)[0]
    assert top["label"] == "Amazon Order History"


def test_name_is_searchable(conn):
    _graph(conn)
    namer.run_once(conn, api_key="k", client=_gemini("Amazon Purchase History"))
    assert any("order-history" in r["url"] for r in pipeline.search(conn, "purchase", use_jev=False))


def test_failures_leave_pages_unnamed_for_next_run(conn):
    _graph(conn)
    assert namer.run_once(conn, api_key="k", client=_gemini(status=503)) == {"named": 0, "failed": 2}
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE name IS NOT NULL").fetchone()[0] == 0
    assert namer.run_once(conn, api_key="k", client=_gemini())["named"] == 2


def test_already_named_pages_are_not_renamed(conn):
    _graph(conn)
    namer.run_once(conn, api_key="k", client=_gemini())
    assert namer.run_once(conn, api_key="k", client=_gemini("Something Else")) == {"named": 0, "failed": 0}


def test_without_key_does_nothing(conn, monkeypatch):
    monkeypatch.setattr(namer.config, "GEMINI_API_KEY", None)
    assert namer.run_once(conn)["named"] == 0
