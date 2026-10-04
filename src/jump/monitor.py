"""Watches the active Chrome tab: records visits (with real time on page) and the text you're looking at.

Talks to your running Chrome through AppleScript. Needs Chrome → View → Developer →
"Allow JavaScript from Apple Events" for page text; visits are recorded either way.
"""
import json
import subprocess
import threading
import time
from dataclasses import dataclass

from . import db, urls

POLL_S = 2
CAPTURE_AFTER_S = 3      # read page text once you've stayed this long
SNIPPET_CHARS = 1000

_TAB_SCRIPT = """
if application "Google Chrome" is not running then return ""
set sep to character id 9
tell application "Google Chrome"
  if not frontmost or (count of windows) = 0 then return ""
  set w to front window
  set t to active tab of w
  return (id of t as text) & sep & (mode of w) & sep & (URL of t) & sep & (title of t)
end tell
"""

# Runs inside the tab. Reads (1) the page's own description + breadcrumb, (2) real text blocks only
# (no menus, headers, footers, buttons, forms or hidden elements; >= 5 words), (3) across the whole page:
# every h1-h3 as an outline, plus the first 2 text blocks under each heading.
_TEXT_JS = " ".join("""
(() => {
  const SKIP = 'nav, header, footer, aside, button, select, input, textarea, form, [role=navigation], [role=menu],
    [role=menubar], [role=toolbar], [role=banner], [role=dialog], [aria-hidden=true]';
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const meta = n => clean((document.querySelector('meta[name="' + n + '"], meta[property="' + n + '"]') || {}).content);
  const crumb = document.querySelector('[aria-label*=breadcrumb i], [class*=breadcrumb i]');
  const root = document.querySelector('main, [role=main]') || document.body;
  const outline = [], blocks = [];
  let perSection = 0;
  for (const el of root.querySelectorAll('h1, h2, h3, p, li, td, dd, blockquote')) {
    if (el.closest(SKIP) || !el.getClientRects().length) continue;
    const t = clean(el.innerText);
    if (!t) continue;
    if (/^H[1-3]$/.test(el.tagName)) { if (outline.length < 30) outline.push(t.slice(0, 120)); perSection = 0; continue; }
    if (t.split(' ').length < 5 || perSection >= 2 || el.querySelector('p, li, td, dd')) continue;
    blocks.push(t.slice(0, 300)); perSection++;
  }
  return JSON.stringify({u: location.href, h: outline, d: meta('description') || meta('og:description'),
    b: crumb ? clean(crumb.innerText).slice(0, 200) : '', t: blocks.join(' … ')});
})()
""".split())


def compose_snippet(description: str, breadcrumb: str, text: str) -> str:
    """'Description: … | Breadcrumb: … | <text blocks>', capped at SNIPPET_CHARS."""
    parts = [f"Description: {description}" if description else "", f"Breadcrumb: {breadcrumb}" if breadcrumb else "", text]
    return " | ".join(p for p in parts if p)[:SNIPPET_CHARS]


@dataclass
class Tab:
    id: str
    incognito: bool
    url: str
    title: str


def _osascript(script: str) -> str | None:
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.rstrip("\n")


class Chrome:
    """The real Chrome, via AppleScript."""

    def active_tab(self) -> Tab | None:
        """The tab you're looking at, or None if Chrome isn't the frontmost app."""
        try:
            out = _osascript(_TAB_SCRIPT)
        except RuntimeError:
            return None
        if not out:
            return None
        tab_id, mode, url, title = (out.split("\t") + ["", "", "", ""])[:4]
        return Tab(tab_id, mode == "incognito", url, title)

    def page_text(self) -> dict:
        """{url, headings, snippet} of the active tab, read from the rendered page."""
        js = _TEXT_JS.replace("\\", "\\\\").replace('"', '\\"')
        out = _osascript(f'tell application "Google Chrome" to execute active tab of front window javascript "{js}"')
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise RuntimeError(f"page script failed ({out!r})") from None
        snippet = compose_snippet(data.get("d", ""), data.get("b", ""), data.get("t", ""))
        return {"url": data.get("u", ""), "headings": data.get("h", []), "snippet": snippet}


class Monitor:
    def __init__(self, conn, chrome=None, log=print):
        self.conn, self.chrome, self.log = conn, chrome or Chrome(), log
        self.current: dict | None = None        # the visit in progress
        self.prev_in_tab: dict[str, str] = {}   # tab id -> last URL seen in it (the visit's referrer)

    def tick(self, now: float) -> None:
        tab = self.chrome.active_tab()
        url = urls.normalize(tab.url) if tab and not tab.incognito else None
        cur = self.current
        if cur and (url != cur["url"] or tab.id != cur["tab_id"]):
            self.close(now)
        if url and not self.current:
            ref = self.prev_in_tab.get(tab.id)
            self.current = {"tab_id": tab.id, "url": url, "title": tab.title, "start": now,
                            "from_url": ref if ref != url else None, "captured": False}
        cur = self.current
        if cur and not cur["captured"] and now - cur["start"] >= CAPTURE_AFTER_S:
            cur["captured"] = True
            self._capture(cur)

    def _capture(self, cur: dict) -> None:
        try:
            text = self.chrome.page_text()
        except RuntimeError as e:
            if "turned off" in str(e):
                self.log("can't read page text; turn on Chrome → View → Developer → Allow JavaScript from Apple Events")
            else:
                self.log(f"can't read page text: {e}")
            return
        if urls.normalize(text["url"]) != cur["url"]:
            return  # the tab moved on before we read it
        db.upsert_page(self.conn, cur["url"], headings=text["headings"], snippet=text["snippet"])

    def close(self, now: float) -> None:
        """Record the visit in progress, if any."""
        cur, self.current = self.current, None
        if not cur:
            return
        self.conn.execute(
            "INSERT INTO visits(url, ts, duration_s, from_url) VALUES (?, ?, ?, ?)",
            (cur["url"], cur["start"], now - cur["start"], cur["from_url"]),
        )
        if cur["title"]:
            db.upsert_page(self.conn, cur["url"], title=cur["title"])
        self.prev_in_tab[cur["tab_id"]] = cur["url"]
        self.log(f"visit {now - cur['start']:5.0f}s  {cur['url']}")

    def run(self, stop: threading.Event, poll_s: float = POLL_S) -> None:
        while not stop.is_set():
            try:
                self.tick(time.time())
            except Exception as e:   # one bad page must never stop monitoring
                self.log(f"monitor error: {type(e).__name__}: {e}")
            stop.wait(poll_s)
        self.close(time.time())
