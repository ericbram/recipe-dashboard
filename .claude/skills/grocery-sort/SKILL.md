---
name: grocery-sort
description: Sort the recipe dashboard's grocery list into store aisles (produce, meat, dairy…). Use when asked to sort/categorize/organize the grocery or shopping list, or to group it by aisle.
---

# Grocery sort

Put each ingredient on this week's grocery list into a store aisle, so the list
reads in the order you actually walk the store.

**The app remembers your answers.** Aisles are stored per ingredient, not per
week, so you only ever categorize what is new. A settled household list
converges on nothing to do — when `unsorted_keys` comes back empty, say so and
stop. Do not re-send keys that already have an aisle.

## Steps

Base URL is `${RECIPE_DASHBOARD_URL:-http://127.0.0.1:8000}`.

1. **Read the list.**

   ```bash
   curl -sS "http://127.0.0.1:8000/api/grocery"
   ```

   Add `?week=YYYY-MM-DD` (any day in the week) for a week other than this one.
   You get back `items` (each with `line`, `key`, `aisle`, `sources`), the valid
   `aisles`, and `unsorted_keys` — the only thing you have to work on.

2. **Assign an aisle to every key in `unsorted_keys`.** Judge from the `line`
   the key came from, not the key alone. Use only the aisle names the response
   lists; anything else is rejected.

3. **Send them back**, writing the JSON to a file in the scratchpad rather than
   inlining it:

   ```bash
   curl -sS -X POST "http://127.0.0.1:8000/api/aisles" \
     -H 'Content-Type: application/json' --data-binary @aisles.json
   ```

   Body is a flat `{"<key>": "<aisle>"}` object:

   ```json
   {"ground beef": "meat", "baby potatoes": "produce", "cottage cheese": "dairy"}
   ```

   The reply is `{"learned": N, "known": M}`. Then tell the user the list is
   sorted and link them to `http://127.0.0.1:8000/grocery`.

4. **If the app is not running** (connection refused), say so and stop. Do not
   start the server yourself.

## Judging calls

- **Go by where the store shelves it, not what it is.** Canned tuna is `pantry`,
  not `seafood`. Frozen peas are `frozen`, not `produce`. Butter and eggs are
  `dairy`. Bacon is `meat`. Dried herbs are `spices`; fresh herbs are `produce`.
- **A line that says "juice" means the bottle, not the fruit** — never `produce`.
  Cooking juices (lemon, lime) are `pantry`; drinking juices (orange, apple) are
  `drinks`. "1 tablespoon lemon juice" is a bottle off the shelf; only an actual
  "2 lemons" is produce.
- **A line naming several things** ("Rosemary, salt, pepper") goes to the aisle
  of its most awkward member — `spices` there. One key gets one aisle.
- **`pantry` is the fallback** for shelf-stable groceries, not a dumping ground.
  If something genuinely is not food (foil, dish soap), it is `household`.
- Optional garnishes still need an aisle — "Lettuce, optional" is `produce`.

## Fixing a wrong one

Re-POST the same key with a different aisle; it overwrites. That is also how the
user corrects you: "capers are pantry, not produce" is one POST, and it sticks
for every future week.
