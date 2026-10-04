"""Paths and tunable constants. Every number from the scoring spec lives here."""
import os
from pathlib import Path

HOME = Path(os.environ.get("JUMP_HOME", Path.home() / ".jump"))
DB_PATH = HOME / "graph.db"
JEV_API_KEY = os.environ.get("JEV_API_KEY")  # optional; search falls back to local ranking

# Watcher: revisit scoring
HISTORY_WINDOW_DAYS = 90         # visits older than this are ignored
DAY_HALF_LIFE_DAYS = 14          # a visit 14 days ago counts half
PAGE_HALF_LIFE_DAYS = 7          # single pages fade faster than places
BOUNCE_SECONDS = 10
ACTIVE_THRESHOLD = 0.5
PROBATION_THRESHOLD = 0.2
PROBATION_DAYS = 14
RESCORE_DELTA = 0.05             # post a `rescored` job when a score moves this much

# Planner / crawler
MAX_BUDGET = 500                 # budget = MAX_BUDGET * revisit
HOP_DECAY = 0.7
MIN_PRIORITY = 0.05
SIDEWAYS_FACTOR = 0.5
CRAWL_DELAY_S = 1.0
FETCH_TIMEOUT_S = 15
REFRESH_AFTER_S = 24 * 3600
USER_AGENT = "JumpBot/0.1 (personal navigation index)"

# Search
CANDIDATES = 200                 # retriever output size (Jev handles <=255)
RESULTS = 5
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_TIMEOUT_S = 10
