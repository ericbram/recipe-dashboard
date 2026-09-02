# Recipe Dashboard — Design

**Date:** 2026-09-01
**Status:** Approved for implementation planning

## Purpose

A household recipe dashboard: keep a searchable library of recipes, organize
them by category, rate them, and plan the week's dinners. Self-hosted on a
home server and used by everyone in the house from their phones.

## Scope

In scope:

- Recipe library with search, category filtering, and sorting
- Manual recipe entry and import from a pasted URL
- A 1–5 star rating per recipe, shared across the household
- A week-at-a-glance dinner plan, one recipe per day

Out of scope (and why):

- **Authentication.** The app runs on a trusted home network.
- **Shopping lists.** Not requested. Adding one later means promoting
  ingredients from free text to their own table; see Data Model.
- **Cook history / analytics.** Ratings alone answer "is this any good".
- **Breakfast and lunch slots.** Dinner is the meal that gets planned.
- **Nutrition data.** No source of truth for it, and no one asked.

## Architecture

A single FastAPI process serving server-rendered Jinja templates, backed by
one SQLite file. HTMX handles in-place updates, so there is no JavaScript
build step and no `node_modules`. The app ships as one Docker container.

This shape was chosen over a React SPA because every feature above is a form
submission or a filter — none of them need client-side state. It was chosen
over a Go binary because iteration speed matters more here than runtime
minimalism.

```
recipe-dashboard/
  app/
    main.py           FastAPI routes
    db.py             schema + queries (stdlib sqlite3, no ORM)
    importer.py       URL -> recipe via JSON-LD
    templates/        Jinja templates
    static/style.css
  tests/test_app.py
  Dockerfile
  compose.yaml
  pyproject.toml
  README.md
```

No ORM: the query surface is a dozen statements against three tables, which
SQLAlchemy would obscure rather than simplify.

## Data Model

```sql
CREATE TABLE recipes (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  source_url  TEXT,
  ingredients TEXT NOT NULL DEFAULT '',   -- newline-delimited
  steps       TEXT NOT NULL DEFAULT '',   -- newline-delimited
  rating      INTEGER,                    -- 1-5, NULL = unrated
  notes       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL               -- ISO 8601
);

CREATE TABLE recipe_tags (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  tag       TEXT NOT NULL,
  PRIMARY KEY (recipe_id, tag)
);

CREATE TABLE plan (
  date      TEXT PRIMARY KEY,             -- ISO date, e.g. 2026-09-01
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE
);
```

Three decisions worth stating:

**Ingredients and steps are text, not rows.** Nothing in scope queries across
ingredients. If a shopping list is added later, that is the moment to add an
`ingredients` table — not before.

**Categories live in `recipe_tags`, not a `categories` table.** A recipe is
"chicken" and "quick" at once, so a join is needed, but the tag names carry no
attributes of their own. The category navigation is
`SELECT DISTINCT tag FROM recipe_tags ORDER BY tag`.

**The plan is keyed by date.** One dinner per day falls out of the primary key
for free, and "next week" is date arithmetic rather than a week entity.

`PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL` are set on every
connection: the first makes the cascades above real, the second lets a phone
read the list while someone else saves an edit.

## Pages

| Route | Method | Behavior |
|---|---|---|
| `/` | GET | Recipe list. Query params: `q` (title search), `tag`, `sort` (`rating`, `title`, `newest`; default `newest`). |
| `/recipe/{id}` | GET | Recipe detail with inline edit form. |
| `/recipe/{id}` | POST | Save edits. |
| `/recipe/{id}/rate` | POST | Set rating; returns the star widget fragment. |
| `/recipe/{id}/delete` | POST | Delete, redirect to `/`. |
| `/recipe/new` | GET | Empty form, with a URL import box at the top. |
| `/recipe/new` | POST | Create, redirect to the new recipe. |
| `/import` | POST | Fetch and parse a URL; returns the form prefilled. |
| `/plan` | GET | Week grid. Query param `week` (ISO date of any day in the week; default today). |
| `/plan/{date}` | POST | Assign or clear that day's recipe; returns the day-cell fragment. |

Weeks run Monday to Sunday.

### Interaction model

HTMX swaps only the fragment that changed:

- Clicking a star posts to `/recipe/{id}/rate` and swaps the star widget.
- Changing the search box or a tag chip re-requests `/` and swaps the list.
- Assigning a recipe to a day posts to `/plan/{date}` and swaps that cell.

Every one of these also works as a plain form submission with JavaScript
disabled, because the same handlers render full pages when the request is not
an HTMX request.

## URL Import

`importer.py` fetches the page with `httpx`, extracts every
`<script type="application/ld+json">` block, and looks for an object whose
`@type` is `Recipe` (including inside an `@graph` array). It maps
`name`, `recipeIngredient`, `recipeInstructions`, and `recipeCategory` onto
the recipe fields.

Schema.org JSON-LD is emitted by essentially every major recipe site because
Google requires it for rich results, which is why this works without
site-specific code. The `recipe-scrapers` library is the upgrade path if a
site you care about turns out not to emit it.

Failure is expected and handled, not exceptional. On a network error, a
non-Recipe page, or malformed JSON, the import returns nothing and the user
gets the manual form with the URL preserved and a note explaining that the
page could not be parsed. Import never blocks recipe creation.

`recipeInstructions` arrives as a string, a list of strings, or a list of
`HowToStep` objects depending on the site; the parser normalizes all three to
a newline-delimited string.

## Error Handling

- **Import failures** degrade to the manual form, as above.
- **Missing recipe or plan date** returns 404 through a small error template.
- **Empty title on save** re-renders the form with the message inline; the
  title is the only required field.
- **A deleted recipe that was on the plan** is removed from the plan by the
  foreign key cascade.

## Testing

One `tests/test_app.py` using FastAPI's `TestClient` against a temporary
SQLite file, covering the paths where a bug would be silent:

1. Create a recipe, then find it by search and by tag filter.
2. Rate it; confirm the rating persists and that sorting by rating orders correctly.
3. Assign it to a day, read the week back, then reassign that day.
4. Delete it; confirm it disappears from the plan too.
5. Import a local fixture HTML file containing JSON-LD; confirm the fields map.
6. Import a fixture with no JSON-LD; confirm it fails soft rather than raising.

No mocking framework and no fixtures beyond a temp database path and two HTML
files.

## Deployment

`compose.yaml` runs the container with the SQLite file on a bind-mounted host
volume, so backups are `cp recipes.db recipes.db.bak`. The database path comes
from a `DB_PATH` environment variable, defaulting to `./recipes.db` for local
development. The schema is created on startup if the file does not exist.

## Repository

Git is configured locally to this directory only:

- `user.name` = `Eric Bram`
- `user.email` = `6936019+ericbram@users.noreply.github.com`
- `origin` = `git@github-personal:ericbram/recipe-dashboard.git`

The global work identity and the `gh` CLI's work account are untouched.
