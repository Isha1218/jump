"""Page names from the page's own words, no AI: specific link text, else the cleaned tab title, else
(for generic titles) the first heading, else the file/page name from the URL."""
import json
import re
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

from . import urls

SEP = re.compile(r"\s+[–—|·•-]\s+")                     # title separators: "CSE 123 – Ed Discussion"
UNREAD = re.compile(r"\s*\(\d[\d,]*\)")                  # unread counts: "(886) Inbox", "Inbox (21,407)"
DEFAULT_TITLES = re.compile(
    r"^(powerpoint presentation|presentation\d*|untitled( document| presentation| spreadsheet)?|"
    r"document\d*|microsoft (word|powerpoint|excel) - .*|slide \d+|new tab)$", re.I)
GENERIC_ANCHOR = re.compile(r"^(here|link|pdf|slides?|video|recording|notes|click here|download|code|handout|"
                            r"\[.*\]|\d+|more|open|view)$", re.I)
SUFFIX_SHARED_BY = 3                                     # an ending on this many of a site's titles is the site's name
TITLE_SHARED_BY = 2                                      # a title shared by this many pages can't tell them apart
MAX_CHARS = 100


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _strip_unread(title: str) -> str:
    return UNREAD.sub("", " ".join((title or "").split())).strip()


def site_suffixes(titles: list[str]) -> set[str]:
    """Title endings that repeat across a site's pages ('Ed Discussion', 'LinkedIn')."""
    ends = Counter()
    for t in set(_strip_unread(t) for t in titles if t):
        parts = SEP.split(t)
        if len(parts) > 1:
            ends[parts[-1].lower()] += 1
    return {e for e, n in ends.items() if n >= SUFFIX_SHARED_BY}


def clean_title(title: str, suffixes: set[str]) -> str:
    t = _strip_unread(title)
    while True:
        parts = SEP.split(t)
        if len(parts) > 1 and parts[-1].lower() in suffixes:
            t = t[: t.lower().rfind(parts[-1].lower())].rstrip(" –—|·•-")
        else:
            return t.strip()


def _last_segment(url: str) -> str:
    segs = urls.path_segments(url)
    return unquote(segs[-1]) if segs else _host(url)


def page_name(url: str, title: str | None, headings: list[str], anchors: list[str],
              suffixes: set[str], generic_titles: set[str]) -> str:
    for a in anchors:
        if a and not GENERIC_ANCHOR.match(a.strip()):
            return a.strip()[:MAX_CHARS]
    t = clean_title(title or "", suffixes)
    if t and not DEFAULT_TITLES.match(t) and t.lower() not in generic_titles:
        return t[:MAX_CHARS]
    if headings:
        return headings[0][:MAX_CHARS]
    if t and not DEFAULT_TITLES.match(t):
        return f"{t} · {_last_segment(url)}"[:MAX_CHARS]
    return _last_segment(url)[:MAX_CHARS]


def name_all(pages: list[dict], anchors: dict[str, list[str]]) -> dict[str, str]:
    """{url: name} for `pages` (dicts with url, title, headings JSON); site stats come from the same list."""
    by_host = defaultdict(list)
    for p in pages:
        by_host[_host(p["url"])].append(p)
    names = {}
    for host_pages in by_host.values():
        suffixes = site_suffixes([p["title"] for p in host_pages])
        cleaned = Counter()
        for p in host_pages:
            if p["title"]:
                cleaned[clean_title(p["title"], suffixes).lower()] += 1
        generic = {t for t, n in cleaned.items() if n >= TITLE_SHARED_BY}
        for p in host_pages:
            try:
                headings = [str(h) for h in json.loads(p["headings"] or "[]")]
            except ValueError:
                headings = []
            names[p["url"]] = page_name(p["url"], p["title"], headings, anchors.get(p["url"], []), suffixes, generic)
    return names
