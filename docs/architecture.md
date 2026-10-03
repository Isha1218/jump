# Architecture

Two halves share one SQLite database (`~/.jump/graph.db`, schema in `src/jump/db.py`).

```
BACKGROUND (daemon)                                   FOREGROUND (jump search)
Chrome History ─► WATCHER ─rescored─► PLANNER ─crawl─► CRAWLER ×N      query ─► RETRIEVER ─► ≤200 ─► JEV ─► top 5
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

### Watcher — `jump.agents.watcher`, `jump.history`, `jump.places`, `jump.scoring`
- `watcher.run_once(conn, history_path=None, now=None) -> dict` — copy Chrome History (it is locked while Chrome runs), import new visits into `visits` (incremental by Chrome visit id, `meta['history_last_id']`), group URLs into places, detect hubs, compute revisit scores, set place status, upsert visited pages (`visited=1`, `place_id`, `revisit`), post `rescored` jobs `{place_id}` (dedupe key = place id) when |Δrevisit| ≥ `RESCORE_DELTA`. Returns a summary dict (counts).
- Writes: `visits`, `places`, `pages` (visited pages only), `jobs(rescored)`, `meta`.
- Place grouping by **path fan-out** (no site lists): scope boundary = first path depth where distinct-child count jumps.
- Hubs (search engines, feeds) by behavior: short dwell, outbound to many distinct origins, URL differs mostly by a free-text query param → status `hub`, never crawled.
- Revisit score: `x = 1.5·ln(1+days) + 1.0·typed + 0.5·ln(1+breadth) − 1.0·bounce + 0.5·regular − 3`, `revisit = sigmoid(x)`. `days` = Σ over distinct visit days of `0.5^(age/14)`. Status: ≥0.5 active, ≥0.2 probation (dropped after 14 days without promotion), else dropped. Features stored as JSON in `places.features`.

### Planner — `jump.agents.planner`
- `planner.run_once(conn, now=None) -> dict` — consume `rescored` jobs; for active places set `budget = round(MAX_BUDGET·revisit)`, others 0; post `crawl` jobs `{place_id, budget}` (dedupe key = place id) for active places never crawled or with `last_crawled` older than `REFRESH_AFTER_S`.
- Writes: `places.budget`, `jobs(crawl)`.

### Crawler — `jump.agents.crawler`, `jump.crawl.*`
- `crawler.run_pending(conn, max_jobs=None, fetch=None, delay=None) -> dict` — claim `crawl` jobs one at a time and run them. `fetch` injectable for tests.
- `crawler.crawl_place(conn, place_id, budget, fetch=None, delay=None) -> dict` — best-first crawl seeded from the place's visited pages. Link priority = `parent · HOP_DECAY · rarity · scope · (1 + pattern)`; rarity = `ln(N/n)/ln(N)` over pages in the place containing the link; scope: deeper 1, sideways `SIDEWAYS_FACTOR`, outside 0 (record as unfetched page + link only); pattern = share of visited pages in the place sharing the link's `url_template`. Stop at `MIN_PRIORITY` or budget. robots.txt respected, `CRAWL_DELAY_S` between requests, GET only, skip `is_action_url`, never fetch non-HTML kinds (index them via link text).
- Parsing: title, h1–h3 headings, snippet, links with anchor text and enclosing row/list-item context.
- Writes: `pages` (crawled + discovered), `links`, `places.last_crawled`.

### Search — `jump.search.retriever`, `jump.search.jev`, `jump.search.pipeline`
- `retriever.sync_fts(conn) -> int` — rebuild FTS rows for `fts_dirty` pages (title, snippet, headings, incoming anchors, incoming contexts, `url_words`); clear the flag.
- `retriever.retrieve(conn, query, k=CANDIDATES) -> list[dict]` — (BM25 + 2 per matched term) × `(0.5 + revisit)` × `(1 + picks)`; revisit = max(page, its place), else max of places linking to it; alias match ×3. Dicts have `page_id, url, kind, label, description, score`.
- `jev.rank(query, candidates, api_key, client=None) -> list[float] | None` — one `choice` question; returns probability per candidate, None on any failure. `jev.rank_with_confidence` also returns Jev's choice + confidence.
- `pipeline.search(conn, query, use_jev=True, api_key=None, client=None) -> list[dict]` — retrieve → Jev (if key, >1 candidate) → top `RESULTS`, each with `page_id, url, label, kind, probability|None`.
- `pipeline.record_pick(conn, query, page_id)`, `pipeline.open_in_browser(url)` (macOS `open -a "Google Chrome"`).
- Writes: FTS table, `picks`.

### Daemon — `jump.daemon`
- `daemon.run(conn, history_path=None, stop=None, workers=2, debouncer=None)` — watcher triggered by History file changes (30s debounce, changes during a run stay pending) + 10-min fallback timer; planner after watcher; crawler worker threads (own connections) draining `crawl` jobs; stale `running` jobs requeued on start.
- `jump.service.install(load=False)` / `uninstall()` — launchd agent (`~/Library/LaunchAgents/com.jump.daemon.plist`): start at login, restart on crash.
