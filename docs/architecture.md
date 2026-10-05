# Architecture

Two halves share one SQLite database (`~/.jump/graph.db`, schema in `src/jump/db.py`).

```
BACKGROUND (daemon)                                   FOREGROUND (jump search)
Active Chrome tab ─► MONITOR ─visits─► WATCHER ─rescored─► PLANNER ─crawl─► CRAWLER ×N      query ─► RETRIEVER ─► ≤200 ─► JEV ─► top 5
                     │                   │                │                        │
                     └──────────── SQLite: places · pages · links · visits · jobs · picks ────────────┘
```

Agents never call each other. They read/write tables and coordinate through the `jobs` queue (`src/jump/jobs.py`).

## Module contracts

Each module owns its tables' writes as listed. Function names below are called by `cli.py` / `daemon.py` and must not change.

### Foundation (shared)
- `jump.config` — paths + every tunable constant.
- `jump.db` — `connect()`, `upsert_page()`, `add_link()`, `upsert_place()`, `page_id()`, `get_meta()/set_meta()`.
- `jump.jobs` — `post()`, `claim()`, `finish()`, `requeue_stale()`.
- `jump.urls` — `normalize()`, `origin()`, `path_segments()`, `kind_from_url()`, `url_template()`, `in_scope()`, `relation()`, `is_action_url()`, `url_words()`.

### Monitor — `jump.monitor`
- `Monitor(conn, chrome=None).tick(now)` / `.run(stop, poll_s=POLL_S)` — every 2s asks Chrome (AppleScript) for the frontmost window's active tab. A visit starts when the URL/tab changes and ends when it changes again or Chrome isn't frontmost; stored in `visits` with real `duration_s` and `from_url` = previous URL in the same tab. Incognito and non-web tabs are ignored. After 3s on a page (and again at 15s if it had no text yet), reads the rendered `<main>` (or `<body>`) via JavaScript: description, breadcrumb, h1–h3 outline and up to 2 text blocks per section (≥5 words, no nav/menus/forms); pages built from bare `<div>`s fall back to their first 6 innermost text `<div>`s → `pages.headings/snippet`; title → `pages.title`.
- Needs Chrome → View → Developer → *Allow JavaScript from Apple Events* for text (visits are recorded without it).

### Watcher — `jump.agents.watcher`, `jump.places`, `jump.scoring`
- `watcher.run_once(conn, now=None) -> dict` — over visits in the last `HISTORY_WINDOW_DAYS`: detect hubs, group URLs into places, compute revisit scores, set place status, upsert visited pages (`visited=1`, `place_id`, `revisit`), post `rescored` jobs `{place_id}` when a place is new, changes status or |Δrevisit| ≥ `RESCORE_DELTA`. One transaction; idempotent.
- Hubs (search results, login redirects) by behavior: same path with mostly distinct queries linking out to many origins, or mostly bounces. Never stored as places or pages.
- Places by path structure: split a level into tenants when children's subtrees share URL shapes (`github.com/<owner>/<repo>`); descend when one child holds ≥80% of visit-days; else the node is the place.
- Revisit score: `x = 1.5·ln(1+days) + 0.5·ln(1+breadth) − 1.0·bounce + 0.5·regular − 3`, `revisit = sigmoid(x)`; `days` = Σ distinct visit days of `0.5^(age/14)`. ≥0.5 active, ≥0.2 probation (dropped 14 days after the last visit), else dropped.

### Planner — `jump.agents.planner`
- `planner.run_once(conn, now=None) -> dict` — consume `rescored` jobs; for active and probation places set `budget = round(MAX_BUDGET·revisit)`, others 0; post `crawl` jobs `{place_id, budget}` (dedupe key = place id) for those places never crawled or with `last_crawled` older than `REFRESH_AFTER_S`.
- Writes: `places.budget`, `jobs(crawl)`.

### Crawler — `jump.agents.crawler`, `jump.crawl.*`
- `crawler.run_pending(conn, max_jobs=None, fetch=None, delay=None) -> dict` — claim `crawl` jobs one at a time and run them. `fetch` injectable for tests.
- `crawler.crawl_place(conn, place_id, budget, fetch=None, delay=None) -> dict` — seeds are the place's pages visited in the last `RECENT_DAYS` (30); links are read only from those, and pages one link away are fetched for their own text (their links aren't followed or saved), best first. Link priority = `parent · HOP_DECAY · rarity · scope · (1 + pattern)`; rarity = `ln(N/n)/ln(N)` over pages in the place containing the link; scope: deeper 1, sideways `SIDEWAYS_FACTOR`, outside 0 (record as unfetched page + link only); pattern = share of visited pages in the place sharing the link's `url_template`. Stop at `MIN_PRIORITY`, budget, or when the one-hop frontier is empty. robots.txt respected, `CRAWL_DELAY_S` between requests, GET only, skip `is_action_url`, never fetch non-HTML kinds (index them via link text).
- Parsing: title, h1–h3 headings, snippet, links with anchor text and enclosing row/list-item context.
- Writes: `pages` (crawled + discovered), `links`, `places.last_crawled`.

### Search — `jump.search.retriever`, `jump.search.jev`, `jump.search.pipeline`
- `retriever.sync_fts(conn) -> int` — rebuild FTS rows for `fts_dirty` pages (title, snippet, headings, incoming anchors, incoming contexts, `url_words`); clear the flag.
- `retriever.retrieve(conn, query, k=CANDIDATES) -> list[dict]` — (BM25 + 2 per matched term) × `(0.5 + revisit)` × `(1 + picks)`; revisit = max(page, its place), else max of places linking to it; alias match ×3. Dicts have `page_id, url, kind, label, description, score`.
- `jev.rank(query, candidates, api_key, client=None) -> list[float] | None` — one `choice` question; returns probability per candidate, None on any failure. `jev.rank_with_confidence` also returns Jev's choice + confidence.
- Candidates are limited to pages visited in the last `RECENT_DAYS` and never-opened pages one link away from them; lookalikes (same site + URL pattern) collapse to one.
- `pipeline.search(conn, query, use_jev=True, api_key=None, client=None) -> list[dict]` — retrieve → Jev (if key, >1 candidate) → Jev probability × (1 + picks), hide < 0.1 → top `RESULTS`, each with `page_id, url, label, kind, probability|None`.
- `pipeline.evaluate(conn, use_jev=True) -> dict` (`jump eval [--local]`) — replays every past pick's query with that pick hidden; reports the share ranked #1 / in the top 5 and the misses.
- `pipeline.record_pick(conn, query, page_id)`, `pipeline.open_in_browser(url)` (macOS `open -a "Google Chrome"`).
- Writes: FTS table, `picks`.

### Namer — `jump.agents.namer`, `jump.naming`
- `namer.run_once(conn) -> dict` — names every page from its own words, no AI: specific incoming link text; else the tab title with unread counts and the site's repeated ending (e.g. "– Ed Discussion", found on 3+ of the site's titles) removed; if that title is shared by 2+ pages of the site or is a default ("PowerPoint Presentation", "Untitled"), the first heading, else "title · last URL segment"; else the file/page name. Only changed names are written.
- Search labels use `name`; Raycast shows the URL path under it for "where".

### Daemon — `jump.daemon`
- `daemon.run(conn, stop=None, workers=2, cycle_s=300, chrome=None)` — monitor thread; watcher → planner → namer every `cycle_s`; crawler worker threads (own connections) draining `crawl` jobs; stale `running` jobs requeued on start.
- `jump.service.install(load=False)` / `uninstall()` — launchd agent (`~/Library/LaunchAgents/com.jump.daemon.plist`): start at login, restart on crash.
