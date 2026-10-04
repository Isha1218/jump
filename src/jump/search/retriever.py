"""Local retrieval: FTS5/BM25 over page text + incoming link text, weighted by revisit, picks and alias scope."""
import json
import re
import sqlite3
from urllib.parse import unquote, urlsplit

from .. import urls
from ..config import CANDIDATES
from .text import normalize_text, query_terms, tokens

MAX_LINK_TEXTS = 10              # incoming anchors/contexts kept per page
ALIAS_BOOST = 3.0
COVERAGE = 2.0                   # relevance per matched query term; BM25 IDF collapses for words in most pages
DESCRIPTION_CHARS = 400
# bm25 column weights, in pages_fts column order
WEIGHTS = {"title": 2.5, "anchors": 3.0, "contexts": 1.5, "headings": 1.0, "snippet": 0.7, "words": 0.5}
GENERIC_ANCHOR = re.compile(
    r"^\W*(slides?|pdf|ppt|pptx|here|click here|link|video|recording|download|view|open|notes|handout|"
    r"html|watch|more|read more|\d+)?\W*$",
    re.I,
)

FTS_SCHEMA = f"CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5({', '.join(WEIGHTS)}, tokenize='unicode61')"


def _link_texts(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[list[str], list[str]]]:
    """page id -> (deduped anchors, deduped contexts) from incoming links, capped."""
    out: dict[int, tuple[list[str], list[str]]] = {i: ([], []) for i in ids}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        rows = conn.execute(
            f"SELECT to_id, anchor, context FROM links WHERE to_id IN ({','.join('?' * len(part))}) ORDER BY rowid",
            part,
        )
        for r in rows:
            anchors, contexts = out[r["to_id"]]
            for text, bucket in ((r["anchor"], anchors), (r["context"], contexts)):
                text = " ".join((text or "").split())
                if text and text not in bucket and len(bucket) < MAX_LINK_TEXTS:
                    bucket.append(text)
    return out


def _headings(raw: str | None) -> list[str]:
    try:
        h = json.loads(raw) if raw else []
        return [str(x) for x in h] if isinstance(h, list) else [str(h)]
    except (ValueError, TypeError):
        return [raw]


def sync_fts(conn: sqlite3.Connection) -> int:
    """(Re)index every page with fts_dirty=1; returns how many were indexed."""
    conn.execute(FTS_SCHEMA)
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        pages = conn.execute("SELECT id, url, name, title, snippet, headings FROM pages WHERE fts_dirty = 1").fetchall()
        texts = _link_texts(conn, [p["id"] for p in pages])
        for p in pages:
            anchors, contexts = texts[p["id"]]
            conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (p["id"],))
            conn.execute(
                "INSERT INTO pages_fts(rowid, title, anchors, contexts, headings, snippet, words) VALUES (?,?,?,?,?,?,?)",
                (p["id"], normalize_text(" | ".join(filter(None, [p["name"], p["title"]]))), normalize_text(" | ".join(anchors)),
                 normalize_text(" | ".join(contexts)), normalize_text(" ".join(_headings(p["headings"]))),
                 normalize_text(p["snippet"]), normalize_text(urls.url_words(p["url"]))),
            )
        conn.executemany("UPDATE pages SET fts_dirty = 0 WHERE id = ?", [(p["id"],) for p in pages])
        if own_tx:
            conn.execute("COMMIT")
    except BaseException:
        if own_tx:
            conn.execute("ROLLBACK")
        raise
    return len(pages)


def _fts_atom(term: str) -> str:
    """One safely quoted FTS5 atom: a phrase for multi-word terms, a prefix for words of 3+ chars."""
    quoted = '"' + term.replace('"', '""') + '"'
    if " " in term or len(term) < 3:
        return quoted
    if len(term) >= 4 and term.endswith("s") and not term.endswith("ss"):
        quoted = '"' + term[:-1] + '"'          # crude plural folding: 'lectures' -> lecture*
    return quoted + "*"


def _or(terms: list[str]) -> str:
    return " OR ".join(dict.fromkeys(_fts_atom(t) for t in terms))


def match_expression(query: str) -> str:
    """FTS5 MATCH string OR-ing every term and synonym; '' if the query has no searchable words."""
    return _or([t for group in query_terms(query) for t in group])


def alias_places(conn: sqlite3.Connection, query: str) -> list[int]:
    """Places the query names by alias: '452' or 'cse 452' -> 'cse452'. Words shared by many aliases ('cse') don't count."""
    toks = tokens(query)
    compact = {"".join(toks[i:i + n]) for n in (1, 2, 3) for i in range(len(toks))}
    aliases = [(r["id"], normalize_text(r["alias"]).split())
               for r in conn.execute("SELECT id, alias FROM places WHERE alias IS NOT NULL AND alias != ''")]
    df: dict[str, int] = {}
    for _, words in aliases:
        for w in set(words):
            df[w] = df.get(w, 0) + 1
    return [pid for pid, words in aliases
            if "".join(words) in compact or any(w in toks and df[w] <= 2 for w in words)]


def label_for(page, anchors: list[str], contexts: list[str]) -> str:
    """Human label: best anchor, else '<context> — <generic anchor>', else title, else last path segment."""
    for a in anchors:
        if not GENERIC_ANCHOR.match(a):
            return a[:120]
    if anchors and contexts:
        return f"{contexts[0][:100]} — {anchors[0]}"
    if page["title"]:
        return " ".join(page["title"].split())[:120]
    segs = urls.path_segments(page["url"])
    return unquote(segs[-1]) if segs else (urlsplit(page["url"]).hostname or page["url"])


def description_for(page, label: str, site: str, anchors: list[str], contexts: list[str]) -> str:
    """One line for Jev: label | site › path | kind | context or snippet excerpt."""
    path = unquote(urlsplit(page["url"]).path) or "/"
    extra = [c for c in contexts if c not in label] + anchors[1:3]
    excerpt = " · ".join(extra) or " ".join((page["snippet"] or "").split())
    return f"{label} | {site} › {path} | {page['kind']} | {excerpt}"[:DESCRIPTION_CHARS]


def _url_key(url: str) -> str:
    """Lookalike pages share a key: same site + URL pattern (discussion/123 and discussion/456 collapse)."""
    return urls.url_template(url).removeprefix("www.")


def retrieve(conn: sqlite3.Connection, query: str, k: int = CANDIDATES) -> list[dict]:
    """Top-k candidates: dicts with page_id, url, kind, label, description, score."""
    sync_fts(conn)
    groups = [_or(g) for g in query_terms(query)]
    if not groups:
        return []
    covered = " + ".join(["(m.id IN (SELECT rowid FROM pages_fts WHERE pages_fts MATCH ?))"] * len(groups))
    scoped = ",".join(str(int(i)) for i in alias_places(conn, query)) or "NULL"
    weights = ", ".join(str(w) for w in WEIGHTS.values())
    sql = f"""
    WITH m AS MATERIALIZED (SELECT rowid AS id, -bm25(pages_fts, {weights}) AS rel FROM pages_fts WHERE pages_fts MATCH ?),
    s AS (
      SELECT p.id, p.url, p.kind, p.name, p.title, p.snippet, m.rel + {COVERAGE} * ({covered}) AS rel,
        MAX(p.revisit, COALESCE(pl.revisit, (SELECT MAX(lp.revisit) FROM links l JOIN pages f ON f.id = l.from_id
                                             JOIN places lp ON lp.id = f.place_id WHERE l.to_id = p.id), 0)) AS rv,
        (SELECT COUNT(*) FROM picks WHERE page_id = p.id) AS npicks,
        CASE WHEN p.place_id IN ({scoped}) OR EXISTS (SELECT 1 FROM links l JOIN pages f ON f.id = l.from_id
             WHERE l.to_id = p.id AND f.place_id IN ({scoped})) THEN {ALIAS_BOOST} ELSE 1.0 END AS boost,
        COALESCE(pl.alias, pl.origin) AS site
      FROM m JOIN pages p ON p.id = m.id LEFT JOIN places pl ON pl.id = p.place_id
    )
    SELECT *, rel * (0.5 + rv) * (1 + npicks) * boost AS score FROM s ORDER BY score DESC LIMIT ?
    """
    try:
        rows = conn.execute(sql, (" OR ".join(groups), *groups, 2 * k)).fetchall()
    except sqlite3.OperationalError:     # defensive: a MATCH string FTS5 still rejects
        return []
    seen, picked = set(), []
    for r in rows:
        key = _url_key(r["url"])
        if key not in seen:
            seen.add(key)
            picked.append(r)
        if len(picked) == k:
            break
    texts = _link_texts(conn, [r["id"] for r in picked])
    sites = _linking_sites(conn, [r["id"] for r in picked if r["site"] is None])
    out = []
    for r in picked:
        anchors, contexts = texts[r["id"]]
        label = r["name"] or label_for(r, anchors, contexts)
        site = r["site"] or sites.get(r["id"]) or urlsplit(r["url"]).hostname or ""
        out.append({"page_id": r["id"], "url": r["url"], "kind": r["kind"], "label": label,
                    "description": description_for(r, label, site, anchors, contexts), "score": r["score"],
                    "picks": r["npicks"]})
    return out


def _linking_sites(conn: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """For pages outside any place: alias of the most-revisited place linking to them."""
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT l.to_id, COALESCE(pl.alias, pl.origin) AS site FROM links l JOIN pages f ON f.id = l.from_id "
        f"JOIN places pl ON pl.id = f.place_id WHERE l.to_id IN ({','.join('?' * len(ids))}) ORDER BY pl.revisit",
        ids,
    )
    return {r["to_id"]: r["site"] for r in rows}
