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
