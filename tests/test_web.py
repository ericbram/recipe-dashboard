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


def test_rate_without_hx_header_redirects(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/rate", data={"rating": "4"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/recipe/{rid}"
    assert db.get_recipe(client.conn, rid)["rating"] == 4


def test_delete_recipe(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_recipe(client.conn, rid) is None


def test_delete_missing_recipe_is_404(client):
    assert client.post("/recipe/9999/delete").status_code == 404


def test_cross_origin_post_is_rejected(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/delete", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert db.get_recipe(client.conn, rid) is not None


def test_post_without_origin_header_succeeds(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert db.get_recipe(client.conn, rid) is None


def test_post_with_matching_origin_succeeds(client):
    rid = db.create_recipe(client.conn, "Chili")
    host = client.base_url.host
    resp = client.post(f"/recipe/{rid}/delete", headers={"Origin": f"http://{host}"},
                       follow_redirects=False)
    assert resp.status_code == 303
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


def test_plan_starts_empty_with_a_picker(client):
    body = client.get("/plan", params={"week": "2026-09-03"}).text
    assert "Nothing picked for this week yet" in body
    assert 'action="/plan/add"' in body


def test_plan_defaults_to_this_week(client):
    assert client.get("/plan").status_code == 200


def test_add_a_recipe_to_the_week(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": str(rid)},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Chili" in resp.text
    assert [r["title"] for r in db.get_plan(client.conn, date(2026, 8, 31))] == ["Chili"]


def test_add_several_and_they_all_stay(client):
    for t in ["Chili", "Stew", "Tacos"]:
        rid = db.create_recipe(client.conn, t)
        client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": str(rid)})
    titles = [r["title"] for r in db.get_plan(client.conn, date(2026, 8, 31))]
    assert titles == ["Chili", "Stew", "Tacos"]


def test_remove_takes_one_off_and_leaves_the_rest(client):
    ids = {t: db.create_recipe(client.conn, t) for t in ["Chili", "Stew"]}
    for rid in ids.values():
        client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": str(rid)})
    resp = client.post("/plan/remove",
                       data={"week": "2026-09-02", "recipe_id": str(ids["Chili"])},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert [r["title"] for r in db.get_plan(client.conn, date(2026, 8, 31))] == ["Stew"]


def test_a_recipe_already_on_the_list_is_not_offered_again(client):
    rid = db.create_recipe(client.conn, "Chili")
    assert f'<option value="{rid}"' in client.get("/plan", params={"week": "2026-09-02"}).text
    client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": str(rid)})
    body = client.get("/plan", params={"week": "2026-09-02"}).text
    assert f'<option value="{rid}"' not in body
    assert "Chili" in body  # still shown, as a picked item


def test_add_without_hx_header_redirects_to_the_week(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": str(rid)},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/plan?week=2026-08-31"


def test_non_numeric_recipe_id_is_400(client):
    assert client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": "abc"}).status_code == 400


def test_adding_a_missing_recipe_is_404(client):
    assert client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": "9999"}).status_code == 404


def test_plan_renders_the_picked_recipe(client):
    rid = db.create_recipe(client.conn, "Weeknight Chili")
    db.add_to_plan(client.conn, date(2026, 9, 2), rid)
    assert "Weeknight Chili" in client.get("/plan", params={"week": "2026-09-03"}).text


def test_bad_week_param_is_400(client):
    assert client.get("/plan", params={"week": "not-a-date"}).status_code == 400


def test_bad_week_on_add_is_400(client):
    assert client.post("/plan/add", data={"week": "nonsense", "recipe_id": "1"}).status_code == 400


def test_htmx_error_returns_a_bare_message_not_a_page(client):
    """htmx will not swap a non-2xx page, so errors must come back as text."""
    resp = client.post(
        "/plan/add",
        data={"week": "2026-09-02", "recipe_id": "9999"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 404
    assert resp.text == "Recipe not found"
    assert "<!doctype" not in resp.text.lower()


def test_non_htmx_error_still_renders_the_full_page(client):
    resp = client.post("/plan/add", data={"week": "2026-09-02", "recipe_id": "9999"})
    assert resp.status_code == 404
    assert "<!doctype" in resp.text.lower()


def test_paste_import_prefills_the_form(client):
    blob = '{"title": "Pasted Pie", "ingredients": ["Apples"], "tags": ["dessert"], "notes": "Serves 6."}'
    resp = client.post("/import/paste", data={"recipe": blob})
    assert resp.status_code == 200
    assert 'value="Pasted Pie"' in resp.text
    assert "Apples" in resp.text
    assert "dessert" in resp.text
    assert "Serves 6." in resp.text


def test_paste_import_of_junk_returns_the_form_with_an_error(client):
    resp = client.post("/import/paste", data={"recipe": "nonsense"})
    assert resp.status_code == 200
    assert "did not parse as recipe JSON" in resp.text


def test_pasted_recipe_can_be_saved(client):
    """The end-to-end path: paste blob -> prefilled form -> saved recipe."""
    client.post("/import/paste", data={"recipe": '{"title": "Pasted Pie"}'})
    resp = client.post("/recipe/new", data={"title": "Pasted Pie", "tags": "dessert"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert "Pasted Pie" in resp.text


def test_api_create_returns_the_new_recipe(client):
    resp = client.post("/api/recipes", json={
        "title": "API Tacos",
        "ingredients": ["Tortillas", "Carnitas"],
        "steps": "Warm the tortillas.",
        "tags": ["Dinner", "pork"],
        "notes": "Serves 4.",
        "source_url": "https://example.com/tacos",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "API Tacos"
    assert body["url"] == f"/recipe/{body['id']}"

    page = client.get(body["url"])
    assert page.status_code == 200
    assert "Carnitas" in page.text
    assert "Serves 4." in page.text
    assert "dinner, pork" in page.text


def test_api_rejects_a_body_with_no_title(client):
    resp = client.post("/api/recipes", json={"steps": "Do a thing."})
    assert resp.status_code == 400
    assert resp.json() == {"error": "Needs at least a non-empty title"}


def test_api_errors_are_json_not_an_html_page(client):
    """The error handler must not hand an API client a rendered error page."""
    resp = client.post("/api/recipes", content=b"not json",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json() == {"error": "Body must be JSON"}
    assert "<!doctype" not in resp.text.lower()


def test_api_create_is_still_blocked_cross_origin(client):
    resp = client.post("/api/recipes", json={"title": "Evil Pie"},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def _stock_the_week(client):
    beef = client.post("/api/recipes", json={
        "title": "Chili", "ingredients": ["1 lb ground beef", "1 onion, diced"]}).json()
    soup = client.post("/api/recipes", json={
        "title": "Soup", "ingredients": ["1 onion, diced", "4 cups stock"]}).json()
    client.post("/plan/add", data={"week": "2026-08-31", "recipe_id": str(beef["id"])})
    client.post("/plan/add", data={"week": "2026-08-31", "recipe_id": str(soup["id"])})
    return beef, soup


def test_grocery_page_lists_the_weeks_ingredients(client):
    _stock_the_week(client)
    resp = client.get("/grocery?week=2026-09-01")
    assert resp.status_code == 200
    assert "1 lb ground beef" in resp.text
    assert "4 cups stock" in resp.text
    # The shared onion is one row, credited to both dinners. Count the
    # checkbox, which is emitted exactly once per item.
    assert resp.text.count('value="1 onion, diced"') == 1
    assert resp.text.count('type="checkbox"') == 3
    assert "Chili, Soup" in resp.text
    assert "3 items for 2 recipes" in resp.text


def test_grocery_page_is_empty_when_nothing_is_planned(client):
    resp = client.get("/grocery?week=2026-09-01")
    assert resp.status_code == 200
    assert "Nothing planned this week yet" in resp.text


def test_grocery_page_rejects_a_bad_week(client):
    assert client.get("/grocery?week=not-a-date").status_code == 400


def test_plan_page_links_to_the_grocery_list(client):
    resp = client.get("/plan?week=2026-09-01")
    assert '/grocery?week=2026-08-31' in resp.text


def test_grocery_api_reports_what_needs_sorting(client):
    _stock_the_week(client)
    body = client.get("/api/grocery?week=2026-09-01").json()
    assert body["week_start"] == "2026-08-31"
    assert "meat" in body["aisles"]
    assert sorted(body["unsorted_keys"]) == ["ground beef", "onion diced", "stock"]
    beef = next(i for i in body["items"] if i["line"] == "1 lb ground beef")
    assert beef == {"line": "1 lb ground beef", "key": "ground beef",
                    "aisle": "unsorted", "sources": ["Chili"], "have": False}


def test_learned_aisles_apply_and_shrink_the_next_run(client):
    _stock_the_week(client)
    resp = client.post("/api/aisles", json={"ground beef": "meat", "onion diced": "produce"})
    assert resp.status_code == 200
    assert resp.json() == {"learned": 2, "known": 2}

    body = client.get("/api/grocery?week=2026-09-01").json()
    assert body["unsorted_keys"] == ["stock"]

    page = client.get("/grocery?week=2026-09-01")
    assert "1 not yet in an aisle" in page.text
    assert page.text.index("produce") < page.text.index("meat") < page.text.index("unsorted")


def test_a_relearned_aisle_overwrites_rather_than_duplicating(client):
    client.post("/api/aisles", json={"capers": "produce"})
    resp = client.post("/api/aisles", json={"capers": "pantry"})
    assert resp.json() == {"learned": 1, "known": 1}
    assert db.get_aisles(client.conn) == {"capers": "pantry"}


def test_aisles_api_rejects_a_name_outside_the_vocabulary(client):
    resp = client.post("/api/aisles", json={"beef": "butcher counter"})
    assert resp.status_code == 400
    assert "butcher counter" in resp.json()["error"]
    assert db.get_aisles(client.conn) == {}, "a rejected batch must write nothing"


def test_aisles_api_rejects_an_empty_or_non_object_body(client):
    assert client.post("/api/aisles", json={}).status_code == 400
    assert client.post("/api/aisles", json=["beef", "meat"]).status_code == 400


def test_kitchen_lists_ingredients_and_staples_together(client):
    _stock_the_week(client)
    db.add_staple(client.conn, "Milk")
    body = client.get("/kitchen?week=2026-09-01").text
    assert "1 lb ground beef" in body
    assert "Milk" in body
    assert "3 proposed" not in body        # 3 ingredients + 1 staple
    assert "4 proposed" in body


def test_ticking_an_item_removes_it_from_the_grocery_list(client):
    _stock_the_week(client)
    resp = client.post("/kitchen/have", data={
        "week": "2026-09-01", "item_key": "1 lb ground beef", "have": "1",
    }, headers={"HX-Request": "true"})
    assert resp.status_code == 200

    assert db.get_pantry(client.conn, date(2026, 8, 31)) == {"1 lb ground beef"}
    grocery_page = client.get("/grocery?week=2026-09-01").text
    assert "1 lb ground beef" not in grocery_page
    assert "4 cups stock" in grocery_page
    assert "1 already in the kitchen" in grocery_page


def test_unticking_puts_it_back_on_the_grocery_list(client):
    _stock_the_week(client)
    for flag in ("1", "0"):
        client.post("/kitchen/have", data={
            "week": "2026-09-01", "item_key": "1 lb ground beef", "have": flag})
    assert "1 lb ground beef" in client.get("/grocery?week=2026-09-01").text


def test_the_tick_is_keyed_by_line_not_by_display_case(client):
    _stock_the_week(client)
    client.post("/kitchen/have", data={
        "week": "2026-09-01", "item_key": "  1 LB Ground Beef  ", "have": "1"})
    assert db.get_pantry(client.conn, date(2026, 8, 31)) == {"1 lb ground beef"}


def test_a_staple_appears_on_the_grocery_list_until_ticked(client):
    db.add_staple(client.conn, "Milk")
    assert "Milk" in client.get("/grocery?week=2026-09-01").text
    client.post("/kitchen/have", data={"week": "2026-09-01", "item_key": "Milk", "have": "1"})
    assert "Milk" not in client.get("/grocery?week=2026-09-01").text


def test_add_and_remove_a_staple_through_the_page(client):
    resp = client.post("/staples/add", data={"week": "2026-09-01", "line": "Coffee"},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Coffee" in resp.text
    sid = db.get_staples(client.conn)[0]["id"]
    client.post("/staples/remove", data={"week": "2026-09-01", "staple_id": str(sid)})
    assert db.get_staples(client.conn) == []


def test_a_blank_staple_is_a_400(client):
    assert client.post("/staples/add", data={"week": "2026-09-01", "line": "  "}).status_code == 400


def test_kitchen_toggle_without_hx_redirects(client):
    _stock_the_week(client)
    resp = client.post("/kitchen/have", data={
        "week": "2026-09-01", "item_key": "1 lb ground beef", "have": "1"},
        follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/kitchen?week=2026-08-31"


def test_api_grocery_reports_the_have_flag(client):
    _stock_the_week(client)
    client.post("/kitchen/have", data={
        "week": "2026-09-01", "item_key": "1 lb ground beef", "have": "1"})
    body = client.get("/api/grocery?week=2026-09-01").json()
    flags = {i["line"]: i["have"] for i in body["items"]}
    assert flags["1 lb ground beef"] is True
    assert flags["4 cups stock"] is False
    # Still listed, so its aisle can be learned even though we are not buying it.
    assert "ground beef" in body["unsorted_keys"]


def test_grocery_says_you_have_everything_rather_than_nothing_is_planned(client):
    """An empty list because you own it all must not read as an empty week."""
    rid = client.post("/api/recipes", json={"title": "Toast", "ingredients": ["Bread"]}).json()["id"]
    client.post("/plan/add", data={"week": "2026-09-01", "recipe_id": str(rid)})
    client.post("/kitchen/have", data={"week": "2026-09-01", "item_key": "Bread", "have": "1"})
    body = client.get("/grocery?week=2026-09-01").text
    assert "Nothing to buy" in body
    assert "Nothing planned this week yet" not in body
