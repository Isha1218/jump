"""Jev (TypeSafe AI) final ranking: one `choice` question over the retriever's candidates."""
from dataclasses import dataclass

import httpx

from ..config import CANDIDATES, JEV_MODEL, JEV_TIMEOUT_S, JEV_URL

MAX_OPTIONS = 255                # Jev's limit on choice criteria
INSTRUCTIONS = "Which page is the user trying to open?"


@dataclass
class Ranking:
    probabilities: list[float]   # aligned with the candidates sent
    choice: int | None           # index of Jev's pick
    confidence: float | None


def request_body(query: str, candidates: list[dict]) -> dict:
    criteria = {f"c{i}": c["description"] for i, c in enumerate(candidates[:min(CANDIDATES, MAX_OPTIONS)])}
    return {
        "state": f'The user typed "{query}" into a quick launcher.\n'
                 "They want to open the single page they mean, from the candidate pages below.",
        "model": JEV_MODEL,
        "questions": {"page": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}},
    }


def rank_with_confidence(query: str, candidates: list[dict], api_key: str | None,
                         client: httpx.Client | None = None) -> Ranking | None:
    """Ask Jev which candidate the user means; None on any failure (no key, timeout, HTTP or format error)."""
    if not api_key or not candidates:
        return None
    body = request_body(query, candidates)
    n = len(body["questions"]["page"]["criteria"])
    try:
        own = client is None
        client = client or httpx.Client(timeout=JEV_TIMEOUT_S)
        try:
            resp = client.post(JEV_URL, json=body, timeout=JEV_TIMEOUT_S,
                               headers={"Authorization": f"Bearer {api_key}"})
        finally:
            if own:
                client.close()
        resp.raise_for_status()
        ans = resp.json()["answers"]["page"]
        probs_raw = ans.get("probabilities") or {}
        choice = ans.get("choice")
        conf = ans.get("confidence")
        conf = float(conf) if conf is not None else None
        if not probs_raw and choice:                   # some answers carry only the pick
            probs_raw = {choice: conf if conf is not None else 1.0}
        probs = [max(0.0, float(probs_raw.get(f"c{i}", 0) or 0)) for i in range(n)]
        if not any(probs):
            return None
        idx = int(choice[1:]) if isinstance(choice, str) and choice[1:].isdigit() and int(choice[1:]) < n else None
        return Ranking(probs + [0.0] * (len(candidates) - n), idx, conf)
    except Exception:                                  # search must never crash because of Jev
        return None


def rank(query: str, candidates: list[dict], api_key: str | None, client: httpx.Client | None = None) -> list[float] | None:
    """Probability per candidate (missing -> 0), or None on any failure."""
    r = rank_with_confidence(query, candidates, api_key, client)
    return r.probabilities if r else None
