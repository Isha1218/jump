# Project: Jump
## Overview
Say a phrase, land on the page. Learns which sites you return to by watching the active Chrome tab (no hardcoded lists), crawls the links on the page you're on (one hop), and resolves a query like `452 rpc lecture` to the exact page — including pages you've never opened. Search from Raycast (`raycast/`) or the `jump` CLI.

## Tech Stack
Python 3.12 · SQLite (WAL, FTS5) · httpx · BeautifulSoup/lxml · AppleScript (Chrome) · pytest · Raycast extension (TypeScript, `@raycast/api`) · Jev (TypeSafe AI) for final ranking (optional)

## Architecture
Multi-agent "blackboard": background agents (Monitor → Crawlers; Watcher → Namer) coordinate only through SQLite tables and a `jobs` queue. Foreground search is a plain pipeline: Retriever (FTS5/BM25 × revisit × picks, ≤200) → Jev (one choice call) → top 5. Full contracts: `docs/architecture.md`.

## Key Files
- `src/jump/db.py` schema + helpers · `jobs.py` queue · `urls.py` URL helpers · `config.py` all constants
- `src/jump/monitor.py` reads the active Chrome tab (URL, time on page, rendered text) via AppleScript
- `src/jump/naming.py` + `agents/namer.py` build page names from titles/headings/link text (no AI); shown as result labels
- `src/jump/agents/` watcher, crawler, namer · `src/jump/search/` text (normalize + synonyms), retriever (FTS5 `pages_fts`), jev, pipeline · `src/jump/daemon.py`
- `src/jump/crawl/` parse (title, headings, snippet) · fetch (httpx, robots cache)
- `src/jump/cli.py` `jump` command (`search --json` feeds Raycast)
- `raycast/src/jump.tsx` Raycast "Jump" command: runs `jump search --json`, Enter opens the page in Chrome

## Status
Backend v0 done (monitor, watcher, crawler, search, daemon), CI on every PR. Raycast command done. Next: learned score weights, tuning from real misses.

## How to Run
API keys (optional) go in `~/.jump/keys` as a `JEV_API_KEY=…` line (env vars override). Never in the repo.
```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
# once: Chrome → View → Developer → Allow JavaScript from Apple Events
.venv/bin/jump monitor                                 # record what you look at (Ctrl-C to stop)
.venv/bin/jump watch && .venv/bin/jump places --why   # score places from recorded visits
.venv/bin/jump crawl                                   # run crawls queued by the monitor
.venv/bin/jump name                                    # rebuild page names (the daemon does this every 5 min)
.venv/bin/jump search "452 rpc lecture"                # uses Jev when JEV_API_KEY is set
.venv/bin/jump eval                                    # how well past picks rank (--local skips Jev)
.venv/bin/jump daemon                                  # run all agents continuously
cd raycast && npm install && npm run dev               # load the Raycast command (once; Ctrl-C after it builds)
.venv/bin/jump service install --load                  # keep the daemon running via launchd
```
