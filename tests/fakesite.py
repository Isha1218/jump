"""Fake `fetch` for crawler tests: a dict of url -> html (or FetchResult); everything else 404."""
from jump.crawl.fetch import FetchResult


class FakeFetch:
    def __init__(self, pages: dict, fallback=None):
        self.pages, self.fallback, self.calls = pages, fallback, []

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None and self.fallback:
            body = self.fallback(url)
        if body is None:
            return FetchResult(404, "text/html", url, "")
        if isinstance(body, FetchResult):
            return body
        ctype = "text/plain" if url.endswith("/robots.txt") else "text/html; charset=utf-8"
        return FetchResult(200, ctype, url, body)

    @property
    def pages_fetched(self) -> list[str]:
        return [u for u in self.calls if not u.endswith("/robots.txt")]
