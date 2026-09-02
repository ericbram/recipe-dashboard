"""SQLite access for the recipe dashboard. Plain sqlite3, no ORM."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  source_url  TEXT,
  ingredients TEXT NOT NULL DEFAULT '',
  steps       TEXT NOT NULL DEFAULT '',
  rating      INTEGER,
  notes       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_tags (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  tag       TEXT NOT NULL,
  PRIMARY KEY (recipe_id, tag)
);

CREATE TABLE IF NOT EXISTS plan (
  date      TEXT PRIMARY KEY,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recipe_tags_tag ON recipe_tags(tag);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
