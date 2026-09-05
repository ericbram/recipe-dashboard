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
    conn.execute("INSERT INTO plan (week, recipe_id) VALUES ('2026-08-31', 1)")
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


def test_a_new_week_is_empty(conn):
    assert db.get_plan(conn, date(2026, 8, 31)) == []


def test_add_and_read_the_week(conn):
    a = db.create_recipe(conn, "Stew")
    b = db.create_recipe(conn, "Chili")
    db.add_to_plan(conn, date(2026, 9, 2), a)
    db.add_to_plan(conn, date(2026, 9, 4), b)
    # Any day in the week resolves to the same Monday-keyed list, alphabetical.
    assert [r["title"] for r in db.get_plan(conn, date(2026, 8, 31))] == ["Chili", "Stew"]
    assert [r["title"] for r in db.get_plan(conn, date(2026, 9, 6))] == ["Chili", "Stew"]


def test_adding_the_same_recipe_twice_is_a_no_op(conn):
    rid = db.create_recipe(conn, "Chili")
    db.add_to_plan(conn, date(2026, 9, 2), rid)
    db.add_to_plan(conn, date(2026, 9, 5), rid)
    assert len(db.get_plan(conn, date(2026, 8, 31))) == 1


def test_remove_takes_it_off_the_week(conn):
    a = db.create_recipe(conn, "Chili")
    b = db.create_recipe(conn, "Stew")
    db.add_to_plan(conn, date(2026, 9, 2), a)
    db.add_to_plan(conn, date(2026, 9, 2), b)
    db.remove_from_plan(conn, date(2026, 9, 6), a)
    assert [r["title"] for r in db.get_plan(conn, date(2026, 8, 31))] == ["Stew"]


def test_removing_something_not_on_the_list_is_harmless(conn):
    rid = db.create_recipe(conn, "Chili")
    db.remove_from_plan(conn, date(2026, 9, 2), rid)
    assert db.get_plan(conn, date(2026, 8, 31)) == []


def test_weeks_do_not_bleed_into_each_other(conn):
    rid = db.create_recipe(conn, "Chili")
    db.add_to_plan(conn, date(2026, 9, 2), rid)
    assert db.get_plan(conn, date(2026, 9, 9)) == []


def test_deleting_recipe_clears_it_from_plan(conn):
    rid = db.create_recipe(conn, "Chili")
    db.add_to_plan(conn, date(2026, 9, 2), rid)
    db.delete_recipe(conn, rid)
    assert db.get_plan(conn, date(2026, 8, 31)) == []


OLD_PLAN_SCHEMA = """
CREATE TABLE recipes (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, source_url TEXT,
  ingredients TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '',
  rating INTEGER, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE plan (
  date TEXT PRIMARY KEY,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE);
"""


def _old_style_db(tmp_path, rows):
    c = db.connect(str(tmp_path / "old.db"))
    c.executescript(OLD_PLAN_SCHEMA)
    for i, (day, title) in enumerate(rows, start=1):
        c.execute("INSERT INTO recipes (id,title,created_at) VALUES (?,?,'2026-09-01')", (i, title))
        c.execute("INSERT INTO plan (date, recipe_id) VALUES (?, ?)", (day, i))
    c.commit()
    return c


def test_migration_folds_daily_rows_onto_their_week(tmp_path):
    c = _old_style_db(tmp_path, [("2026-08-31", "Chili"), ("2026-09-04", "Stew")])
    db.init_schema(c)
    assert [r["title"] for r in db.get_plan(c, date(2026, 9, 2))] == ["Chili", "Stew"]
    cols = {r["name"] for r in c.execute("PRAGMA table_info(plan)")}
    assert cols == {"week", "recipe_id"}
    assert c.execute(
        "SELECT COUNT(*) c FROM sqlite_master WHERE name='plan_by_day'").fetchone()["c"] == 0
    c.close()


def test_migration_collapses_a_recipe_cooked_on_two_days(tmp_path):
    c = db.connect(str(tmp_path / "dupe.db"))
    c.executescript(OLD_PLAN_SCHEMA)
    c.execute("INSERT INTO recipes (id,title,created_at) VALUES (1,'Chili','2026-09-01')")
    c.execute("INSERT INTO plan (date, recipe_id) VALUES ('2026-08-31', 1)")
    c.execute("INSERT INTO plan (date, recipe_id) VALUES ('2026-09-03', 1)")
    c.commit()
    db.init_schema(c)
    assert [r["title"] for r in db.get_plan(c, date(2026, 8, 31))] == ["Chili"]
    c.close()


def test_migration_splits_rows_across_different_weeks(tmp_path):
    c = _old_style_db(tmp_path, [("2026-08-31", "Chili"), ("2026-09-08", "Stew")])
    db.init_schema(c)
    assert [r["title"] for r in db.get_plan(c, date(2026, 8, 31))] == ["Chili"]
    assert [r["title"] for r in db.get_plan(c, date(2026, 9, 7))] == ["Stew"]
    c.close()


def test_migration_is_idempotent(tmp_path):
    c = _old_style_db(tmp_path, [("2026-09-02", "Chili")])
    db.init_schema(c)
    db.init_schema(c)
    assert [r["title"] for r in db.get_plan(c, date(2026, 8, 31))] == ["Chili"]
    c.close()


def test_staples_round_trip_and_dedupe(conn):
    db.add_staple(conn, "Milk")
    db.add_staple(conn, "  Milk  ")          # same thing, normalized
    db.add_staple(conn, "Coffee")
    assert [s["line"] for s in db.get_staples(conn)] == ["Coffee", "Milk"]


def test_a_blank_staple_is_rejected(conn):
    with pytest.raises(ValueError):
        db.add_staple(conn, "   ")
    assert db.get_staples(conn) == []


def test_remove_staple(conn):
    db.add_staple(conn, "Milk")
    db.remove_staple(conn, db.get_staples(conn)[0]["id"])
    assert db.get_staples(conn) == []


def test_pantry_marks_are_per_week(conn):
    db.set_pantry(conn, date(2026, 9, 2), "1 lb beef", have=True)
    assert db.get_pantry(conn, date(2026, 8, 31)) == {"1 lb beef"}
    assert db.get_pantry(conn, date(2026, 9, 9)) == set(), "last week must not carry over"


def test_pantry_unmark(conn):
    db.set_pantry(conn, date(2026, 9, 2), "1 lb beef", have=True)
    db.set_pantry(conn, date(2026, 9, 4), "1 lb beef", have=False)
    assert db.get_pantry(conn, date(2026, 8, 31)) == set()


def test_marking_twice_is_a_no_op(conn):
    for _ in range(2):
        db.set_pantry(conn, date(2026, 9, 2), "1 lb beef", have=True)
    assert db.get_pantry(conn, date(2026, 8, 31)) == {"1 lb beef"}


def _interrupted_migration(tmp_path):
    """A database left mid-migration: renamed, new table made, copy never ran."""
    c = db.connect(str(tmp_path / "crash.db"))
    c.executescript(OLD_PLAN_SCHEMA)
    c.execute("INSERT INTO recipes (id,title,created_at) VALUES (1,'Chili','2026-09-01')")
    c.execute("INSERT INTO recipes (id,title,created_at) VALUES (2,'Stew','2026-09-01')")
    c.execute("INSERT INTO plan (date,recipe_id) VALUES ('2026-09-02',1)")
    c.execute("INSERT INTO plan (date,recipe_id) VALUES ('2026-09-04',2)")
    c.commit()
    c.execute("ALTER TABLE plan RENAME TO plan_by_day")
    c.executescript(db.SCHEMA)   # implicit COMMIT — the rename is now durable
    return c


def test_an_interrupted_migration_is_finished_on_the_next_start(tmp_path):
    """executescript commits, so the rename and the copy cannot share a
    transaction. A surviving plan_by_day must mean 'resume', not 'done'."""
    c = _interrupted_migration(tmp_path)
    assert db.get_plan(c, date(2026, 8, 31)) == [], "precondition: the copy never ran"

    db.init_schema(c)   # restart

    assert [r["title"] for r in db.get_plan(c, date(2026, 8, 31))] == ["Chili", "Stew"]
    left = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE name='plan_by_day'")}
    assert left == set(), "the old table must be dropped once the rows are safe"
    c.close()


def test_resuming_twice_does_not_duplicate(tmp_path):
    c = _interrupted_migration(tmp_path)
    db.init_schema(c)
    db.init_schema(c)
    assert len(db.get_plan(c, date(2026, 8, 31))) == 2
    c.close()
