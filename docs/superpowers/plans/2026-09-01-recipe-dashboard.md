# Recipe Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted household recipe dashboard with a searchable, categorized, star-rated recipe library and a weekly dinner plan.

**Architecture:** One FastAPI process renders Jinja templates against a single SQLite file. HTMX swaps page fragments for in-place updates, so there is no JavaScript build step. Data access is plain `sqlite3` in `app/db.py`; every route handler is thin.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2, HTMX (single CDN-free vendored file), stdlib `sqlite3`, `httpx`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-recipe-dashboard-design.md`

## Global Constraints

- Python 3.11 or newer.
- No ORM. Data access is stdlib `sqlite3` with explicit SQL in `app/db.py`.
- No authentication anywhere. The app trusts its network.
- No JavaScript build step, no `node_modules`. HTMX is one vendored file in `app/static/`.
- Runtime dependencies are limited to: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `httpx`. Dev adds `pytest` only.
- Weeks run Monday through Sunday.
- Every connection sets `PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL`.
- Dinner is the only planned meal; one recipe per date.
- Ratings are integers 1–5, or `NULL` for unrated.
- Ingredients and steps are stored as newline-delimited text, never as child tables.
- The database path comes from the `DB_PATH` environment variable, defaulting to `./recipes.db`.
- Git in this repo uses the personal identity already configured locally; do not touch global git config.

---

### Task 1: Project scaffold and database schema

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/db.py`
- Create: `tests/__init__.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.db.connect(db_path: str) -> sqlite3.Connection` and `app.db.init_schema(conn: sqlite3.Connection) -> None`. `connect` returns a connection whose `row_factory` is `sqlite3.Row`, with foreign keys and WAL enabled.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "recipe-dashboard"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "jinja2",
    "python-multipart",
    "httpx",
]

[project.optional-dependencies]
dev = ["pytest"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the environment and install**

```bash
cd /Users/ericbram/code/recipe-dashboard
python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
```

- [ ] **Step 3: Create the package directories and markers**

```bash
mkdir -p app/templates app/static tests/fixtures
touch app/__init__.py tests/__init__.py
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_db.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError` or `AttributeError: module 'app.db' has no attribute 'connect'`.

- [ ] **Step 6: Write the minimal implementation**

Create `app/db.py`:

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml app/__init__.py app/db.py tests/__init__.py tests/test_db.py
git commit -m "feat: project scaffold and SQLite schema"
```

---

### Task 2: Recipe create, read, update, delete with tags

**Files:**
- Modify: `app/db.py` (append functions)
- Test: `tests/test_db.py` (append tests)

**Interfaces:**
- Consumes: `db.connect`, `db.init_schema` from Task 1.
- Produces:
  - `create_recipe(conn, title, *, ingredients="", steps="", notes="", source_url=None, tags=()) -> int` returns the new recipe id.
  - `get_recipe(conn, recipe_id: int) -> sqlite3.Row | None`
  - `get_tags(conn, recipe_id: int) -> list[str]` sorted alphabetically.
  - `update_recipe(conn, recipe_id, *, title, ingredients, steps, notes, source_url, tags) -> None` replaces the tag set wholesale.
  - `delete_recipe(conn, recipe_id: int) -> None`
  - Tags are normalized: stripped, lowercased, empties dropped, duplicates collapsed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `module 'app.db' has no attribute 'create_recipe'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/db.py` (add `from datetime import date, datetime` at the top of the file; `date` is used in Task 4):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: recipe CRUD with normalized tags"
```

---

### Task 3: Listing, search, tag filter, sorting, and rating

**Files:**
- Modify: `app/db.py` (append functions)
- Test: `tests/test_db.py` (append tests)

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces:
  - `list_recipes(conn, *, q: str | None = None, tag: str | None = None, sort: str = "newest") -> list[dict]`. `sort` accepts `"newest"`, `"title"`, `"rating"`; anything else falls back to `"newest"`. Rating sort is descending with unrated recipes last. Each dict holds every `recipes` column plus a `tags` key: the recipe's tags joined by `", "` (empty string when none). Dicts rather than `sqlite3.Row` because the tag string is assembled in Python and `Row` is immutable; templates index both the same way.
  - `all_tags(conn) -> list[str]` — every distinct tag in use, alphabetical.
  - `set_rating(conn, recipe_id: int, rating: int | None) -> None`. Values outside 1–5 raise `ValueError`; `None` clears the rating.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `module 'app.db' has no attribute 'list_recipes'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/db.py`:

```python
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
```

`LIKE` is case-insensitive for ASCII in SQLite by default, which is what the search test relies on. The `sort` value is never interpolated from user input — it is looked up in `_SORTS`, so an unknown value degrades to `newest` rather than reaching SQL.

The second query reads every row of `recipe_tags` rather than only the tags for the listed recipes. At household scale that is a few hundred rows and one round trip; if the library ever grows enough for that to matter, add `WHERE recipe_id IN (...)` built from the ids just fetched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: recipe listing, search, tag filter, sort, and ratings"
```

---

### Task 4: Weekly dinner plan

**Files:**
- Modify: `app/db.py` (append functions)
- Test: `tests/test_db.py` (append tests)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces:
  - `week_start(d: date) -> date` — the Monday of `d`'s week.
  - `get_plan(conn, start: date) -> list[tuple[date, sqlite3.Row | None]]` — exactly seven entries, Monday through Sunday, each paired with the planned recipe row or `None`.
  - `set_plan(conn, d: date, recipe_id: int | None) -> None` — assigns, replaces, or (with `None`) clears that day.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py` (add `from datetime import date, timedelta` to the imports at the top of the file):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `module 'app.db' has no attribute 'week_start'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `app/db.py` (add `from datetime import timedelta` to the existing datetime import):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: weekly dinner plan storage"
```

---

### Task 5: Recipe import from a URL

**Files:**
- Create: `app/importer.py`
- Create: `tests/fixtures/jsonld_recipe.html`
- Create: `tests/fixtures/no_recipe.html`
- Test: `tests/test_importer.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ImportedRecipe` dataclass with fields `title: str`, `ingredients: str`, `steps: str`, `tags: list[str]`, `source_url: str`.
  - `parse_recipe(html: str, source_url: str = "") -> ImportedRecipe | None` — pure, no network.
  - `fetch_recipe(url: str) -> ImportedRecipe | None` — fetches then parses; returns `None` on any network or parse failure, never raises.

- [ ] **Step 1: Create the fixtures**

Create `tests/fixtures/jsonld_recipe.html`:

```html
<!doctype html>
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "WebPage", "name": "ignore me"},
    {
      "@type": "Recipe",
      "name": "Weeknight Chili",
      "recipeIngredient": ["1 lb beef", "2 cans beans"],
      "recipeInstructions": [
        {"@type": "HowToStep", "text": "Brown the beef."},
        {"@type": "HowToStep", "text": "Add beans and simmer."}
      ],
      "recipeCategory": ["Dinner", "Main Course"]
    }
  ]
}
</script>
</head><body><p>Chili</p></body></html>
```

Create `tests/fixtures/no_recipe.html`:

```html
<!doctype html>
<html><head>
<script type="application/ld+json">{"@type": "Article", "name": "Not a recipe"}</script>
</head><body><p>An article about chili.</p></body></html>
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_importer.py`:

```python
from pathlib import Path

from app import importer

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_jsonld_recipe():
    html = (FIXTURES / "jsonld_recipe.html").read_text()
    r = importer.parse_recipe(html, "https://example.com/chili")
    assert r is not None
    assert r.title == "Weeknight Chili"
    assert r.ingredients == "1 lb beef\n2 cans beans"
    assert r.steps == "Brown the beef.\nAdd beans and simmer."
    assert r.tags == ["dinner", "main course"]
    assert r.source_url == "https://example.com/chili"


def test_returns_none_when_no_recipe_present():
    assert importer.parse_recipe((FIXTURES / "no_recipe.html").read_text()) is None


def test_returns_none_on_garbage_html():
    assert importer.parse_recipe("<html><body>hello</body></html>") is None


def test_survives_malformed_json():
    html = '<script type="application/ld+json">{not json at all</script>'
    assert importer.parse_recipe(html) is None


def test_instructions_as_plain_string():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": "Toast", "recipeIngredient": ["bread"],
     "recipeInstructions": "Toast the bread."}</script>"""
    r = importer.parse_recipe(html)
    assert r.steps == "Toast the bread."


def test_instructions_as_list_of_strings():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": "Toast", "recipeIngredient": ["bread"],
     "recipeInstructions": ["Slice.", "Toast."]}</script>"""
    r = importer.parse_recipe(html)
    assert r.steps == "Slice.\nToast."


def test_type_may_be_a_list():
    html = """<script type="application/ld+json">
    {"@type": ["Recipe", "NewsArticle"], "name": "Toast",
     "recipeIngredient": ["bread"], "recipeInstructions": "Toast."}</script>"""
    assert importer.parse_recipe(html).title == "Toast"


def test_recipe_without_a_name_is_rejected():
    html = '<script type="application/ld+json">{"@type": "Recipe", "recipeIngredient": ["x"]}</script>'
    assert importer.parse_recipe(html) is None


def test_fetch_returns_none_on_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(importer.httpx, "get", boom)
    assert importer.fetch_recipe("https://example.com/whatever") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.importer'`.

- [ ] **Step 4: Write the minimal implementation**

Create `app/importer.py`:

```python
"""Import a recipe from a URL by reading its schema.org JSON-LD.

Every major recipe site emits JSON-LD because Google requires it for rich
results, so this needs no site-specific code. If a site you care about turns
out not to emit it, the `recipe-scrapers` library is the upgrade path.
"""

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; recipe-dashboard/0.1)"
TIMEOUT = 10.0


@dataclass
class ImportedRecipe:
    title: str
    ingredients: str = ""
    steps: str = ""
    tags: list[str] = field(default_factory=list)
    source_url: str = ""


class _JsonLdCollector(HTMLParser):
    """Collects the body of every <script type="application/ld+json"> block."""

    def __init__(self):
        super().__init__()
        self.blocks: list[str] = []
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._capturing = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._capturing = False

    def handle_data(self, data):
        if self._capturing:
            self.blocks.append(data)


def _walk(node):
    """Yield every dict nested anywhere inside a decoded JSON document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == "recipe" for x in types)


def _lines(value) -> str:
    """Normalize a string / list of strings / list of HowToStep into text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    out = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("name") or ""
        else:
            text = ""
        text = text.strip()
        if text:
            out.append(text)
    return "\n".join(out)


def _tags(value) -> list[str]:
    raw = [value] if isinstance(value, str) else (value or [])
    seen = {str(t).strip().lower() for t in raw if isinstance(t, (str, int))}
    return sorted(t for t in seen if t)


def parse_recipe(html: str, source_url: str = "") -> ImportedRecipe | None:
    collector = _JsonLdCollector()
    try:
        collector.feed(html)
    except Exception:
        return None

    for block in collector.blocks:
        try:
            doc = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(doc):
            if not _is_recipe(node):
                continue
            title = (node.get("name") or "").strip()
            if not title:
                continue
            return ImportedRecipe(
                title=title,
                ingredients=_lines(node.get("recipeIngredient")),
                steps=_lines(node.get("recipeInstructions")),
                tags=_tags(node.get("recipeCategory")),
                source_url=source_url,
            )
    return None


def fetch_recipe(url: str) -> ImportedRecipe | None:
    """Fetch and parse. Returns None on any failure — import never blocks entry."""
    try:
        resp = httpx.get(
            url,
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return parse_recipe(resp.text, url)
    except Exception:
        return None
```

`_tags` sorts, so `["Dinner", "Main Course"]` becomes `["dinner", "main course"]`, matching the test.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
git add app/importer.py tests/test_importer.py tests/fixtures/
git commit -m "feat: import recipes from a URL via schema.org JSON-LD"
```

---

### Task 6: Web app skeleton and the recipe list page

**Files:**
- Create: `app/main.py`
- Create: `app/templates/base.html`
- Create: `app/templates/index.html`
- Create: `app/templates/_list.html`
- Create: `app/static/style.css`
- Create: `app/static/htmx.min.js`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_schema`, `db.list_recipes`, `db.all_tags`, `db.create_recipe`.
- Produces:
  - `app.main.app` — the FastAPI application.
  - `app.main.get_db()` — a FastAPI dependency yielding a connection, overridable in tests.
  - `app.main.templates` — the configured `Jinja2Templates` instance.
  - A `stars(rating)` Jinja global returning a 5-character string like `"★★★☆☆"`, or `"—"` when `rating` is `None`.
  - The convention later routes follow: when the request header `HX-Request` is present, render just the fragment template; otherwise render the full page.

- [ ] **Step 1: Vendor HTMX**

```bash
curl -sSL -o app/static/htmx.min.js https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
test -s app/static/htmx.min.js && head -c 40 app/static/htmx.min.js
```

Expected: a non-empty file. If the download fails, the app still works — every interaction has a plain-form fallback — but fix it before deploying.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web.py`:

```python
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


def test_htmx_request_returns_fragment_only(client):
    db.create_recipe(client.conn, "Chili")
    body = client.get("/", headers={"HX-Request": "true"}).text
    assert "Chili" in body
    assert "<html" not in body.lower()


def test_stars_renders_rating(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_rating(client.conn, rid, 3)
    assert "★★★☆☆" in client.get("/").text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 4: Write `app/main.py`**

```python
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

BASE_DIR = Path(__file__).parent
DB_PATH = os.environ.get("DB_PATH", "./recipes.db")

_conn = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _conn
    _conn = db.connect(DB_PATH)
    db.init_schema(_conn)
    yield
    _conn.close()


app = FastAPI(title="Recipe Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_db():
    return _conn


def stars(rating) -> str:
    if not rating:
        return "—"
    return "★" * int(rating) + "☆" * (5 - int(rating))


templates.env.globals["stars"] = stars


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: str = "", sort: str = "newest", conn=Depends(get_db)):
    ctx = {
        "recipes": db.list_recipes(conn, q=q or None, tag=tag or None, sort=sort),
        "tags": db.all_tags(conn),
        "q": q,
        "tag": tag,
        "sort": sort,
    }
    # Full page normally; the bare list fragment when HTMX asks for it.
    name = "_list.html" if request.headers.get("HX-Request") else "index.html"
    return templates.TemplateResponse(request, name, ctx)
```

- [ ] **Step 5: Write the templates**

Create `app/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Recipes{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="/static/htmx.min.js" defer></script>
</head>
<body>
  <nav>
    <a href="/">Recipes</a>
    <a href="/plan">This week</a>
    <a href="/recipe/new" class="primary">Add recipe</a>
  </nav>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

Create `app/templates/index.html`:

```html
{% extends "base.html" %}
{% block content %}
<form class="filters" hx-get="/" hx-target="#list" hx-trigger="input changed delay:200ms from:#q, change from:#sort">
  <input id="q" name="q" type="search" placeholder="Search recipes" value="{{ q }}">
  <select id="sort" name="sort">
    <option value="newest" {% if sort == 'newest' %}selected{% endif %}>Newest</option>
    <option value="title"  {% if sort == 'title'  %}selected{% endif %}>Title</option>
    <option value="rating" {% if sort == 'rating' %}selected{% endif %}>Rating</option>
  </select>
  <input type="hidden" name="tag" value="{{ tag }}">
  <noscript><button type="submit">Go</button></noscript>
</form>

<div class="chips">
  <a href="/?q={{ q }}&sort={{ sort }}" class="chip {% if not tag %}on{% endif %}">All</a>
  {% for t in tags %}
    <a href="/?q={{ q }}&sort={{ sort }}&tag={{ t }}" class="chip {% if t == tag %}on{% endif %}">{{ t }}</a>
  {% endfor %}
</div>

<div id="list">{% include "_list.html" %}</div>
{% endblock %}
```

Create `app/templates/_list.html`:

```html
{% if not recipes %}
  <p class="empty">No recipes yet. <a href="/recipe/new">Add one</a>.</p>
{% else %}
  <ul class="cards">
    {% for r in recipes %}
      <li class="card">
        <a href="/recipe/{{ r['id'] }}">{{ r['title'] }}</a>
        <span class="stars">{{ stars(r['rating']) }}</span>
        {% if r['tags'] %}<span class="tags">{{ r['tags'] }}</span>{% endif %}
      </li>
    {% endfor %}
  </ul>
{% endif %}
```

Create `app/static/style.css`:

```css
:root { --bg:#fbfaf8; --fg:#22201d; --muted:#6b6560; --line:#e4e0d9; --accent:#b4531f; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; }
nav { display:flex; gap:1rem; align-items:center; padding:1rem; border-bottom:1px solid var(--line); }
nav a { color:var(--fg); text-decoration:none; }
nav a.primary { margin-left:auto; color:var(--accent); font-weight:600; }
main { max-width:52rem; margin:0 auto; padding:1.5rem 1rem 4rem; }
.filters { display:flex; gap:.5rem; margin-bottom:1rem; }
.filters input[type=search] { flex:1; }
input, select, textarea, button { font:inherit; padding:.5rem; border:1px solid var(--line);
  border-radius:6px; background:#fff; color:inherit; }
textarea { width:100%; min-height:8rem; }
.chips { display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:1rem; }
.chip { padding:.2rem .6rem; border:1px solid var(--line); border-radius:999px;
  font-size:.85rem; text-decoration:none; color:var(--muted); background:#fff; }
.chip.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.cards { list-style:none; padding:0; margin:0; display:grid; gap:.5rem; }
.card { display:flex; align-items:center; gap:.75rem; padding:.75rem 1rem;
  background:#fff; border:1px solid var(--line); border-radius:8px; }
.card a { font-weight:600; color:inherit; text-decoration:none; }
.stars { margin-left:auto; color:var(--accent); white-space:nowrap; }
.tags { color:var(--muted); font-size:.85rem; }
.empty { color:var(--muted); }
.week { display:grid; gap:.5rem; }
.day { display:flex; align-items:center; gap:.75rem; padding:.75rem 1rem;
  background:#fff; border:1px solid var(--line); border-radius:8px; }
.day .name { width:6.5rem; color:var(--muted); }
.day.today { border-color:var(--accent); }
.error { color:#a11; }
.field { display:block; margin-bottom:1rem; }
.field span { display:block; font-size:.85rem; color:var(--muted); margin-bottom:.25rem; }
.field input, .field textarea { width:100%; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1b1a18; --fg:#ece8e2; --muted:#9a938c; --line:#35322e; --accent:#e0813f; }
  input, select, textarea, button, .card, .day, .chip { background:#232120; }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/ app/static/ tests/test_web.py
git commit -m "feat: web app skeleton and recipe list page"
```

---

### Task 7: Recipe detail, create, edit, rate, delete, and URL import

**Files:**
- Modify: `app/main.py` (append routes and the 404 handler)
- Create: `app/templates/recipe.html`
- Create: `app/templates/form.html`
- Create: `app/templates/_stars.html`
- Create: `app/templates/error.html`
- Test: `tests/test_web.py` (append tests)

**Interfaces:**
- Consumes: everything from Tasks 1–6, plus `importer.fetch_recipe`.
- Produces: the routes `GET/POST /recipe/new`, `GET/POST /recipe/{id}`, `POST /recipe/{id}/rate`, `POST /recipe/{id}/delete`, `POST /import`. Tags arrive from forms as a comma-separated string and are split on commas before reaching `db`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
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
    assert "★★★★☆" in resp.text
    assert db.get_recipe(client.conn, rid)["rating"] == 4


def test_rate_zero_clears_the_rating(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_rating(client.conn, rid, 5)
    client.post(f"/recipe/{rid}/rate", data={"rating": "0"}, headers={"HX-Request": "true"})
    assert db.get_recipe(client.conn, rid)["rating"] is None


def test_rate_rejects_out_of_range(client):
    rid = db.create_recipe(client.conn, "Chili")
    assert client.post(f"/recipe/{rid}/rate", data={"rating": "9"}).status_code == 400


def test_delete_recipe(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post(f"/recipe/{rid}/delete", follow_redirects=True)
    assert resp.status_code == 200
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: FAIL — 404s and `AttributeError: module 'app.main' has no attribute 'importer'`.

- [ ] **Step 3: Append the routes to `app/main.py`**

Add `from fastapi import Form, HTTPException` and `from fastapi.responses import RedirectResponse` to the imports, add `from app import importer`, then append.

Route order matters: `GET /recipe/new` must be declared **before** `GET /recipe/{recipe_id}`, or FastAPI matches `new` as a recipe id and returns a validation error. The order below is correct — keep it.

```python
@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html",
        {"status": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _form_ctx(request: Request, **over):
    ctx = {"title": "", "ingredients": "", "steps": "", "notes": "",
           "source_url": "", "tags": "", "recipe_id": None, "error": None}
    ctx.update(over)
    return ctx


@app.get("/recipe/new", response_class=HTMLResponse)
def new_recipe_form(request: Request):
    return templates.TemplateResponse(request, "form.html", _form_ctx(request))


@app.post("/recipe/new")
def create_recipe(
    request: Request,
    title: str = Form(""),
    ingredients: str = Form(""),
    steps: str = Form(""),
    notes: str = Form(""),
    source_url: str = Form(""),
    tags: str = Form(""),
    conn=Depends(get_db),
):
    if not title.strip():
        ctx = _form_ctx(request, title=title, ingredients=ingredients, steps=steps,
                        notes=notes, source_url=source_url, tags=tags,
                        error="Title is required.")
        return templates.TemplateResponse(request, "form.html", ctx, status_code=400)
    rid = db.create_recipe(conn, title.strip(), ingredients=ingredients, steps=steps,
                           notes=notes, source_url=source_url or None,
                           tags=_split_tags(tags))
    return RedirectResponse(f"/recipe/{rid}", status_code=303)


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
def recipe_detail(request: Request, recipe_id: int, conn=Depends(get_db)):
    row = db.get_recipe(conn, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return templates.TemplateResponse(
        request, "recipe.html",
        {"r": row, "tags": db.get_tags(conn, recipe_id)},
    )


@app.post("/recipe/{recipe_id}")
def edit_recipe(
    request: Request,
    recipe_id: int,
    title: str = Form(""),
    ingredients: str = Form(""),
    steps: str = Form(""),
    notes: str = Form(""),
    source_url: str = Form(""),
    tags: str = Form(""),
    conn=Depends(get_db),
):
    if db.get_recipe(conn, recipe_id) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if not title.strip():
        ctx = _form_ctx(request, title=title, ingredients=ingredients, steps=steps,
                        notes=notes, source_url=source_url, tags=tags,
                        recipe_id=recipe_id, error="Title is required.")
        return templates.TemplateResponse(request, "form.html", ctx, status_code=400)
    db.update_recipe(conn, recipe_id, title=title.strip(), ingredients=ingredients,
                     steps=steps, notes=notes, source_url=source_url or None,
                     tags=_split_tags(tags))
    return RedirectResponse(f"/recipe/{recipe_id}", status_code=303)


@app.post("/recipe/{recipe_id}/rate", response_class=HTMLResponse)
def rate_recipe(request: Request, recipe_id: int, rating: int = Form(...), conn=Depends(get_db)):
    if db.get_recipe(conn, recipe_id) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        db.set_rating(conn, recipe_id, rating or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return templates.TemplateResponse(
        request, "_stars.html", {"r": db.get_recipe(conn, recipe_id)},
    )


@app.post("/recipe/{recipe_id}/delete")
def remove_recipe(recipe_id: int, conn=Depends(get_db)):
    db.delete_recipe(conn, recipe_id)
    return RedirectResponse("/", status_code=303)


@app.post("/import", response_class=HTMLResponse)
def import_recipe(request: Request, url: str = Form("")):
    found = importer.fetch_recipe(url) if url.strip() else None
    if found is None:
        ctx = _form_ctx(request, source_url=url,
                        error="Could not read a recipe from that page. Fill it in below.")
    else:
        ctx = _form_ctx(request, title=found.title, ingredients=found.ingredients,
                        steps=found.steps, tags=", ".join(found.tags),
                        source_url=found.source_url)
    return templates.TemplateResponse(request, "form.html", ctx)
```

- [ ] **Step 4: Write the templates**

Create `app/templates/error.html`:

```html
{% extends "base.html" %}
{% block title %}{{ status }}{% endblock %}
{% block content %}
<h1>{{ status }}</h1>
<p class="error">{{ detail }}</p>
<p><a href="/">Back to recipes</a></p>
{% endblock %}
```

Create `app/templates/_stars.html`:

```html
<span class="stars" id="stars-{{ r['id'] }}">
  {% for n in range(1, 6) %}
    <button hx-post="/recipe/{{ r['id'] }}/rate" hx-vals='{"rating": {{ n }}}'
            hx-target="#stars-{{ r['id'] }}" hx-swap="outerHTML"
            title="{{ n }} star{{ '' if n == 1 else 's' }}">{{ '★' if r['rating'] and n <= r['rating'] else '☆' }}</button>
  {% endfor %}
  <button hx-post="/recipe/{{ r['id'] }}/rate" hx-vals='{"rating": 0}'
          hx-target="#stars-{{ r['id'] }}" hx-swap="outerHTML" title="Clear rating">✕</button>
</span>
```

Create `app/templates/recipe.html`:

```html
{% extends "base.html" %}
{% block title %}{{ r['title'] }}{% endblock %}
{% block content %}
<h1>{{ r['title'] }}</h1>
{% include "_stars.html" %}
{% if tags %}<p class="tags">{{ tags | join(', ') }}</p>{% endif %}
{% if r['source_url'] %}<p><a href="{{ r['source_url'] }}" rel="noreferrer">Original recipe</a></p>{% endif %}

<h2>Ingredients</h2>
<ul>{% for line in r['ingredients'].splitlines() %}{% if line.strip() %}<li>{{ line }}</li>{% endif %}{% endfor %}</ul>

<h2>Steps</h2>
<ol>{% for line in r['steps'].splitlines() %}{% if line.strip() %}<li>{{ line }}</li>{% endif %}{% endfor %}</ol>

{% if r['notes'] %}<h2>Notes</h2><p>{{ r['notes'] }}</p>{% endif %}

<details>
  <summary>Edit</summary>
  <form method="post" action="/recipe/{{ r['id'] }}">
    <label class="field"><span>Title</span><input name="title" value="{{ r['title'] }}" required></label>
    <label class="field"><span>Tags (comma separated)</span><input name="tags" value="{{ tags | join(', ') }}"></label>
    <label class="field"><span>Ingredients (one per line)</span><textarea name="ingredients">{{ r['ingredients'] }}</textarea></label>
    <label class="field"><span>Steps (one per line)</span><textarea name="steps">{{ r['steps'] }}</textarea></label>
    <label class="field"><span>Notes</span><textarea name="notes">{{ r['notes'] }}</textarea></label>
    <label class="field"><span>Source URL</span><input name="source_url" value="{{ r['source_url'] or '' }}"></label>
    <button type="submit">Save</button>
  </form>
</details>

<form method="post" action="/recipe/{{ r['id'] }}/delete"
      onsubmit="return confirm('Delete {{ r['title'] }}?')">
  <button type="submit">Delete recipe</button>
</form>
{% endblock %}
```

Create `app/templates/form.html`:

```html
{% extends "base.html" %}
{% block title %}{{ 'Edit recipe' if recipe_id else 'Add recipe' }}{% endblock %}
{% block content %}
<h1>{{ 'Edit recipe' if recipe_id else 'Add recipe' }}</h1>

{% if not recipe_id %}
<form method="post" action="/import" class="filters">
  <input name="url" type="url" placeholder="Paste a recipe URL to import" value="{{ source_url }}">
  <button type="submit">Import</button>
</form>
{% endif %}

{% if error %}<p class="error">{{ error }}</p>{% endif %}

<form method="post" action="{{ '/recipe/' ~ recipe_id if recipe_id else '/recipe/new' }}">
  <label class="field"><span>Title</span><input name="title" value="{{ title }}" required></label>
  <label class="field"><span>Tags (comma separated)</span><input name="tags" value="{{ tags }}"></label>
  <label class="field"><span>Ingredients (one per line)</span><textarea name="ingredients">{{ ingredients }}</textarea></label>
  <label class="field"><span>Steps (one per line)</span><textarea name="steps">{{ steps }}</textarea></label>
  <label class="field"><span>Notes</span><textarea name="notes">{{ notes }}</textarea></label>
  <label class="field"><span>Source URL</span><input name="source_url" value="{{ source_url }}"></label>
  <button type="submit">Save</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS. The list page's `stars(...)` global and the interactive `_stars.html` fragment coexist — the list uses the plain string, the detail page uses the clickable buttons.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/ tests/test_web.py
git commit -m "feat: recipe detail, editing, rating, deletion, and URL import"
```

Note: the exception handler makes every `HTTPException` render HTML, including
the 400s raised in Task 8. `test_bad_week_param_is_400` there checks only the
status code, so it holds either way.

---

### Task 8: Weekly plan page

**Files:**
- Modify: `app/main.py` (append routes)
- Create: `app/templates/plan.html`
- Create: `app/templates/_day.html`
- Test: `tests/test_web.py` (append tests)

**Interfaces:**
- Consumes: `db.week_start`, `db.get_plan`, `db.set_plan`, `db.list_recipes`.
- Produces: `GET /plan` (optional `week=YYYY-MM-DD` query param, any day in the target week) and `POST /plan/{date}` taking a `recipe_id` form field where an empty value clears the day. The POST returns the `_day.html` fragment for HTMX and redirects to `/plan` otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py` (add `from datetime import date` to the imports at the top of the file):

```python
def test_plan_shows_seven_days(client):
    body = client.get("/plan", params={"week": "2026-09-03"}).text
    for name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        assert name in body


def test_plan_defaults_to_this_week(client):
    assert client.get("/plan").status_code == 200


def test_assign_a_recipe_to_a_day(client):
    rid = db.create_recipe(client.conn, "Chili")
    resp = client.post("/plan/2026-09-02", data={"recipe_id": str(rid)},
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Chili" in resp.text
    assert dict(db.get_plan(client.conn, date(2026, 8, 31)))[date(2026, 9, 2)]["title"] == "Chili"


def test_clear_a_day(client):
    rid = db.create_recipe(client.conn, "Chili")
    db.set_plan(client.conn, date(2026, 9, 2), rid)
    client.post("/plan/2026-09-02", data={"recipe_id": ""}, headers={"HX-Request": "true"})
    assert dict(db.get_plan(client.conn, date(2026, 8, 31)))[date(2026, 9, 2)] is None


def test_plan_renders_the_assigned_recipe(client):
    rid = db.create_recipe(client.conn, "Weeknight Chili")
    db.set_plan(client.conn, date(2026, 9, 2), rid)
    assert "Weeknight Chili" in client.get("/plan", params={"week": "2026-09-03"}).text


def test_bad_week_param_is_400(client):
    assert client.get("/plan", params={"week": "not-a-date"}).status_code == 400


def test_bad_plan_date_is_400(client):
    assert client.post("/plan/nonsense", data={"recipe_id": ""}).status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: FAIL — 404 on `/plan`.

- [ ] **Step 3: Append the routes to `app/main.py`**

Add `from datetime import date, timedelta` to the imports, then append:

```python
def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a date: {raw!r}")


@app.get("/plan", response_class=HTMLResponse)
def plan_week(request: Request, week: str = "", conn=Depends(get_db)):
    anchor = _parse_date(week) if week else date.today()
    start = db.week_start(anchor)
    return templates.TemplateResponse(
        request, "plan.html",
        {
            "week": db.get_plan(conn, start),
            "start": start,
            "prev": start - timedelta(days=7),
            "next": start + timedelta(days=7),
            "today": date.today(),
            "recipes": db.list_recipes(conn, sort="title"),
        },
    )


@app.post("/plan/{day}", response_class=HTMLResponse)
def assign_day(request: Request, day: str, recipe_id: str = Form(""), conn=Depends(get_db)):
    d = _parse_date(day)
    rid = int(recipe_id) if recipe_id.strip() else None
    if rid is not None and db.get_recipe(conn, rid) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    db.set_plan(conn, d, rid)
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/plan?week={d.isoformat()}", status_code=303)
    planned = dict(db.get_plan(conn, db.week_start(d)))[d]
    return templates.TemplateResponse(
        request, "_day.html",
        {"day": d, "recipe": planned, "today": date.today(),
         "recipes": db.list_recipes(conn, sort="title")},
    )
```

- [ ] **Step 4: Write the templates**

Create `app/templates/_day.html`:

```html
<div class="day {% if day == today %}today{% endif %}" id="day-{{ day.isoformat() }}">
  <span class="name">{{ day.strftime('%A') }}</span>
  <form method="post" action="/plan/{{ day.isoformat() }}"
        hx-post="/plan/{{ day.isoformat() }}"
        hx-target="#day-{{ day.isoformat() }}" hx-swap="outerHTML"
        hx-trigger="change from:find select">
    <select name="recipe_id">
      <option value="">— nothing planned —</option>
      {% for r in recipes %}
        <option value="{{ r['id'] }}" {% if recipe and r['id'] == recipe['id'] %}selected{% endif %}>{{ r['title'] }}</option>
      {% endfor %}
    </select>
    <noscript><button type="submit">Set</button></noscript>
  </form>
  {% if recipe %}<a href="/recipe/{{ recipe['id'] }}" class="stars">{{ stars(recipe['rating']) }}</a>{% endif %}
</div>
```

Create `app/templates/plan.html`:

```html
{% extends "base.html" %}
{% block title %}Week of {{ start.strftime('%b %-d') }}{% endblock %}
{% block content %}
<h1>Week of {{ start.strftime('%B %-d') }}</h1>
<p class="chips">
  <a class="chip" href="/plan?week={{ prev.isoformat() }}">← Previous</a>
  <a class="chip" href="/plan">This week</a>
  <a class="chip" href="/plan?week={{ next.isoformat() }}">Next →</a>
</p>
<div class="week">
  {% for day, recipe in week %}
    {% include "_day.html" %}
  {% endfor %}
</div>
{% endblock %}
```

Note the loop variables `day` and `recipe` are exactly the names `_day.html` expects, so the include needs no `with` clause.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/ tests/test_web.py
git commit -m "feat: weekly dinner plan page"
```

---

### Task 9: Docker packaging and README

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `README.md`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: the whole application.
- Produces: a running container serving on port 8000, with the SQLite file on a bind-mounted host volume.

- [ ] **Step 1: Write the `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/recipes.db

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `.dockerignore`**

```
.git
.venv
tests
docs
*.db
*.db-wal
*.db-shm
__pycache__
```

- [ ] **Step 3: Write `compose.yaml`**

```yaml
services:
  recipes:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
```

- [ ] **Step 4: Build and smoke-test the container**

```bash
docker compose build
docker compose up -d
sleep 3
curl -sf http://localhost:8000/ | head -5
docker compose down
```

Expected: HTML containing `Recipes`. If `docker` is unavailable on this machine, note that and skip to Step 5 — the tests already cover the application itself.

- [ ] **Step 5: Write `README.md`**

```markdown
# Recipe Dashboard

A household recipe library with categories, star ratings, and a weekly
dinner plan. Self-hosted, no accounts, no cloud.

## Run it

```bash
docker compose up -d          # http://localhost:8000
```

The database is a single SQLite file at `./data/recipes.db`. Back it up by
copying it.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --reload
```

## How it works

- `app/db.py` — every SQL statement in the project. Three tables: `recipes`,
  `recipe_tags`, `plan`.
- `app/importer.py` — pastes a URL in, reads the page's schema.org JSON-LD,
  gets a recipe out. Returns nothing rather than raising when a page has no
  recipe data; the UI falls back to the manual form.
- `app/main.py` — FastAPI routes. Each one renders a full page normally and a
  bare fragment when HTMX asks for it.

Design notes and the implementation plan live in `docs/superpowers/`.
```

- [ ] **Step 6: Run the full suite one last time**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore compose.yaml README.md
git commit -m "feat: Docker packaging and README"
```

---

## Definition of Done

- `.venv/bin/pytest` passes.
- `docker compose up` serves the app on port 8000 and the database survives a restart.
- You can add a recipe manually, import one from a URL, tag it, rate it, find it by search and by tag, and put it on a day of the week.
