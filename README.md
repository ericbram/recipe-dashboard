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
  recipe data; the UI falls back to the manual form. Note that importing a
  URL makes the server itself fetch that URL, so it can reach addresses on
  your home network that your browser can reach too — only import URLs you
  trust.
- `app/main.py` — FastAPI routes. Each one renders a full page normally and a
  bare fragment when HTMX asks for it.

Design notes and the implementation plan live in `docs/superpowers/`.
