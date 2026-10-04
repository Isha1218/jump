import json

from jump import cli, db


def test_search_json(tmp_path, capsys):
    path = tmp_path / "g.db"
    conn = db.connect(path)
    pid = db.upsert_place(conn, "https://www.amazon.com", "/", status="active", revisit=0.6, alias="amazon")
    db.upsert_page(conn, "https://www.amazon.com/gp/css/order-history", place_id=pid, visited=1, title="Your Orders")
    conn.close()
    cli.main(["--db", str(path), "search", "amazon orders", "--local", "--json"])
    results = json.loads(capsys.readouterr().out)
    assert results[0]["url"] == "https://www.amazon.com/gp/css/order-history"
    assert results[0]["label"] == "Your Orders" and results[0]["probability"] is None
