import sqlite3

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
