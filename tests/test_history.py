from fake_chrome import FakeHistory

from jump import db, history

NOW = 1_790_000_000.0
DAY = 86400


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM visits ORDER BY chrome_id")]


def test_time_conversion_roundtrip():
    assert history.to_unix(history.to_chrome(NOW)) == NOW
    assert history.to_unix(11644473600 * 10**6) == 0


def test_import_filters_and_resolves_referrer(conn, tmp_path):
    h = FakeHistory(tmp_path / "History")
    s = h.visit("https://www.google.com/search?q=rpc", NOW - 100, transition=1)
    h.visit("https://x.edu/a#frag", NOW - 90, from_visit=s, duration_s=5, title="A")
    h.visit("https://ads.example/frame", NOW - 80, transition=3)            # auto subframe
    h.visit("chrome://settings/", NOW - 70, transition=1)                   # not web
    h.visit("https://x.edu/old", NOW - 200 * DAY)                            # outside window
    h.visit("https://x.edu/a", NOW - 60, transition=8 | 0x30000000)         # reload with chain bits

    n, titles = history.import_visits(conn, h.path, NOW)
    rows = _rows(conn)
    assert n == 3 and [r["url"] for r in rows] == ["https://www.google.com/search?q=rpc", "https://x.edu/a",
                                                   "https://x.edu/a"]
    assert rows[1]["from_url"] == "https://www.google.com/search?q=rpc"
    assert rows[1]["duration_s"] == 5 and rows[0]["transition"] == 1 and rows[2]["transition"] == 8
    assert abs(rows[1]["ts"] - (NOW - 90)) < 1e-3
    assert titles["https://x.edu/a"] == "A"
    assert db.get_meta(conn, "history_last_id") == "6"


def test_import_is_incremental(conn, tmp_path):
    h = FakeHistory(tmp_path / "History")
    h.visit("https://x.edu/a", NOW - 50)
    assert history.import_visits(conn, h.path, NOW)[0] == 1
    assert history.import_visits(conn, h.path, NOW)[0] == 0
    h.visit("https://x.edu/b", NOW - 10)
    assert history.import_visits(conn, h.path, NOW)[0] == 1
    assert len(_rows(conn)) == 2


def test_redirect_chain_keeps_end_and_original_referrer(conn, tmp_path):
    h = FakeHistory(tmp_path / "History")
    s = h.visit("https://search.example/results?q=x", NOW - 30)
    hop = h.visit("https://search.example/url?u=1", NOW - 29, from_visit=s, transition=0x10000000)
    h.visit("https://x.edu/page", NOW - 28, from_visit=hop, transition=0x80000000 | 0x20000000)
    history.import_visits(conn, h.path, NOW)
    rows = _rows(conn)
    assert [r["url"] for r in rows] == ["https://search.example/results?q=x", "https://x.edu/page"]
    assert rows[1]["from_url"] == "https://search.example/results?q=x"


def test_reads_copy_with_journal_present(conn, tmp_path):
    h = FakeHistory(tmp_path / "History")
    h.visit("https://x.edu/a", NOW - 5)
    (tmp_path / "History-journal").write_bytes(b"")
    assert history.import_visits(conn, h.path, NOW)[0] == 1


def test_opener_visit_is_referrer_fallback(conn, tmp_path):
    h = FakeHistory(tmp_path / "History")
    s = h.visit("https://search.example/s?q=x", NOW - 30)
    h.visit("https://x.edu/new-tab", NOW - 20, opener_visit=s)
    history.import_visits(conn, h.path, NOW)
    assert _rows(conn)[1]["from_url"] == "https://search.example/s?q=x"
