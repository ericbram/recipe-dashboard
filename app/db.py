"""SQLite access for the recipe dashboard. Plain sqlite3, no ORM."""

import sqlite3
from datetime import date, datetime, timedelta

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


_SORTS = {
    "newest": "r.created_at DESC, r.id DESC",
    "title": "r.title COLLATE NOCASE ASC",
    "rating": "r.rating IS NULL, r.rating DESC, r.title COLLATE NOCASE ASC",
}


def list_recipes(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    tag: str | None = None,
    sort: str = "newest",
) -> list[dict]:
    where, params = [], []
    if q:
        where.append("r.title LIKE ?")
        params.append(f"%{q}%")
    if tag:
        where.append("EXISTS (SELECT 1 FROM recipe_tags t WHERE t.recipe_id = r.id AND t.tag = ?)")
        params.append(tag.strip().lower())

    sql = f"""
        SELECT r.*
          FROM recipes r
         {'WHERE ' + ' AND '.join(where) if where else ''}
         ORDER BY {_SORTS.get(sort, _SORTS['newest'])}
    """
    rows = conn.execute(sql, params).fetchall()

    # Tags are joined in Python: SQLite's GROUP_CONCAT has no portable ordering
    # guarantee, and a correlated subquery in FROM is not reliable across versions.
    by_recipe: dict[int, list[str]] = {}
    for r in conn.execute("SELECT recipe_id, tag FROM recipe_tags ORDER BY tag"):
        by_recipe.setdefault(r["recipe_id"], []).append(r["tag"])

    out = []
    for row in rows:
        rec = dict(row)
        rec["tags"] = ", ".join(by_recipe.get(rec["id"], ()))
        out.append(rec)
    return out


def all_tags(conn: sqlite3.Connection) -> list[str]:
    return [r["tag"] for r in conn.execute("SELECT DISTINCT tag FROM recipe_tags ORDER BY tag")]


def set_rating(conn: sqlite3.Connection, recipe_id: int, rating: int | None) -> None:
    if rating is not None and not 1 <= int(rating) <= 5:
        raise ValueError(f"rating must be 1-5 or None, got {rating!r}")
    conn.execute("UPDATE recipes SET rating = ? WHERE id = ?", (rating, recipe_id))
    conn.commit()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_plan(conn: sqlite3.Connection, start: date) -> list[tuple[date, sqlite3.Row | None]]:
    days = [start + timedelta(days=i) for i in range(7)]
    rows = conn.execute(
        """SELECT p.date AS day, r.*
             FROM plan p JOIN recipes r ON r.id = p.recipe_id
            WHERE p.date BETWEEN ? AND ?""",
        (days[0].isoformat(), days[-1].isoformat()),
    ).fetchall()
    by_day = {date.fromisoformat(r["day"]): r for r in rows}
    return [(d, by_day.get(d)) for d in days]


def set_plan(conn: sqlite3.Connection, d: date, recipe_id: int | None) -> None:
    if recipe_id is None:
        conn.execute("DELETE FROM plan WHERE date = ?", (d.isoformat(),))
    else:
        conn.execute(
            "INSERT INTO plan (date, recipe_id) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET recipe_id = excluded.recipe_id",
            (d.isoformat(), recipe_id),
        )
    conn.commit()
