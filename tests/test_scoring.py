import math

from jump import scoring

NOW = 1_790_000_000.0
DAY = 86400
BASE = "https://x.edu/c/452/"


def v(url, days_ago, duration=60, from_url=None):
    return {"url": url, "ts": NOW - days_ago * DAY, "duration_s": duration, "from_url": from_url}


def test_course_like_place_scores_high():
    visits = []
    for k, d in enumerate(range(0, 16, 2)):                     # 8 days, every 2 days
        visits.append(v(BASE, d))
        for j in range(4):
            page = f"{BASE}lec/l{(k * 4 + j) % 30:02d}.html"
            visits.append(v(page, d, from_url=BASE, duration=5 if j == 0 and k % 3 == 0 else 120))
    feats, score = scoring.score_place(visits, NOW, inside=lambda u: u.startswith(BASE))
    assert feats["n_days"] == 8 and feats["regular"] == 1.0
    assert feats["breadth"] >= 29 and feats["bounce"] < 0.1
    assert score > 0.85


def test_single_bounce_from_search_scores_low():
    hub = "https://search.example/s?q=x"
    feats, score = scoring.score_place([v(BASE + "p", 0, duration=3, from_url=hub)], NOW,
                                       from_hub=lambda u: u.startswith("https://search.example/s"))
    assert feats["bounce"] == 1.0 and feats["from_search"] == 1.0 and feats["regular"] == 0.0
    assert score < 0.1


def test_feature_details():
    visits = [v(BASE, 14, duration=0), v(BASE, 14, duration=2), v(BASE + "a", 0)]
    f, s = scoring.score_place(visits, NOW)
    assert f["days"] == 1.5                                       # 1 (today) + 0.5 (one half-life ago)
    assert math.isclose(f["bounce"], 1 / 3, abs_tol=1e-3)         # duration 0 is unknown, not a bounce
    x = -3 + 1.5 * math.log(2.5) - 1 / 3 + 0.5 * math.log(3)
    assert math.isclose(s, 1 / (1 + math.exp(-x)), abs_tol=1e-3)


def test_regularity():
    regular = [v(BASE, d) for d in (0, 7, 14, 21)]
    erratic = [v(BASE, d) for d in (0, 1, 2, 30)]
    assert scoring.features(regular, NOW, 14)["regular"] == 1.0
    assert scoring.features(erratic, NOW, 14)["regular"] < 0.2


def test_page_score_omits_breadth_and_decays_faster():
    visits = [v(BASE, 7)]
    pf, ps = scoring.score_page(visits, NOW)
    lf, ls = scoring.score_place(visits, NOW)
    assert "breadth" not in pf and pf["days"] == 0.5 and lf["days"] < 1
    assert ps < ls


def test_weights_are_overridable():
    f, _ = scoring.score_place([v(BASE, 0)], NOW)
    assert scoring.revisit(f, {**scoring.WEIGHTS, "bias": 100}) > 0.99
