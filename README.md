# Recipe Dashboard

A household recipe library with categories, star ratings, and a weekly
dinner plan. Self-hosted, no accounts, no cloud.

## Run it

```bash
docker compose up -d --wait   # http://localhost:8000, throwaway Postgres
```

Data lives in Postgres (`PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`).
Production runs in the pi-stack repo against its shared Postgres.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
docker compose up -d --wait postgres
export PGHOST=localhost PGPORT=5433 PGUSER=postgres PGPASSWORD=dev PGDATABASE=recipes
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --reload
```

## Moving an old SQLite library

```bash
python -m app.migrate_sqlite path/to/recipes.db   # PG* env points at the target
```

Keeps ids and is safe to re-run.

## How it works

- `app/db.py` — every SQL statement in the project.
- `app/importer.py` — pastes a URL in, reads the page's schema.org JSON-LD,
  gets a recipe out. Returns nothing rather than raising when a page has no
  recipe data; the UI falls back to the manual form. Note that importing a
  URL makes the server itself fetch that URL, so it can reach addresses on
  your home network that your browser can reach too — only import URLs you
  trust.
- `app/main.py` — FastAPI routes. Each one renders a full page normally and a
  bare fragment when HTMX asks for it.

Design notes and the implementation plan live in `docs/superpowers/`.
