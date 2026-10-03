"""Polite HTTP GET and robots.txt. Never raises: network errors come back as status 0."""
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.robotparser import RobotFileParser

import httpx

from .. import config, urls

MAX_BYTES = 5 * 1024 * 1024
ROBOTS_TTL_S = 24 * 3600
HTML_TYPES = ("text/html", "application/xhtml+xml")


@dataclass
class FetchResult:
    status: int                 # HTTP status, 0 on network error
    content_type: str = ""
    final_url: str = ""
    text: str | None = None     # body, only for HTML / text responses

    @property
    def is_html(self) -> bool:
        return self.content_type.split(";")[0].strip().lower() in HTML_TYPES


Fetch = Callable[[str], FetchResult]

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=config.FETCH_TIMEOUT_S,
                                   follow_redirects=True)
        return _client


def fetch(url: str) -> FetchResult:
    """GET `url`; read the body only for HTML / text (and robots.txt), capped at MAX_BYTES."""
    try:
        with _get_client().stream("GET", url) as r:
            ctype = r.headers.get("content-type", "")
            res = FetchResult(r.status_code, ctype, urls.normalize(str(r.url)) or str(r.url))
            main = ctype.split(";")[0].strip().lower()
            if main in HTML_TYPES or main.startswith("text/") or url.endswith("/robots.txt"):
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                res.text = bytes(body).decode(r.charset_encoding or "utf-8", "replace")
            return res
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError):
        return FetchResult(0, final_url=url)


class Robots:
    """robots.txt cache per origin, fetched through the given `fetch`."""

    def __init__(self, fetch_: Fetch = fetch, ttl_s: float = ROBOTS_TTL_S):
        self.fetch, self.ttl_s = fetch_, ttl_s
        self._cache: dict[str, tuple[float, RobotFileParser]] = {}
        self._lock = threading.Lock()

    def _parser(self, origin: str) -> RobotFileParser | None:
        with self._lock:
            hit = self._cache.get(origin)
        if hit and time.time() - hit[0] < self.ttl_s:
            return hit[1]
        r = self.fetch(origin + "/robots.txt")
        rp = RobotFileParser()
        if r.status == 0 or r.status >= 500:
            return None                      # temporarily unknown: disallow, don't cache
        if r.status in (401, 403):
            rp.disallow_all = True
        elif r.status >= 400:
            rp.allow_all = True
        else:
            rp.parse((r.text or "").splitlines())
        with self._lock:
            self._cache[origin] = (time.time(), rp)
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._parser(urls.origin(url))
        return bool(rp and rp.can_fetch(config.USER_AGENT, url))


default_robots = Robots()
