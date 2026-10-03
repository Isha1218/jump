"""Hub detection and grouping of visited URLs into places, from behavior and URL structure only.

Hubs: an origin+path visited >= HUB_MIN_VISITS times, with >= HUB_DISTINCT_QUERIES of those visits
having distinct query strings, from which you navigated out to >= HUB_MIN_OUT_ORIGINS other origins
(search results, feeds). Hub URLs are never part of a place.

Places: per origin, build a trie over each URL's *directory* (path up to its last "/"; a page `/a/b`
sitting next to a directory `/a/b/` is treated as that directory's index). Weight = page-days (sum over
URLs of distinct days visited). Walk down from the root; at each node:
  1. Tenants: children whose subtrees share URL shapes are interchangeable instances (owners, repos,
     courses). The shape of a URL below child c is its templated path after c with the first segment
     blanked; two shapes are alike if equally long (>= 2) and agree on at least half of the remaining
     positions. Descend into every child alike to some other child.
  2. Concentration: else if one child holds >= CONCENTRATION of the node's weight, descend into it.
  3. Otherwise stop: the node is the place (its children are sections, e.g. /api, /payments).
URLs not carried down (the node's own pages, non-tenant children) form a place at the node.
Depth is capped at MAX_DEPTH segments.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil
from urllib.parse import unquote, urlsplit

from . import urls as U

HUB_MIN_VISITS = 3
HUB_DISTINCT_QUERIES = 0.8
HUB_MIN_OUT_ORIGINS = 3
CONCENTRATION = 0.8
MAX_DEPTH = 4
TERMISH = re.compile(r"^(\d+[a-z]{0,2}|[a-z]?\d+|[0-9a-f-]{12,})$", re.I)


@dataclass
class Place:
    origin: str
    path_prefix: str
    urls: list[str]
    alias: str = ""

    @property
    def scope(self) -> str:
        return self.origin + self.path_prefix


@dataclass
class _Node:
    children: dict[str, "_Node"] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)

    def all_urls(self) -> list[str]:
        out = list(self.urls)
        for c in self.children.values():
            out += c.all_urls()
        return out


def path_key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def directory(url: str) -> str:
    path = urlsplit(url).path or "/"
    return path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"


def find_hubs(visits: list[dict]) -> set[str]:
    """origin+path keys that behave like search/feed pages."""
    queries: dict[str, list[str]] = defaultdict(list)
    out: dict[str, set[str]] = defaultdict(set)
    for v in visits:
        queries[path_key(v["url"])].append(urlsplit(v["url"]).query)
        if v.get("from_url") and U.origin(v["url"]) != U.origin(v["from_url"]):
            out[path_key(v["from_url"])].add(U.origin(v["url"]))
    return {k for k, qs in queries.items()
            if len(qs) >= HUB_MIN_VISITS and len(set(qs)) >= HUB_DISTINCT_QUERIES * len(qs)
            and len(out[k]) >= HUB_MIN_OUT_ORIGINS}


def _shapes(url: str, depth: int) -> tuple[str, ...] | None:
    """Templated path below the child at `depth`, first segment blanked; None if too short to compare."""
    segs = [s for s in U.url_template(url).split("/")[1:] if s][depth + 1:]
    return ("*", *segs[1:]) if len(segs) >= 2 else None


def _alike(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if len(a) != len(b):
        return False
    agree = sum(x == y for x, y in zip(a[1:], b[1:]))
    return agree >= max(1, ceil((len(a) - 1) / 2))


def _tenants(node: _Node, depth: int) -> list[str]:
    shapes = {name: {s for u in c.all_urls() if (s := _shapes(u, depth))} for name, c in node.children.items()}
    names = [n for n in shapes if shapes[n]]
    found = set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if (a not in found or b not in found) and any(_alike(x, y) for x in shapes[a] for y in shapes[b]):
                found |= {a, b}
    return [n for n in node.children if n in found]


def _adopt(node: _Node) -> None:
    """Move a page `/a/b` into child directory `b/` when that directory exists."""
    keep = []
    for u in node.urls:
        last = urlsplit(u).path.rstrip("/").rsplit("/", 1)[-1]
        (node.children[last].urls if last in node.children else keep).append(u)
    node.urls = keep
    for c in node.children.values():
        _adopt(c)


def _split(node: _Node, segs: list[str], origin: str, weight: dict[str, int], out: list[Place]) -> None:
    descend: list[str] = []
    if len(segs) < MAX_DEPTH and node.children:
        descend = _tenants(node, len(segs))
        if not descend:
            total = sum(weight[u] for u in node.all_urls())
            name, child = max(node.children.items(), key=lambda kv: sum(weight[u] for u in kv[1].all_urls()))
            if sum(weight[u] for u in child.all_urls()) >= CONCENTRATION * total:
                descend = [name]
    leftover = list(node.urls) + [u for n, c in node.children.items() if n not in descend for u in c.all_urls()]
    if leftover:
        prefix = "/" + "".join(s + "/" for s in segs)
        out.append(Place(origin, prefix, sorted(leftover), alias(origin, prefix)))
    for name in descend:
        _split(node.children[name], segs + [name], origin, weight, out)


def group(url_days: dict[str, set]) -> list[Place]:
    """Group URLs (mapped to the set of days each was visited) into places."""
    roots: dict[str, _Node] = defaultdict(_Node)
    for url in url_days:
        node = roots[U.origin(url)]
        for s in [s for s in directory(url).split("/") if s]:
            node = node.children.setdefault(s, _Node())
        node.urls.append(url)
    weight = {u: len(d) for u, d in url_days.items()}
    out: list[Place] = []
    for origin, root in roots.items():
        _adopt(root)
        _split(root, [], origin, weight, out)
    return out


def alias(origin: str, path_prefix: str) -> str:
    """Short readable name: the last meaningful prefix segment, else the site's name from its host."""
    for s in reversed([unquote(s) for s in path_prefix.split("/") if s]):
        if len(s) > 1 and not TERMISH.match(s):
            return s.lower()[:40]
    labels = [lab for lab in (urlsplit(origin).hostname or "").split(".") if lab != "www"]
    if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else origin)
