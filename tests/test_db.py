import sqlite3
from datetime import date, timedelta

import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()


def test_schema_creates_three_tables(conn):
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"recipes", "recipe_tags", "plan"} <= names


def test_rows_are_mappings(conn):
    conn.execute("INSERT INTO recipes (title, created_at) VALUES ('Chili', '2026-09-01')")
    row = conn.execute("SELECT * FROM recipes").fetchone()
    assert row["title"] == "Chili"


def test_foreign_keys_cascade(conn):
    conn.execute("INSERT INTO recipes (id, title, created_at) VALUES (1, 'Chili', '2026-09-01')")
    conn.execute("INSERT INTO recipe_tags (recipe_id, tag) VALUES (1, 'dinner')")
    conn.execute("INSERT INTO plan (date, recipe_id) VALUES ('2026-09-01', 1)")
    conn.execute("DELETE FROM recipes WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) c FROM recipe_tags").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM plan").fetchone()["c"] == 0


def test_init_schema_is_idempotent(tmp_path):
    c = db.connect(str(tmp_path / "twice.db"))
    db.init_schema(c)
    db.init_schema(c)
    c.close()


def test_create_and_get_recipe(conn):
    rid = db.create_recipe(
        conn,
        "Chili",
        ingredients="beans\ntomato",
        steps="cook\nserve",
        notes="freezes well",
        source_url="https://example.com/chili",
        tags=["Dinner", "spicy"],
    )
    row = db.get_recipe(conn, rid)
    assert row["title"] == "Chili"
    assert row["ingredients"] == "beans\ntomato"
    assert row["rating"] is None
    assert row["created_at"]
    assert db.get_tags(conn, rid) == ["dinner", "spicy"]


def test_get_missing_recipe_returns_none(conn):
    assert db.get_recipe(conn, 999) is None


def test_tags_are_normalized(conn):
    rid = db.create_recipe(conn, "Soup", tags=["  Dinner ", "dinner", "", "QUICK"])
    assert db.get_tags(conn, rid) == ["dinner", "quick"]


def test_update_replaces_fields_and_tags(conn):
    rid = db.create_recipe(conn, "Soup", tags=["dinner"])
    db.update_recipe(
        conn,
        rid,
        title="Better Soup",
        ingredients="water",
        steps="boil",
        notes="",
        source_url=None,
        tags=["lunch", "quick"],
    )
    row = db.get_recipe(conn, rid)
    assert row["title"] == "Better Soup"
    assert row["ingredients"] == "water"
    assert db.get_tags(conn, rid) == ["lunch", "quick"]


def test_update_preserves_rating(conn):
    rid = db.create_recipe(conn, "Soup")
    conn.execute("UPDATE recipes SET rating = 4 WHERE id = ?", (rid,))
    db.update_recipe(
        conn, rid, title="Soup", ingredients="", steps="", notes="",
        source_url=None, tags=[],
    )
    assert db.get_recipe(conn, rid)["rating"] == 4


def test_delete_recipe(conn):
    rid = db.create_recipe(conn, "Soup", tags=["dinner"])
    db.delete_recipe(conn, rid)
    assert db.get_recipe(conn, rid) is None
    assert db.get_tags(conn, rid) == []


def test_list_returns_newest_first_by_default(conn):
    a = db.create_recipe(conn, "Alpha")
    b = db.create_recipe(conn, "Beta")
    ids = [r["id"] for r in db.list_recipes(conn)]
    assert ids == [b, a]


def test_list_search_matches_title_case_insensitively(conn):
    db.create_recipe(conn, "Chicken Curry")
    db.create_recipe(conn, "Beef Stew")
    titles = [r["title"] for r in db.list_recipes(conn, q="curry")]
    assert titles == ["Chicken Curry"]


def test_list_filters_by_tag(conn):
    db.create_recipe(conn, "Chili", tags=["dinner"])
    db.create_recipe(conn, "Toast", tags=["breakfast"])
    titles = [r["title"] for r in db.list_recipes(conn, tag="dinner")]
    assert titles == ["Chili"]


def test_list_sorts_by_title(conn):
    db.create_recipe(conn, "Zucchini")
    db.create_recipe(conn, "Apple")
    titles = [r["title"] for r in db.list_recipes(conn, sort="title")]
    assert titles == ["Apple", "Zucchini"]


def test_list_sorts_by_rating_with_unrated_last(conn):
    low = db.create_recipe(conn, "Low")
    high = db.create_recipe(conn, "High")
    db.create_recipe(conn, "Unrated")
    db.set_rating(conn, low, 2)
    db.set_rating(conn, high, 5)
    titles = [r["title"] for r in db.list_recipes(conn, sort="rating")]
    assert titles == ["High", "Low", "Unrated"]


def test_unknown_sort_falls_back_to_newest(conn):
    a = db.create_recipe(conn, "Alpha")
    b = db.create_recipe(conn, "Beta")
    assert [r["id"] for r in db.list_recipes(conn, sort="'; DROP TABLE recipes")] == [b, a]


def test_list_rows_include_joined_tags(conn):
    db.create_recipe(conn, "Chili", tags=["spicy", "dinner"])
    db.create_recipe(conn, "Toast")
    rows = {r["title"]: r["tags"] for r in db.list_recipes(conn)}
    assert rows["Chili"] == "dinner, spicy"
    assert rows["Toast"] == ""


def test_all_tags_is_distinct_and_sorted(conn):
    db.create_recipe(conn, "Chili", tags=["dinner", "spicy"])
    db.create_recipe(conn, "Stew", tags=["dinner"])
    assert db.all_tags(conn) == ["dinner", "spicy"]


def test_set_and_clear_rating(conn):
    rid = db.create_recipe(conn, "Chili")
    db.set_rating(conn, rid, 4)
    assert db.get_recipe(conn, rid)["rating"] == 4
    db.set_rating(conn, rid, None)
    assert db.get_recipe(conn, rid)["rating"] is None


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_set_rating_rejects_out_of_range(conn, bad):
    rid = db.create_recipe(conn, "Chili")
    with pytest.raises(ValueError):
        db.set_rating(conn, rid, bad)


def test_week_start_returns_monday():
    assert db.week_start(date(2026, 9, 3)) == date(2026, 8, 31)   # Thursday -> Monday
    assert db.week_start(date(2026, 8, 31)) == date(2026, 8, 31)  # Monday -> itself
    assert db.week_start(date(2026, 9, 6)) == date(2026, 8, 31)   # Sunday -> Monday


def test_get_plan_returns_seven_days(conn):
    week = db.get_plan(conn, date(2026, 8, 31))
    expected = [date(2026, 8, 31) + timedelta(days=i) for i in range(7)]
    assert [d for d, _ in week] == expected
    assert all(recipe is None for _, recipe in week)


def test_set_and_read_plan(conn):
    rid = db.create_recipe(conn, "Chili")
    db.set_plan(conn, date(2026, 9, 2), rid)
    week = dict(db.get_plan(conn, date(2026, 8, 31)))
    assert week[date(2026, 9, 2)]["title"] == "Chili"
    assert week[date(2026, 9, 1)] is None


def test_set_plan_replaces_existing_day(conn):
    a = db.create_recipe(conn, "Chili")
    b = db.create_recipe(conn, "Stew")
    db.set_plan(conn, date(2026, 9, 2), a)
    db.set_plan(conn, date(2026, 9, 2), b)
    week = dict(db.get_plan(conn, date(2026, 8, 31)))
    assert week[date(2026, 9, 2)]["title"] == "Stew"


def test_set_plan_none_clears_day(conn):
    rid = db.create_recipe(conn, "Chili")
    db.set_plan(conn, date(2026, 9, 2), rid)
    db.set_plan(conn, date(2026, 9, 2), None)
    assert dict(db.get_plan(conn, date(2026, 8, 31)))[date(2026, 9, 2)] is None


def test_deleting_recipe_clears_it_from_plan(conn):
    rid = db.create_recipe(conn, "Chili")
    db.set_plan(conn, date(2026, 9, 2), rid)
    db.delete_recipe(conn, rid)
    assert dict(db.get_plan(conn, date(2026, 8, 31)))[date(2026, 9, 2)] is None
