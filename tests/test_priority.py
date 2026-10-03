import math

import pytest

from jump import config
from jump.crawl.priority import Frontier, Patterns, link_base, rarity, scope_factor


def test_rarity():
    assert rarity(1, 1) == 1.0                    # early on: not 0
    assert rarity(10, 1) == 1.0
    assert rarity(10, 10) < 0.05                  # on every page -> ~0
    assert rarity(10, 2) > rarity(10, 5) > rarity(10, 10)
    assert rarity(0, 0) == 1.0


def test_scope_and_base():
    assert scope_factor("deeper") == 1.0
    assert scope_factor("sideways") == config.SIDEWAYS_FACTOR
    assert scope_factor("outside") == 0.0
    assert link_base(1.0, "deeper", 1.0) == pytest.approx(2 * config.HOP_DECAY)
    assert link_base(1.0, "outside", 1.0) == 0.0


def test_patterns():
    pat = Patterns(["https://x.edu/l/l03.html", "https://x.edu/l/l04.html", "https://x.edu/staff.html"])
    assert pat("https://x.edu/l/l05.html") == pytest.approx(2 / 3)
    assert pat("https://x.edu/other.html") == 0
    assert Patterns([])("https://x.edu/a") == 0


def test_frontier_best_first_and_lazy_reevaluation():
    f = Frontier()
    f.add_seed("s", 0.9)
    assert f.pop() == ("s", 0.9)
    f.observe({"nav", "a"})
    f.offer("nav", 0.8)
    f.offer("a", 0.5)
    f.offer("a", 0.3)                              # keeps the best base
    assert f.priority("a") == 0.5
    # three more pages all link to nav -> nav's rarity collapses, "a" overtakes it
    for _ in range(3):
        f.observe({"nav"})
    assert f.priority("nav") == pytest.approx(0.8 * math.log(5 / 4) / math.log(5))
    assert f.pop()[0] == "a"
    assert f.pop()[0] == "nav"
    assert f.pop() is None
    f.offer("a", 1.0)                              # done urls are never re-queued
    assert f.pop() is None
