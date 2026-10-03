"""Link priority: parent · HOP_DECAY · rarity · scope · (1 + pattern), and a best-first frontier."""
import heapq
import math
from collections import Counter

from .. import config, urls

EPS = 1e-12


def rarity(n_pages: int, n_linking: int) -> float:
    """ln((N+1)/n) / ln(N+1): 1 for a link seen on one page, -> 0 for links on every page (nav, footer)."""
    if n_pages < 1 or n_linking < 1:
        return 1.0
    return math.log((n_pages + 1) / min(n_linking, n_pages)) / math.log(n_pages + 1)


def scope_factor(relation: str) -> float:
    return {"deeper": 1.0, "sideways": config.SIDEWAYS_FACTOR}.get(relation, 0.0)


def link_base(parent: float, relation: str, pattern: float) -> float:
    """Priority of a link before rarity: parent · HOP_DECAY · scope · (1 + pattern)."""
    return parent * config.HOP_DECAY * scope_factor(relation) * (1 + pattern)


class Patterns:
    """Share of a place's visited pages that have the same `url_template` as a URL."""

    def __init__(self, visited_urls: list[str]):
        self.counts = Counter(urls.url_template(u) for u in visited_urls)
        self.total = len(visited_urls)

    def __call__(self, url: str) -> float:
        return self.counts[urls.url_template(url)] / self.total if self.total else 0.0


class Frontier:
    """Max-priority queue whose items' rarity changes as pages are fetched (lazy re-evaluation on pop).

    An item's priority = base · rarity(N, n), where base is the best `parent · HOP_DECAY · scope · (1+pattern)`
    over every page linking to it; seeds have a fixed priority (no rarity).
    """

    def __init__(self):
        self._heap: list[tuple[float, int, str]] = []
        self._seq = 0
        self.base: dict[str, float] = {}
        self.seeds: set[str] = set()
        self.done: set[str] = set()
        self.linking: Counter = Counter()   # target -> fetched pages linking to it
        self.n_pages = 0                    # pages whose links have been observed

    def _push(self, url: str) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (-self.priority(url), self._seq, url))

    def priority(self, url: str) -> float:
        base = self.base.get(url, 0.0)
        return base if url in self.seeds else base * rarity(self.n_pages, self.linking[url])

    def add_seed(self, url: str, priority: float) -> None:
        if url not in self.done and priority > self.base.get(url, -1.0):
            self.base[url] = priority
            self.seeds.add(url)
            self._push(url)

    def observe(self, targets: set[str]) -> None:
        """Record the distinct link targets of one more fetched page."""
        self.n_pages += 1
        self.linking.update(targets)

    def offer(self, url: str, base: float) -> None:
        """A link to `url` was found with the given base priority (before rarity); keep the best."""
        if url in self.done or url in self.seeds or base <= self.base.get(url, -1.0):
            return
        self.base[url] = base
        self._push(url)

    def pop(self) -> tuple[str, float] | None:
        """Best (url, current priority), or None when empty. The url is marked done."""
        while self._heap:
            _, seq, url = heapq.heappop(self._heap)
            if url in self.done:
                continue
            cur = self.priority(url)
            if self._heap and cur < -self._heap[0][0] - EPS:
                heapq.heappush(self._heap, (-cur, seq, url))
                continue
            self.done.add(url)
            return url, cur
        return None

    def mark_done(self, url: str) -> None:
        self.done.add(url)
