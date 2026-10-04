"""One-time copy of a legacy SQLite recipes.db into Postgres.

    PGHOST=... PGDATABASE=recipes python -m app.migrate_sqlite data/recipes.db

Keeps ids, so tags and plan rows still point at the right recipes, and is safe
to re-run: rows already present are skipped. Run it from the Mac against the
Pi's Postgres through `ssh pi -L 5432:localhost:5432`.
"""

import sqlite3
import sys

from app import db

# Parents before children, so foreign keys hold.
TABLES = [
    ("recipes", ["id", "title", "source_url", "ingredients", "steps", "rating", "notes", "created_at"]),
    ("recipe_tags", ["recipe_id", "tag"]),
    ("plan", ["week", "recipe_id"]),
    ("staples", ["id", "line"]),
    ("pantry", ["week", "item_key"]),
    ("aisles", ["key", "aisle"]),
]


def migrate(src: sqlite3.Connection, dst) -> dict[str, int]:
    """Returns rows copied per table."""
    db.init_schema(dst)
    counts = {}
    with dst.transaction():
        for table, cols in TABLES:
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            marks = ", ".join(["%s"] * len(cols))
            dst.cursor().executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) OVERRIDING SYSTEM VALUE "
                f"VALUES ({marks}) ON CONFLICT DO NOTHING"
                if "id" in cols else
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks}) ON CONFLICT DO NOTHING",
                [tuple(r) for r in rows],
            )
            counts[table] = len(rows)
        # Explicit ids bypass the identity counter; move it past the copied rows
        # or the next new recipe collides with id 1.
        for table in ("recipes", "staples"):
            dst.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1))"
            )
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    src = sqlite3.connect(f"file:{argv[0]}?mode=ro", uri=True)
    with db.connect() as dst:
        counts = migrate(src, dst)
    print(", ".join(f"{t}: {n}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
