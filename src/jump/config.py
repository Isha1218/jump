"""Paths and tunable constants. Every number from the scoring spec lives here."""
import os
from pathlib import Path

HOME = Path(os.environ.get("JUMP_HOME", Path.home() / ".jump"))
DB_PATH = HOME / "graph.db"
KEYS_FILE = HOME / "keys"        # local `NAME=value` lines; never in the repo


def load_keys(path: Path = KEYS_FILE) -> dict[str, str]:
    keys = {}
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and not name.startswith("#"):
                keys[name.strip()] = value.strip().strip("'\"")
    return keys


_keys = load_keys()
# Environment variables win over the keys file.
JEV_API_KEY = os.environ.get("JEV_API_KEY") or _keys.get("JEV_API_KEY")  # optional; search falls back to local ranking

# Watcher: revisit scoring
HISTORY_WINDOW_DAYS = 90         # visits older than this are ignored
DAY_HALF_LIFE_DAYS = 14          # a visit 14 days ago counts half
PAGE_HALF_LIFE_DAYS = 7          # single pages fade faster than places
BOUNCE_SECONDS = 10
ACTIVE_THRESHOLD = 0.5
PROBATION_THRESHOLD = 0.2
PROBATION_DAYS = 14

# Planner / crawler / search
RECENT_DAYS = 30                 # only pages visited this recently (and pages one link away) count
PAGE_BUDGET = 50                 # most pages fetched per page you're on
CRAWL_DELAY_S = 1.0
FETCH_TIMEOUT_S = 15
REFRESH_AFTER_S = 24 * 3600      # don't crawl the same page again sooner
USER_AGENT = "JumpBot/0.1 (personal navigation index)"

# Search
CANDIDATES = 200                 # retriever output size (Jev handles <=255)
RESULTS = 5
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_TIMEOUT_S = 10
