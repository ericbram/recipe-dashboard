import sqlite3

from app import db, migrate_sqlite

LEGACY = """
CREATE TABLE recipes (id INTEGER PRIMARY KEY, title TEXT NOT NULL, source_url TEXT,
  ingredients TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '',
  rating INTEGER, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE recipe_tags (recipe_id INTEGER NOT NULL, tag TEXT NOT NULL);
CREATE TABLE plan (week TEXT NOT NULL, recipe_id INTEGER NOT NULL);
CREATE TABLE staples (id INTEGER PRIMARY KEY, line TEXT NOT NULL);
CREATE TABLE pantry (week TEXT NOT NULL, item_key TEXT NOT NULL);
CREATE TABLE aisles (key TEXT PRIMARY KEY, aisle TEXT NOT NULL);
INSERT INTO recipes (id, title, rating, created_at) VALUES (7, 'Chili', 4, '2026-09-01T10:00:00');
INSERT INTO recipe_tags VALUES (7, 'dinner');
INSERT INTO plan VALUES ('2026-08-31', 7);
INSERT INTO staples VALUES (3, 'Milk');
INSERT INTO pantry VALUES ('2026-08-31', 'milk');
INSERT INTO aisles VALUES ('beef', 'Meat');
"""


def _legacy():
    src = sqlite3.connect(":memory:")
    src.executescript(LEGACY)
    return src


def test_rows_land_with_their_ids_and_relations(conn):
    migrate_sqlite.migrate(_legacy(), conn)
    assert db.get_recipe(conn, 7)["rating"] == 4
    assert db.get_tags(conn, 7) == ["dinner"]
    assert [r["title"] for r in db.get_plan(conn, db.date(2026, 8, 31))] == ["Chili"]
    assert [s["line"] for s in db.get_staples(conn)] == ["Milk"]
    assert db.get_aisles(conn) == {"beef": "Meat"}


def test_new_rows_after_migrating_do_not_collide(conn):
    migrate_sqlite.migrate(_legacy(), conn)
    assert db.create_recipe(conn, "Stew") > 7
    db.add_staple(conn, "Eggs")
    assert len(db.get_staples(conn)) == 2


def test_running_twice_does_not_duplicate(conn):
    migrate_sqlite.migrate(_legacy(), conn)
    migrate_sqlite.migrate(_legacy(), conn)
    assert len(db.list_recipes(conn)) == 1
