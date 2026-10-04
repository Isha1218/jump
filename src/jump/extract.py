"""Read the rendered text of visited pages with a headless browser (Playwright), using Jump's own logged-in profile."""
import re
import sqlite3

from playwright.sync_api import BrowserContext, Page, sync_playwright

from . import config, db, urls

PROFILE = config.HOME / "browser"
SNIPPET_CHARS = 300
LOAD_TIMEOUT_MS = 20000
SETTLE_TIMEOUT_MS = 5000

_HEADINGS_JS = "els => els.map(e => e.innerText.trim()).filter(Boolean).slice(0, 30)"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _open(p, headless: bool) -> BrowserContext:
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE), headless=headless)


def login(start_url: str = "about:blank") -> None:
    """Open a visible browser on Jump's profile; sign in to your sites, then close the window."""
    with sync_playwright() as p:
        ctx = _open(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url)
        print("Sign in to the sites you want indexed, then close the browser window.")
        ctx.wait_for_event("close", timeout=0)


def extract_page(page: Page, url: str) -> dict:
    """Load `url` and return its rendered title, headings and text snippet (read from the DOM)."""
    page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except Exception:
        pass  # busy apps never go idle; the DOM so far is good enough
    root = "main" if page.query_selector("main") else "body"   # <main> skips site menus
    headings = [_clean(h) for h in page.eval_on_selector_all(f"{root} :is(h1, h2, h3)", _HEADINGS_JS)]
    return {
        "title": _clean(page.title()),
        "headings": headings,
        "snippet": _clean(page.inner_text(root))[:SNIPPET_CHARS],
    }


def run(conn: sqlite3.Connection, limit: int | None = None, headless: bool = True) -> dict:
    """Extract text for visited pages in active places that don't have any yet."""
    rows = conn.execute(
        "SELECT g.url FROM pages g JOIN places p ON p.id = g.place_id "
        "WHERE g.visited = 1 AND g.snippet IS NULL AND g.kind = 'html' AND p.status = 'active' "
        "ORDER BY g.revisit DESC LIMIT ?",
        (limit if limit is not None else -1,),
    ).fetchall()
    done, failed = 0, 0
    with sync_playwright() as p:
        ctx = _open(p, headless=headless)
        page = ctx.new_page()
        for row in rows:
            url = row["url"]
            if urls.is_action_url(url):
                continue
            try:
                data = extract_page(page, url)
            except Exception as e:
                failed += 1
                print(f"failed {url}: {type(e).__name__}")
                continue
            db.upsert_page(conn, url, **{k: v for k, v in data.items() if v})
            done += 1
            print(f"ok     {url}")
        ctx.close()
    return {"candidates": len(rows), "extracted": done, "failed": failed}
