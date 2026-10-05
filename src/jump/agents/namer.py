"""Namer agent: gives every page a short, clear name from its own words (see jump.naming). No AI."""
import sqlite3

from .. import db, naming


def run_once(conn: sqlite3.Connection) -> dict:
    """Recompute names for all pages; write only the ones that changed."""
    pages = [dict(r) for r in conn.execute("SELECT url, title, headings, name FROM pages")]
    anchors: dict[str, list[str]] = {}
    for r in conn.execute("SELECT t.url, l.anchor FROM links l JOIN pages t ON t.id = l.to_id "
                          "WHERE l.anchor IS NOT NULL AND l.anchor != ''"):
        anchors.setdefault(r["url"], []).append(r["anchor"])
    names = naming.name_all(pages, anchors)
    changed = [p["url"] for p in pages if names[p["url"]] and names[p["url"]] != p["name"]]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for url in changed:
            db.upsert_page(conn, url, name=names[url])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"pages": len(pages), "renamed": len(changed)}
