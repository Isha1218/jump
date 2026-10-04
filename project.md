# Project: Jump
## Overview
Say a phrase, land on the page. Learns which sites you return to by watching the active Chrome tab (no hardcoded lists), crawls those places ahead of time, and resolves a query like `452 rpc lecture` to the exact page — including pages you've never opened. Search from Raycast (`raycast/`) or the `jump` CLI.

## Tech Stack
Python 3.12 · SQLite (WAL, FTS5) · httpx · BeautifulSoup/lxml · AppleScript (Chrome) · pytest · Raycast extension (TypeScript, `@raycast/api`) · Jev (TypeSafe AI) for final ranking (optional)

## Architecture
Multi-agent "blackboard": background agents (Monitor → Watcher → Planner → Crawlers) coordinate only through SQLite tables and a `jobs` queue. Foreground search is a plain pipeline: Retriever (FTS5/BM25 × revisit × picks, ≤200) → Jev (one choice call) → top 5. Full contracts: `docs/architecture.md`.

## Key Files
- `src/jump/db.py` schema + helpers · `jobs.py` queue · `urls.py` URL helpers · `config.py` all constants
- `src/jump/monitor.py` reads the active Chrome tab (URL, time on page, rendered text) via AppleScript
- `src/jump/agents/` watcher, planner, crawler · `src/jump/search/` text (normalize + synonyms), retriever (FTS5 `pages_fts`), jev, pipeline · `src/jump/daemon.py`
- `src/jump/crawl/` parse (links + row context) · fetch (httpx, robots cache) · priority (rarity, lazy best-first frontier)
- `src/jump/cli.py` `jump` command (`search --json` feeds Raycast)
- `raycast/src/jump.tsx` Raycast "Jump" command: runs `jump search --json`, Enter opens the page in Chrome

## Status
Backend v0 done (monitor, watcher, planner, crawler, search, daemon), CI on every PR. Raycast command done. Next: learned score weights, tuning from real misses.

## How to Run
```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
# once: Chrome → View → Developer → Allow JavaScript from Apple Events
.venv/bin/jump monitor                                 # record what you look at (Ctrl-C to stop)
.venv/bin/jump watch && .venv/bin/jump places --why   # score places from recorded visits
.venv/bin/jump plan && .venv/bin/jump crawl            # crawl active places
.venv/bin/jump search "452 rpc lecture"                # JEV_API_KEY=... to enable Jev
.venv/bin/jump daemon                                  # run all agents continuously
cd raycast && npm install && npm run dev               # load the Raycast command (once; Ctrl-C after it builds)
.venv/bin/jump service install --load                  # keep the daemon running via launchd
```
