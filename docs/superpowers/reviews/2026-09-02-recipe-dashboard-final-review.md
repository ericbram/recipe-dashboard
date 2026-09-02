# Final whole-branch review — recipe-dashboard

Branch `feat/initial-implementation`, 15 commits off `main`, reviewed as a whole
against `docs/superpowers/specs/2026-09-01-recipe-dashboard-design.md`.

Reviewed: full branch diff (excluding `app/static/htmx.min.js`), the spec, the
plan, the ledger, and the current working tree. Every finding below was
reproduced against the running app, not inferred from reading.

**Verdict: CHANGES REQUIRED — 2 blocking.**

---

## Critical

None. The two XSS holes closed in Task 7 stay closed, and I found no third one.

**Sink trace (complete).** Every place user- or import-originated data reaches a
dangerous context:

| Sink | Source | Status |
|---|---|---|
| `recipe.html:7` `<a href>` | `source_url` (importable) | scheme allowlist, fail-closed |
| `recipe.html:31` `onsubmit` JS string | — | title removed entirely; no server data in JS context |
| `_list.html:7`, `_day.html:15` `<a href>` | `r['id']` | DB integer |
| `_stars.html:3,8` `hx-post`, `hx-vals` | `r['id']`, `range()` literal | integers only |
| `_day.html:3-6` `action`, `hx-post`, `hx-target` | `day.isoformat()` | validated `date` |
| `index.html:17,19` chip `href` | `q`, `sort`, `tag` | `\| urlencode` then autoescape |
| `Location` (4 routes) | int path params, `d.isoformat()` | no injection surface |
| `error.html:5` `{{ detail }}` | user query params via `!r` | Jinja autoescape, text context |

No user or imported data reaches an `hx-*` attribute, an inline handler, or a
response header anywhere on the branch. Titles, tags, ingredients, steps and
notes all land in text or attribute-value contexts under autoescaping.

---

## Important

### 1. The no-JS rating path does not exist, and the spec promises it — `app/templates/_stars.html`, `app/main.py:154`

This is the cross-cutting inconsistency: the three route groups do not handle the
HTMX-fragment-vs-full-page split the same way, and rating is the one that is
actually broken.

- `/` checks `HX-Request` and returns `index.html` or `_list.html` (`main.py:67`).
- `/plan/{day}` checks `HX-Request` and returns a 303 or `_day.html` (`main.py:223`).
- `/recipe/{id}/rate` **never checks**. It always returns the bare `_stars.html`
  fragment.

And the widget has no non-JS affordance to begin with. Reproduced: the star
`<button>`s in `_stars.html` sit at `recipe.html:5`, outside any `<form>` —
confirmed there is no open `<form>` anywhere before them in the rendered page.
A `<button>` outside a form does nothing. With JavaScript off, **rating a recipe
is completely inoperable**, and if a client did POST there it would receive a
bare `<span>` as its whole document:

```
non-HX POST /recipe/1/rate -> 200, body starts '<span class="stars" id="stars-1" ...'
```

Spec line 124 lists "clicking a star" in the interaction model and lines 128-130
state: *"Every one of these also works as a plain form submission with JavaScript
disabled, because the same handlers render full pages when the request is not an
HTMX request."* Search and day-assignment honour that. Rating does not.

**Fix:** wrap the widget in `<form method="post" action="/recipe/{{ r['id'] }}/rate">`
and make each button `type="submit" name="rating" value="{{ n }}"` (the `hx-*`
attributes keep working and take precedence when htmx is live), then add to
`rate_recipe` the same branch its two siblings already have:

```python
if not request.headers.get("HX-Request"):
    return RedirectResponse(f"/recipe/{recipe_id}", status_code=303)
```

Add a test that POSTs `/recipe/{id}/rate` *without* `HX-Request` and asserts 303
— every existing rating test either sets the header or asserts status only, so
this whole path is currently uncovered as well as unimplemented.

### 2. No CSRF protection means any internet page can drive writes into the LAN app — `app/main.py` (all POST routes)

The spec knowingly accepted no authentication on the grounds that "the app runs
on a trusted home network." That reasoning holds for *who can reach the port*.
It does not hold for *who can cause a request to it*: every state-changing route
accepts `application/x-www-form-urlencoded`, which is a CORS simple request, so
any page a household member happens to be browsing can POST to the app from
their browser. The response is opaque to the attacker, but the write lands.

Reproduced:

```
POST /recipe/1/delete   Origin: https://evil.example  ->  303
GET  /recipe/1                                        ->  404   (recipe gone)
```

This is a different trust boundary than the one the spec reasoned about — it
takes the attacker from "on your LAN" to "on any website you visit". The
guessable default (`http://localhost:8000`) makes it cheap to attempt blind.

**Fix (~5 lines):** reject POSTs that carry an `Origin` header not matching the
request host. Same-origin form posts from the app's own pages send a matching
`Origin`; cross-site posts send the attacker's; the no-JS/curl case sends none.

```python
@app.middleware("http")
async def block_cross_origin_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlparse(origin).netloc != request.headers.get("host"):
        return PlainTextResponse("Cross-origin write rejected", status_code=403)
    return await call_next(request)
```

If instead you decide to accept this, it needs to be written into the spec's
out-of-scope section as a knowing acceptance — right now the spec's stated
justification does not cover it.

### 3. `/import` is a blind SSRF, and finding 2 is what makes it remotely triggerable — `app/importer.py:132`

`fetch_recipe` fetches any user-supplied URL server-side with
`follow_redirects=True` and no destination filtering. Reproduced against a local
listener:

```
POST /import  url=http://127.0.0.1:<port>/router/admin?reboot=1
  -> 200, and the internal server logged GET /router/admin?reboot=1
```

The recipe server will issue arbitrary GETs to loopback and LAN addresses,
including paths that are state-changing on other devices (routers, IoT admin
pages). Response content is not reflected unless it parses as JSON-LD, so this
is blind, not an exfiltration channel.

On its own this is low-consequence — the only person who can trigger it is a
household member who is already trusted with full write access. **What makes it
matter is finding 2**: with no CSRF protection, a hostile page can trigger
`/import` from a household member's browser and aim the server's HTTP client at
the LAN. Fixing finding 2 removes the remote trigger and demotes this to
accepted risk. That pairing is the reason this is worth listing — neither task
review could see it, because the two halves live in different files from
different tasks.

**Fix:** finding 2's middleware is the load-bearing part. Optionally also reject
resolved-private destinations in `fetch_recipe` before the request, or at
minimum note the behaviour in the README.

---

## Minor

### 4. `POST /recipe/{id}/delete` is the one write route with no existence check — `app/main.py:167`

```
POST /recipe/9999/delete -> 303 /
```

Spec's Error Handling section says a missing recipe returns 404, and the three
sibling routes (`recipe_detail`, `edit_recipe`, `rate_recipe`) all do exactly
that. `remove_recipe` is also the only write handler that does not take
`request`. Two lines to align it. Delete is idempotent and the redirect lands
somewhere sane, so this is cosmetic consistency rather than a defect — but it is
the kind of drift the lead asked about, and it is in the spec's error table.

### 5. Errors raised during an HTMX request are invisible — `app/main.py:71`

The `HTTPException` handler always renders `error.html`, which extends
`base.html`. Reproduced: an HTMX assign naming a missing recipe returns a full
`<!doctype html>` document with status 404. htmx does not swap non-2xx responses
by default, so the user sees the day cell simply not change, with no message.
Affects `/plan/{day}` (400 and 404) and `/rate` (400 and 404). Cheap improvement:
have the handler return a small fragment when `HX-Request` is set, and give the
plan page an `hx-target-error` or an `htmx:responseError` listener. Not blocking
— the failure is silent, not wrong.

### 6. `GET /recipe/abc` returns FastAPI's JSON 422 rather than `error.html`

The deferred Task 7 minor. I agree with the deferral, with one correction to the
ruling's stated reason: it is reachable, not unreachable — a person mistyping a
URL hits it. It is still cosmetic (a wrong-looking error page for a
hand-mangled URL), so leaving it is fine.

### 7. The container's dependencies are unpinned — `pyproject.toml:5-11`, `Dockerfile:8`

`pip install .` against five unpinned names means a rebuild in six months can
pull a FastAPI or httpx major and break an appliance whose owner changed nothing.
The plan froze the dependency *list*, not versions. A `>=x,<x+1` bound on each,
or a lockfile, would make rebuilds reproducible. Worth doing eventually; not a
merge blocker for a home LAN app.

---

## Data-layer integrity — clean

`app/db.py` was built across four tasks and reads as if it were written once.

- Every mutating function commits exactly once, at the end, after all of its
  statements. `create_recipe` and `update_recipe` each pair a row write with
  `_set_tags` inside one transaction and commit after both — there is no path
  that writes without committing, and no path that commits partial state.
- Every value is parameterized. The only f-string interpolation into SQL is
  `list_recipes`'s `WHERE`/`ORDER BY` assembly (`db.py:139-144`), and both parts
  are built from literals: the `WHERE` clauses are fixed strings with `?`
  placeholders, and the sort key goes through `_SORTS.get(sort, _SORTS['newest'])`,
  a whitelist lookup that discards anything unrecognised. Verified by
  `test_unknown_sort_falls_back_to_newest`.
- Tag normalisation is consistent across the write path (`_norm_tags`) and the
  read path (`list_recipes` lowercases the filter), so a round trip cannot miss.
- `set_rating` validates before it executes, so a rejected rating leaves no
  pending statement.

One note, not a finding: with a single long-lived connection and no
`try/rollback`, an exception between a statement and its `commit()` would leave
an open transaction that the next successful `commit()` would silently include.
No reachable path does this today (the only exceptions raised in these functions
happen before any statement executes), and the `ponytail:` comment at
`main.py:16-19` already names the shared-connection ceiling. Mentioning it so the
next person who adds a multi-statement write knows to wrap it.

## Spec conformance — clean in both directions

All ten routes in the spec's Pages table exist with the specified methods and
query parameters. Weeks run Monday to Sunday (`db.week_start`). The schema
matches the spec's DDL exactly, plus one index on `recipe_tags(tag)` that the
spec did not forbid. Both PRAGMAs are set on every connection.

Nothing declared out of scope was built: grepping `app/` for auth, session,
password, shopping, nutrition, calorie, breakfast, lunch and cook-history terms
returns nothing outside the vendored htmx file.

Two harmless deviations: tests live in three files rather than the spec's single
`tests/test_app.py` (the plan overrode this, and the split is better), and a
malformed plan date returns 400 rather than the spec's blanket "404 for missing
recipe or plan date" — 400 is the more accurate status for unparseable input.

## Test suite — good, with one hole

71 tests, and they are load-bearing rather than decorative. I mutation-tested the
one I suspected of being vacuous: making `db.delete_recipe` a no-op fails three
tests across both files, including `test_web.py::test_delete_recipe`. My
suspicion was wrong; that test does assert the row is gone.

The importer suite covers the failure modes that matter (malformed JSON,
list-valued `name`, non-string `name`, deep nesting, `@type` as a list, network
error), and the db suite covers the FK cascade into `plan`, all three sort
orders, and rating bounds.

The one real gap is finding 1's: no test exercises `/recipe/{id}/rate` without
`HX-Request`, which is precisely why the missing full-page fallback survived nine
task reviews. Task 8 got a regression test for exactly this shape after the
mutation test proved the hole; rating never did.

`test_title_cannot_break_out_of_delete_confirm_js` passes trivially now that the
title was removed from the confirm string entirely, but it is still a valid
regression guard — it fails the moment someone re-adds the interpolation. Keep it.

---

## Deferred minors — triage

All twelve ledger entries, and whether each must be fixed before merge.

| # | Deferred item | Call |
|---|---|---|
| 1 | T1: `test_init_schema_is_idempotent` asserts no-raise only | **Leave.** The schema is `CREATE TABLE IF NOT EXISTS`; a stronger assertion tests SQLite, not us. |
| 2 | T1: WAL pragma re-run per connection | **Leave.** WAL is persistent in the file; re-setting is a no-op that keeps `connect()` self-contained. Correct as written. |
| 3 | T2: `get_tags` `ORDER BY` redundant with `_norm_tags` sort | **Leave.** Deliberate decoupling — the read should not depend on the write's ordering. |
| 4 | T2: `update_recipe` `recipe_id` lacks `int` annotation | **Leave.** Cosmetic; no type checker configured. |
| 5 | T3: `list_recipes` does not escape LIKE wildcards in `q` | **Leave.** Parameterized, so not injection. Searching `50%` matches broadly — a household search-quality nit, and `%` in a recipe title is rare. |
| 6 | T5: no test for a `name` list containing zero strings | **Leave.** `_extract_title` returns `""` for that case by construction and the caller `continue`s; the adjacent int-`name` test covers the same branch. |
| 7 | T6: `*.egg-info/` untracked and not gitignored | **Already resolved** in `.gitignore` and `.dockerignore` during Task 9. |
| 8 | T6: `StarletteDeprecationWarning` from `fastapi/testclient` | **Leave.** Third-party; not ours to fix. |
| 9 | T6: shared sqlite3 connection from the threadpool | **Leave.** The `ponytail:` comment names the ceiling and the upgrade path, which is the right treatment for a single-household app. See the data-layer note above for the one caveat. |
| 10 | T7: non-integer path params yield JSON 422, not `error.html` | **Leave**, but see finding 6 — the ruling's premise ("unreachable through the app's own forms") is right about forms and wrong about typed URLs. The conclusion still stands. |
| 11 | T9: report says "~150 MB", actual 205 MB | **Leave.** A report-file inaccuracy, no code involved. |
| 12 | T9: Dockerfile layer-cache split skipped | **Leave.** Agree with both the reviewer and the ruling; the build is fast enough that the split is not worth the extra layer. |

**None of the twelve should block the merge.** I checked each against the current
tree rather than taking the ledger's word, and one (#7) has already been fixed in
passing. Two rulings I would phrase differently (#2 is not merely harmless but
actively correct; #10's stated reason is wrong even though its conclusion is
right), but neither changes the outcome.

The blocking work is entirely in findings 1 and 2, both of which are things no
single-task review could have seen: finding 1 is a divergence between three route
groups written by three different agents, and finding 2 is a property of the
whole app's request surface rather than of any one route.
