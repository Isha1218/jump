import pytest

from jump import config, db


@pytest.fixture(autouse=True)
def no_recency_window(monkeypatch):
    """Most tests build pages without visit dates; tests of the 30-day window turn it back on."""
    monkeypatch.setattr(config, "RECENT_DAYS", None)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "graph.db")
    yield c
    c.close()
