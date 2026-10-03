import json

import httpx

from jump import config
from jump.search import jev

CANDS = [{"description": f"page {i}"} for i in range(300)]


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def answer(probs, choice="c1", confidence=0.8):
    return {"model": "jev-1.13.0", "usage": {},
            "answers": {"page": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probs}}}


def test_request_shape_and_alignment():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=answer({"c1": 0.7, "c3": 0.2}))

    r = jev.rank_with_confidence("452 rpc lecture", CANDS[:4], "k", client=client_for(handler))
    assert r.probabilities == [0.0, 0.7, 0.0, 0.2] and r.choice == 1 and r.confidence == 0.8
    body = seen["body"]
    assert seen["url"] == config.JEV_URL and seen["auth"] == "Bearer k"
    assert body["model"] == config.JEV_MODEL and "452 rpc lecture" in body["state"]
    q = body["questions"]["page"]
    assert q["type"] == "choice" and q["instructions"] == jev.INSTRUCTIONS
    assert q["criteria"] == {"c0": "page 0", "c1": "page 1", "c2": "page 2", "c3": "page 3"}


def test_caps_criteria():
    seen = {}

    def handler(req):
        seen["n"] = len(json.loads(req.content)["questions"]["page"]["criteria"])
        return httpx.Response(200, json=answer({"c0": 1.0}))

    probs = jev.rank("q", CANDS, "k", client=client_for(handler))
    assert seen["n"] == min(config.CANDIDATES, 255) and len(probs) == len(CANDS)


def test_failures_return_none():
    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    handlers = [
        timeout,
        lambda req: httpx.Response(500, json={"error": "boom"}),
        lambda req: httpx.Response(401, text="nope"),
        lambda req: httpx.Response(200, text="not json"),
        lambda req: httpx.Response(200, json={"answers": {}}),
        lambda req: httpx.Response(200, json=answer({"c0": "x"})),
        lambda req: httpx.Response(200, json=answer({}, choice=None)),
        lambda req: httpx.Response(200, json=[1, 2]),
    ]
    for h in handlers:
        assert jev.rank("q", CANDS[:3], "k", client=client_for(h)) is None
    assert jev.rank("q", CANDS[:3], None) is None
    assert jev.rank("q", [], "k", client=client_for(handlers[1])) is None


def test_choice_only_answer():
    h = lambda req: httpx.Response(200, json=answer(None, choice="c2", confidence=0.6))
    assert jev.rank("q", CANDS[:3], "k", client=client_for(h)) == [0.0, 0.0, 0.6]
