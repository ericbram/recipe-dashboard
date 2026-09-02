import pytest
from fastapi.testclient import TestClient

from app import db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the startup hook at a throwaway file so the suite never touches
    # the real ./recipes.db, then override the dependency to share one
    # connection between the test body and the app.
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "startup.db"))
    conn = db.connect(str(tmp_path / "web.db"))
    db.init_schema(conn)
    main.app.dependency_overrides[main.get_db] = lambda: conn
    with TestClient(main.app) as c:
        c.conn = conn
        yield c
    main.app.dependency_overrides.clear()
    conn.close()


def test_index_is_empty_at_first(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No recipes yet" in resp.text


def test_index_lists_recipes(client):
    db.create_recipe(client.conn, "Weeknight Chili", tags=["dinner"])
    body = client.get("/").text
    assert "Weeknight Chili" in body
    assert "dinner" in body


def test_index_search_filters(client):
    db.create_recipe(client.conn, "Chicken Curry")
    db.create_recipe(client.conn, "Beef Stew")
    body = client.get("/", params={"q": "curry"}).text
    assert "Chicken Curry" in body
    assert "Beef Stew" not in body


def test_index_tag_filter(client):
    db.create_recipe(client.conn, "Chili", tags=["dinner"])
    db.create_recipe(client.conn, "Toast", tags=["breakfast"])
    body = client.get("/", params={"tag": "dinner"}).text
    assert "Chili" in body
    assert "Toast" not in body


def test_plain_request_returns_full_page(client):
    db.create_recipe(client.conn, "Chili")
    body = client.get("/").text
    assert "<html" in body.lower()


def test_htmx_request_returns_fragment_only(client):
    db.create_recipe(client.conn, "Chili")
    body = client.get("/", headers={"HX-Request": "true"}).text
    assert "Chili" in body
    assert "<html" not in body.lower()


def test_stars_renders_rating(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_rating(client.conn, rid, 3)
    assert "★★★☆☆" in client.get("/").text
