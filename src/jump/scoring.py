"""Revisit score: how likely you are to come back to a place (or page). See docs/architecture.md.

x = bias + Σ weight·term, revisit = sigmoid(x); `days` and `breadth` enter as ln(1+value).
"""
import math
from collections.abc import Callable, Iterable
from datetime import date, datetime
from statistics import mean, pstdev

from . import config

WEIGHTS = {"bias": -3.0, "days": 1.5, "typed": 1.0, "breadth": 0.5, "bounce": -1.0, "regular": 0.5}
LOG_TERMS = {"days", "breadth"}
TYPED = {1, 2}       # TYPED, AUTO_BOOKMARK
RELOAD = 8


def day_of(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


def features(visits: Iterable[dict], now: float, half_life: float,
             inside: Callable[[str], bool] | None = None,
             from_hub: Callable[[str], bool] | None = None) -> dict:
    """Score inputs for a set of visits (dicts with url, ts, transition, duration_s, from_url).

    Arrivals are non-reload visits not coming from inside the same place (`inside(from_url)`).
    """
    visits = list(visits)
    today = day_of(now)
    days = sorted({day_of(v["ts"]) for v in visits})
    arrivals = [v for v in visits if v["transition"] != RELOAD and not (inside and v["from_url"] and inside(v["from_url"]))]
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    return {
        "visits": len(visits),
        "n_days": len(days),
        "days": round(sum(0.5 ** (max(0, (today - d).days) / half_life) for d in days), 4),
        "typed": round(sum(v["transition"] in TYPED for v in arrivals) / len(arrivals), 4) if arrivals else 0.0,
        "breadth": len({v["url"] for v in visits}),
        "bounce": round(sum(0 < v["duration_s"] < config.BOUNCE_SECONDS for v in visits) / len(visits), 4)
        if visits else 0.0,
        "regular": round(1 - min(1.0, pstdev(gaps) / mean(gaps)), 4) if len(days) >= 3 else 0.0,
        "from_search": round(sum(bool(from_hub and v["from_url"] and from_hub(v["from_url"])) for v in arrivals)
                             / len(arrivals), 4) if arrivals else 0.0,
    }


def revisit(feats: dict, weights: dict = WEIGHTS) -> float:
    """sigmoid of the weighted features; features missing from `feats` are omitted."""
    x = weights["bias"] + sum(w * (math.log1p(feats[k]) if k in LOG_TERMS else feats[k])
                              for k, w in weights.items() if k != "bias" and k in feats)
    return 1 / (1 + math.exp(-x))


def score_place(visits: Iterable[dict], now: float, inside=None, from_hub=None) -> tuple[dict, float]:
    f = features(visits, now, config.DAY_HALF_LIFE_DAYS, inside, from_hub)
    return f, revisit(f)


def score_page(visits: Iterable[dict], now: float, from_hub=None) -> tuple[dict, float]:
    f = features(visits, now, config.PAGE_HALF_LIFE_DAYS, from_hub=from_hub)
    del f["breadth"]
    return f, revisit(f)
