"""HTML -> title, headings, snippet and links (with anchor text and enclosing row/item context)."""
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .. import urls

MAX_HEADINGS = 30
SNIPPET_BODY_CHARS = 300
CONTEXT_CHARS = 200
CONTEXT_TAGS = ["tr", "li", "dd", "p"]
CHROME_TAGS = ["script", "style", "noscript", "template", "nav", "footer", "header"]


@dataclass
class ParsedPage:
    title: str = ""
    headings: list[str] = field(default_factory=list)
    snippet: str = ""
    links: list[tuple[str, str, str]] = field(default_factory=list)  # (url, anchor, context)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _anchor(a: Tag) -> str:
    text = _clean(a.get_text(" "))
    if text:
        return text
    for attr in ("aria-label", "title"):
        if a.get(attr):
            return _clean(a[attr])
    img = a.find("img", alt=True)
    return _clean(img["alt"]) if img else ""


def _context(a: Tag, anchor: str) -> str:
    box = a.find_parent(CONTEXT_TAGS)
    if box is None:
        return ""
    if box.name == "tr":
        cells = [_clean(c.get_text(" ")) for c in box.find_all(["td", "th"])]
        text = " · ".join(c for c in cells if c)
    else:
        text = _clean(box.get_text(" "))
    text = _clip(text, CONTEXT_CHARS)
    return "" if text == anchor else text


def parse(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    base_tag = soup.find("base", href=True)
    base = urljoin(url, base_tag["href"]) if base_tag else url

    links: dict[str, tuple[str, str]] = {}
    for a in soup.find_all(["a", "area"], href=True):
        target = urls.normalize(a["href"], base)
        if not target:
            continue
        anchor = _anchor(a)
        if target not in links or (not links[target][0] and anchor):
            links[target] = (anchor, _context(a, anchor))

    title = _clean(soup.title.get_text()) if soup.title else ""
    headings = [h for h in (_clean(t.get_text(" ")) for t in soup.find_all(["h1", "h2", "h3"])) if h][:MAX_HEADINGS]
    for t in soup.find_all(CHROME_TAGS):
        t.decompose()
    body = _clip(_clean((soup.body or soup).get_text(" ")), SNIPPET_BODY_CHARS)
    snippet = " · ".join(p for p in (title, " · ".join(headings[:10]), body) if p)
    return ParsedPage(title, headings, snippet, [(u, an, ctx) for u, (an, ctx) in links.items()])
