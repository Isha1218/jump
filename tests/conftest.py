import pytest

from jump import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "graph.db")
    yield c
    c.close()
