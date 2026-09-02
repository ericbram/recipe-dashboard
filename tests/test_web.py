from datetime import date

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


def test_create_recipe_via_form(client):
    resp = client.post(
        "/recipe/new",
        data={"title": "Chili", "ingredients": "beans", "steps": "cook",
              "notes": "", "source_url": "", "tags": "dinner, spicy"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Chili" in resp.text
    assert db.list_recipes(client.conn)[0]["tags"] == "dinner, spicy"


def test_create_requires_a_title(client):
    resp = client.post(
        "/recipe/new",
        data={"title": "  ", "ingredients": "", "steps": "", "notes": "",
              "source_url": "", "tags": ""},
    )
    assert resp.status_code == 400
    assert "Title is required" in resp.text
    assert db.list_recipes(client.conn) == []


def test_recipe_detail_shows_fields(client):
    rid = db.create_recipe(client.conn, "Chili", ingredients="beans", steps="cook")
    body = client.get(f"/recipe/{rid}").text
    assert "Chili" in body and "beans" in body and "cook" in body


def test_missing_recipe_is_404(client):
    resp = client.get("/recipe/999")
    assert resp.status_code == 404
    assert "Recipe not found" in resp.text
    assert "<html" in resp.text.lower()  # an HTML page, not FastAPI's JSON default


def test_edit_recipe(client):
    rid = db.create_recipe(client.conn, "Chili", tags=["dinner"])
    client.post(
        f"/recipe/{rid}",
        data={"title": "Better Chili", "ingredients": "beans", "steps": "cook",
              "notes": "", "source_url": "", "tags": "dinner, quick"},
        follow_redirects=True,
    )
    assert db.get_recipe(client.conn, rid)["title"] == "Better Chili"
    assert db.get_tags(client.conn, rid) == ["dinner", "quick"]


def test_rate_returns_star_fragment(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/rate", data={"rating": "4"},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    # Each star is its own button; count glyphs among the rating buttons
    # rather than asserting a contiguous run (the ✕ clear button's glyph
    # must not be counted as a star).
    rating_buttons = resp.text.split('title="Clear rating"')[0]
    assert rating_buttons.count("★") == 4
    assert rating_buttons.count("☆") == 1
    assert db.get_recipe(client.conn, rid)["rating"] == 4


def test_rate_zero_clears_the_rating(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_rating(client.conn, rid, 5)
    client.post(f"/recipe/{rid}/rate", data={"rating": "0"}, headers={"HX-Request": "true"})
    assert db.get_recipe(client.conn, rid)["rating"] is None


def test_rate_rejects_out_of_range(client):
    rid = db.create_recipe(client.conn, "Chili")
    assert client.post(f"/recipe/{rid}/rate", data={"rating": "9"}).status_code == 400


def test_delete_recipe(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_recipe(client.conn, rid) is None


def test_import_prefills_the_form(client, monkeypatch):
    from app import importer

    monkeypatch.setattr(
        main.importer, "fetch_recipe",
        lambda url: importer.ImportedRecipe(
            title="Weeknight Chili", ingredients="beef\nbeans", steps="cook",
            tags=["dinner"], source_url=url),
    )
    body = client.post("/import", data={"url": "https://example.com/chili"}).text
    assert "Weeknight Chili" in body
    assert "beef" in body
    assert "dinner" in body


def test_failed_import_keeps_the_url_and_explains(client, monkeypatch):
    monkeypatch.setattr(main.importer, "fetch_recipe", lambda url: None)
    body = client.post("/import", data={"url": "https://example.com/nope"}).text
    assert "https://example.com/nope" in body
    assert "Could not read a recipe" in body


def test_javascript_scheme_source_url_is_not_linked(client):
    rid = db.create_recipe(client.conn, "Chili", source_url="javascript:alert(1)")
    body = client.get(f"/recipe/{rid}").text
    assert 'href="javascript:' not in body


def test_http_source_url_is_linked(client):
    rid = db.create_recipe(client.conn, "Chili", source_url="https://example.com/x")
    body = client.get(f"/recipe/{rid}").text
    assert 'href="https://example.com/x"' in body


def test_title_cannot_break_out_of_delete_confirm_js(client):
    evil = "x'); alert(1); //"
    rid = db.create_recipe(client.conn, evil)
    body = client.get(f"/recipe/{rid}").text
    onsubmit = body.split('onsubmit="')[1].split('"')[0]
    assert "alert(1)" not in onsubmit


def test_plan_shows_seven_days(client):
    body = client.get("/plan", params={"week": "2026-09-03"}).text
    for name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        assert name in body


def test_plan_defaults_to_this_week(client):
    assert client.get("/plan").status_code == 200


def test_assign_a_recipe_to_a_day(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post("/plan/2026-09-02", data={"recipe_id": str(rid)},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Chili" in resp.text
    assert dict(db.get_plan(client.conn, date(2026, 8, 31)))[date(2026, 9, 2)]["title"] == "Chili"


def test_clear_a_day(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_plan(client.conn, date(2026, 9, 2), rid)
    client.post("/plan/2026-09-02", data={"recipe_id": ""}, headers={"HX-Request": "true"})
    assert dict(db.get_plan(client.conn, date(2026, 8, 31)))[date(2026, 9, 2)] is None


def test_plan_renders_the_assigned_recipe(client):
    rid = db.create_recipe(client.conn, "Weeknight Chili")
    db.set_plan(client.conn, date(2026, 9, 2), rid)
    assert "Weeknight Chili" in client.get("/plan", params={"week": "2026-09-03"}).text


def test_bad_week_param_is_400(client):
    assert client.get("/plan", params={"week": "not-a-date"}).status_code == 400


def test_bad_plan_date_is_400(client):
    assert client.post("/plan/nonsense", data={"recipe_id": ""}).status_code == 400
