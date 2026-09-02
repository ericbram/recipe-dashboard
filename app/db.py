"""SQLite access for the recipe dashboard. Plain sqlite3, no ORM."""

import sqlite3
from datetime import date, datetime

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


def _norm_tags(tags) -> list[str]:
    seen = {t.strip().lower() for t in tags or ()}
    return sorted(t for t in seen if t)


def _set_tags(conn: sqlite3.Connection, recipe_id: int, tags) -> None:
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        "INSERT INTO recipe_tags (recipe_id, tag) VALUES (?, ?)",
        [(recipe_id, t) for t in _norm_tags(tags)],
    )


def create_recipe(
    conn: sqlite3.Connection,
    title: str,
    *,
    ingredients: str = "",
    steps: str = "",
    notes: str = "",
    source_url: str | None = None,
    tags=(),
) -> int:
    cur = conn.execute(
        """INSERT INTO recipes (title, source_url, ingredients, steps, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, source_url, ingredients, steps, notes, datetime.now().isoformat(timespec="seconds")),
    )
    recipe_id = int(cur.lastrowid)
    _set_tags(conn, recipe_id, tags)
    conn.commit()
    return recipe_id


def get_recipe(conn: sqlite3.Connection, recipe_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()


def get_tags(conn: sqlite3.Connection, recipe_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT tag FROM recipe_tags WHERE recipe_id = ? ORDER BY tag", (recipe_id,)
    )
    return [r["tag"] for r in rows]


def update_recipe(
    conn: sqlite3.Connection,
    recipe_id: int,
    *,
    title: str,
    ingredients: str,
    steps: str,
    notes: str,
    source_url: str | None,
    tags,
) -> None:
    conn.execute(
        """UPDATE recipes
              SET title = ?, ingredients = ?, steps = ?, notes = ?, source_url = ?
            WHERE id = ?""",
        (title, ingredients, steps, notes, source_url, recipe_id),
    )
    _set_tags(conn, recipe_id, tags)
    conn.commit()


def delete_recipe(conn: sqlite3.Connection, recipe_id: int) -> None:
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    conn.commit()
