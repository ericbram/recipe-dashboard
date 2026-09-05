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

-- A week's shortlist: which recipes we intend to cook, with no day attached.
-- `week` is the ISO date of that week's Monday.
CREATE TABLE IF NOT EXISTS plan (
  week      TEXT NOT NULL,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  PRIMARY KEY (week, recipe_id)
);

-- Things the household buys regardless of what is being cooked. They join
-- every week's kitchen check alongside the recipe ingredients.
CREATE TABLE IF NOT EXISTS staples (
  id   INTEGER PRIMARY KEY,
  line TEXT NOT NULL UNIQUE
);

-- "We already have this, do not buy it." One row per item marked in the
-- kitchen check; absence means it still needs buying. Keyed per week so last
-- week's answers do not silently carry over.
CREATE TABLE IF NOT EXISTS pantry (
  week     TEXT NOT NULL,
  item_key TEXT NOT NULL,
  PRIMARY KEY (week, item_key)
);

-- What aisle an ingredient lives in, learned once and reused forever.
-- Keyed by grocery.sort_key(line), so "1 lb ground beef" and "2 lbs ground
-- beef" share the one entry. Populated by the grocery-sort agent skill.
CREATE TABLE IF NOT EXISTS aisles (
  key   TEXT PRIMARY KEY,
  aisle TEXT NOT NULL
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
    _migrate_plan_to_weeks(conn)
    conn.executescript(SCHEMA)
    conn.commit()


def _migrate_plan_to_weeks(conn: sqlite3.Connection) -> None:
    """The plan used to be one recipe per day, keyed by date. It is now a set
    of recipes per week. Fold each old row onto its week's Monday.

    Resumable rather than transactional. `executescript` issues an implicit
    COMMIT, so the rename below lands before the copy does and the two cannot
    share a transaction. Instead, a surviving `plan_by_day` is the signal that
    a previous run was interrupted: the copy re-runs (INSERT OR IGNORE makes
    it safe to repeat) and only then is the old table dropped. Crashing at any
    point leaves the rows recoverable on the next start.
    """
    def table_names() -> set[str]:
        return {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    present = table_names()
    if "plan" in present and "plan_by_day" not in present:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan)")}
        if "week" not in cols:
            conn.execute("ALTER TABLE plan RENAME TO plan_by_day")
            present = table_names()

    if "plan_by_day" not in present:
        return

    conn.executescript(SCHEMA)  # the new plan table, if it is not there yet
    old = conn.execute("SELECT date, recipe_id FROM plan_by_day").fetchall()
    # Two days holding the same recipe collapse to one entry for the week.
    conn.executemany(
        "INSERT OR IGNORE INTO plan (week, recipe_id) VALUES (?, ?)",
        [(week_start(date.fromisoformat(r["date"])).isoformat(), r["recipe_id"]) for r in old],
    )
    conn.commit()
    conn.execute("DROP TABLE plan_by_day")
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


def get_plan(conn: sqlite3.Connection, week: date) -> list[sqlite3.Row]:
    """The recipes shortlisted for that week, alphabetical."""
    return conn.execute(
        """SELECT r.* FROM plan p JOIN recipes r ON r.id = p.recipe_id
            WHERE p.week = ?
            ORDER BY r.title COLLATE NOCASE""",
        (week_start(week).isoformat(),),
    ).fetchall()


def add_to_plan(conn: sqlite3.Connection, week: date, recipe_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO plan (week, recipe_id) VALUES (?, ?)",
        (week_start(week).isoformat(), recipe_id),
    )
    conn.commit()


def remove_from_plan(conn: sqlite3.Connection, week: date, recipe_id: int) -> None:
    conn.execute(
        "DELETE FROM plan WHERE week = ? AND recipe_id = ?",
        (week_start(week).isoformat(), recipe_id),
    )
    conn.commit()


def get_aisles(conn: sqlite3.Connection) -> dict[str, str]:
    """The whole learned key -> aisle map. It stays small: one row per
    distinct ingredient the household has ever planned."""
    return {r["key"]: r["aisle"] for r in conn.execute("SELECT key, aisle FROM aisles")}


def set_aisles(conn: sqlite3.Connection, mapping: dict[str, str]) -> int:
    """Upsert learned aisles. Returns the number of rows written."""
    rows = [(k, v) for k, v in mapping.items() if k and v]
    conn.executemany(
        "INSERT INTO aisles (key, aisle) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET aisle = excluded.aisle",
        rows,
    )
    conn.commit()
    return len(rows)


def get_staples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, line FROM staples ORDER BY line COLLATE NOCASE").fetchall()


def add_staple(conn: sqlite3.Connection, line: str) -> None:
    line = " ".join((line or "").split())
    if not line:
        raise ValueError("a staple needs a name")
    conn.execute("INSERT OR IGNORE INTO staples (line) VALUES (?)", (line,))
    conn.commit()


def remove_staple(conn: sqlite3.Connection, staple_id: int) -> None:
    conn.execute("DELETE FROM staples WHERE id = ?", (staple_id,))
    conn.commit()


def get_pantry(conn: sqlite3.Connection, week: date) -> set[str]:
    """Item keys marked "we already have this" for that week."""
    rows = conn.execute(
        "SELECT item_key FROM pantry WHERE week = ?", (week_start(week).isoformat(),)
    )
    return {r["item_key"] for r in rows}


def set_pantry(conn: sqlite3.Connection, week: date, item_key: str, have: bool) -> None:
    wk = week_start(week).isoformat()
    if have:
        conn.execute(
            "INSERT OR IGNORE INTO pantry (week, item_key) VALUES (?, ?)", (wk, item_key)
        )
    else:
        conn.execute(
            "DELETE FROM pantry WHERE week = ? AND item_key = ?", (wk, item_key)
        )
    conn.commit()
