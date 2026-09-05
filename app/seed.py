"""Load the starter library into the database.

Idempotent by title: recipes already in the library are left alone, so running
this twice is safe and re-running after adding entries to the seed file only
adds the new ones.

    python -m app.seed            # into $DB_PATH, default ./recipes.db
    python -m app.seed --dry-run  # say what it would do
"""

import json
import os
import sys
from pathlib import Path

from app import db, importer

SEED_FILE = Path(__file__).parent / "seeds" / "recipes.json"


def seed(conn, path: Path = SEED_FILE, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """Returns (added, skipped, rejected_titles)."""
    have = {r["title"].strip().lower() for r in db.list_recipes(conn)}
    added, skipped, rejected = 0, 0, []

    for entry in json.loads(path.read_text()):
        # Same normalizer the URL and paste imports use, so a seed entry can
        # carry full ingredients and steps whenever someone fills them in.
        found = importer.recipe_from_dict(entry)
        if found is None:
            rejected.append(str(entry.get("title", entry))[:60])
            continue
        if found.title.lower() in have:
            skipped += 1
            continue
        if not dry_run:
            db.create_recipe(
                conn, found.title,
                ingredients=found.ingredients, steps=found.steps,
                notes=found.notes, source_url=found.source_url or None,
                tags=found.tags,
            )
        have.add(found.title.lower())
        added += 1

    return added, skipped, rejected


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    db_path = os.environ.get("DB_PATH", "./recipes.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    try:
        added, skipped, rejected = seed(conn, dry_run=dry_run)
    finally:
        conn.close()

    verb = "would add" if dry_run else "added"
    print(f"{verb} {added}, skipped {skipped} already present -> {db_path}")
    for title in rejected:
        print(f"  rejected (no usable title): {title}", file=sys.stderr)
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
