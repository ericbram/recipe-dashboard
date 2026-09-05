---
name: recipe-import
description: Turn a recipe URL into JSON to paste into the recipe dashboard. Use when given a recipe URL, or asked to import/scrape/grab a recipe from a link — especially when the app's own URL import failed on that page.
---

# Recipe import

Read a recipe page and emit JSON the dashboard's paste box understands.

This is the fallback for pages the app's built-in `/import` cannot read — sites
that render ingredients in JavaScript, or bury them in prose instead of
schema.org JSON-LD. The app parses JSON-LD only; you can read anything.

## Steps

1. Fetch the page with `mcp__exa__web_fetch_exa`, passing the URL in `urls`.
   Set `maxCharacters` to 12000 — the 3000 default truncates long recipes
   mid-ingredient. Batch multiple URLs in one call if given several.
2. Pull out title, ingredients, steps, tags, and any yield/time/serving note.
3. Build the JSON below, write it to a file, and POST it to the app:

   ```bash
   curl -sS -X POST "${RECIPE_DASHBOARD_URL:-http://127.0.0.1:8000}/api/recipes" \
     -H 'Content-Type: application/json' --data-binary @recipe.json
   ```

   Write the JSON to a file rather than inlining it — quoting a recipe into a
   shell argument mangles apostrophes and fractions. Use the scratchpad
   directory. On success you get back `{"id": N, "title": "...", "url": "/recipe/N"}`;
   give the user the full link, `http://127.0.0.1:8000/recipe/N`.
4. **If the POST fails** (connection refused means the app is not running), do
   not retry or start the server yourself. Print the JSON in one `json` code
   block and tell the user to paste it into **Add recipe → "Paste recipe JSON"**.

## Output shape

```json
{
  "title": "Weeknight Chili",
  "source_url": "https://example.com/chili",
  "ingredients": ["1 lb ground beef", "2 cans kidney beans"],
  "steps": ["Brown the beef.", "Add beans and simmer 40 minutes."],
  "tags": ["dinner", "beef"],
  "notes": "Serves 4. About an hour."
}
```

`title` is the only required field. Strings and arrays are interchangeable
everywhere — an array becomes one item per line. Unknown keys are ignored.

## Rules

- **Transcribe, do not invent.** If the page does not give quantities, times, or
  a yield, leave those out. A missing field beats a plausible guess — someone is
  going to cook this. If the fetch fails or the page is not a recipe, say so and
  emit nothing.
- `source_url` is the URL you were given, not an Exa redirect.
- One ingredient or step per array item, in page order. Strip step numbering
  ("1. ", "Step 3 — ") — the app renders its own list markers.
- `tags` are lowercase and short: meal, main ingredient, or method
  (`dinner`, `chicken`, `slow-cooker`). Skip a tag rather than reach for one.
- `notes` is where yield, total time, and the author's asides go. The dashboard
  has no fields for those, and they are worth keeping.

## Where it goes

`POST /api/recipes` writes the recipe immediately — there is no review step, so
get it right rather than expecting the user to fix it after. Always hand back
the link so they can check and edit it.

Duplicates are not detected: importing the same URL twice makes two recipes.
