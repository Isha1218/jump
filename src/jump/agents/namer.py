"""Namer agent: gives pages a short, clear name for the result list (Gemini), instead of the raw tab title."""
import re
import sqlite3
import time

import httpx

from .. import config, db

MODEL = "gemini-flash-lite-latest"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
BATCH = 50               # pages named per run
MAX_CHARS = 80
DELAY_S = 4.0            # Gemini free tier allows 15 requests/minute

PROMPT = """Write a short, clear name for this web page: the site, then which part of the site it is.
Use the URL path to tell similar pages apart (e.g. a course's home vs its discussion board vs one thread; \
an inbox vs one email; a profile vs its settings). For a single item (a thread, email, document, product), \
include its topic. Keep identifiers that tell things apart: course codes, terms (e.g. 26au), usernames, \
repo names.
Rules: at most 8 words; no pronouns (no "your", "my", "our"); ignore unread counts like "(886)"; \
don't describe the page's purpose for a tool or list; no quotes or trailing punctuation. Reply with the name only.

URL: {url}
Tab title: {title}
Headings: {headings}
Text: {snippet}"""


def clean(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r'^["\'`*]+|["\'`*.]+$', "", line).strip()[:MAX_CHARS]


def name_page(page, api_key: str, client: httpx.Client) -> str | None:
    """One Gemini call; None on any error (the page is simply retried next run)."""
    prompt = PROMPT.format(url=page["url"], title=page["title"] or "", headings=page["headings"] or "",
                           snippet=page["snippet"] or "")
    try:
        r = client.post(URL, headers={"x-goog-api-key": api_key}, timeout=30,
                        json={"contents": [{"parts": [{"text": prompt}]}]})
        r.raise_for_status()
        return clean(r.json()["candidates"][0]["content"]["parts"][0]["text"]) or None
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None


def run_once(conn: sqlite3.Connection, api_key: str | None = None, limit: int = BATCH,
             client: httpx.Client | None = None, delay: float = DELAY_S) -> dict:
    """Name unnamed visited pages in active places, most-revisited first."""
    api_key = api_key or config.GEMINI_API_KEY
    if not api_key:
        return {"named": 0, "skipped": "no GEMINI_API_KEY"}
    rows = conn.execute(
        "SELECT g.url, g.title, g.headings, g.snippet FROM pages g JOIN places p ON p.id = g.place_id "
        "WHERE g.name IS NULL AND g.visited = 1 AND p.status = 'active' AND (g.title IS NOT NULL OR g.snippet IS NOT NULL) "
        "ORDER BY g.revisit DESC LIMIT ?", (limit,)).fetchall()
    client = client or httpx.Client()
    named = 0
    for i, row in enumerate(rows):
        if i:
            time.sleep(delay)
        name = name_page(row, api_key, client)
        if name:
            db.upsert_page(conn, row["url"], name=name)
            named += 1
    return {"named": named, "failed": len(rows) - named}
