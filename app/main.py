import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, grocery, importer

BASE_DIR = Path(__file__).parent
DB_PATH = os.environ.get("DB_PATH", "./recipes.db")

# ponytail: one shared connection for the whole process. sqlite3 is built in
# serialized mode so concurrent threadpool requests are safe at the C level, and
# this app serves one household. Switch to a per-request connection (or a write
# lock) if it ever needs genuinely concurrent writers.
_conn = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _conn
    _conn = db.connect(DB_PATH)
    db.init_schema(_conn)
    yield
    _conn.close()


app = FastAPI(title="Recipe Dashboard", lifespan=lifespan)


@app.middleware("http")
async def block_cross_origin_writes(request: Request, call_next):
    # ponytail: Origin-check CSRF guard. No auth on this app (trusted LAN), but
    # a mismatched Origin still means some other site's page is the one
    # POSTing here, not us — reject that. A missing Origin (curl, no-JS
    # clients) is allowed through; only same-origin or absent Origins pass.
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlparse(origin).netloc != request.headers.get("host"):
        return PlainTextResponse("Cross-origin write rejected", status_code=403)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_db():
    return _conn


def stars(rating) -> str:
    if not rating:
        return "—"
    return "★" * int(rating) + "☆" * (5 - int(rating))


def star_label(rating) -> str:
    if not rating:
        return "Not rated"
    return f"{int(rating)} out of 5"


templates.env.globals["stars"] = stars
templates.env.globals["star_label"] = star_label


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: str = "", sort: str = "newest", conn=Depends(get_db)):
    ctx = {
        "recipes": db.list_recipes(conn, q=q or None, tag=tag or None, sort=sort),
        "tags": db.all_tags(conn),
        "q": q,
        "tag": tag,
        "sort": sort,
        "on_week": _this_week_ids(conn),
    }
    # Full page normally; the bare list fragment when HTMX asks for it.
    name = "_list.html" if request.headers.get("HX-Request") else "index.html"
    return templates.TemplateResponse(request, name, ctx)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    # htmx will not swap a non-2xx response, so a full error page would just be
    # discarded and the user would see nothing happen. Send the bare message
    # instead; base.html shows it in the flash banner.
    if request.headers.get("HX-Request"):
        return PlainTextResponse(exc.detail, status_code=exc.status_code)
    return templates.TemplateResponse(
        request, "error.html",
        {"status": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _form_ctx(**over):
    ctx = {"title": "", "ingredients": "", "steps": "", "notes": "",
           "source_url": "", "tags": "", "recipe_id": None, "error": None}
    ctx.update(over)
    return ctx


@app.get("/recipe/new", response_class=HTMLResponse)
def new_recipe_form(request: Request):
    return templates.TemplateResponse(request, "form.html", _form_ctx())


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
        ctx = _form_ctx(title=title, ingredients=ingredients, steps=steps,
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
        {"r": row, "tags": db.get_tags(conn, recipe_id),
         "on_week": _this_week_ids(conn)},
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
        ctx = _form_ctx(title=title, ingredients=ingredients, steps=steps,
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
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/recipe/{recipe_id}", status_code=303)
    return templates.TemplateResponse(
        request, "_stars.html", {"r": db.get_recipe(conn, recipe_id)},
    )


@app.post("/recipe/{recipe_id}/delete")
def remove_recipe(recipe_id: int, conn=Depends(get_db)):
    if db.get_recipe(conn, recipe_id) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    db.delete_recipe(conn, recipe_id)
    return RedirectResponse("/", status_code=303)


@app.post("/import", response_class=HTMLResponse)
def import_recipe(request: Request, url: str = Form("")):
    found = importer.fetch_recipe(url) if url.strip() else None
    if found is None:
        ctx = _form_ctx(source_url=url,
                        error="Could not read a recipe from that page. Fill it in below.")
    else:
        ctx = _form_ctx(title=found.title, ingredients=found.ingredients,
                        steps=found.steps, tags=", ".join(found.tags),
                        source_url=found.source_url, notes=found.notes)
    return templates.TemplateResponse(request, "form.html", ctx)


@app.post("/import/paste", response_class=HTMLResponse)
def import_pasted(request: Request, recipe: str = Form("")):
    """Fill the form from a JSON blob — what the `recipe-import` skill emits."""
    found = importer.parse_pasted(recipe) if recipe.strip() else None
    if found is None:
        ctx = _form_ctx(error="That did not parse as recipe JSON. Fill it in below.")
    else:
        ctx = _form_ctx(title=found.title, ingredients=found.ingredients,
                        steps=found.steps, tags=", ".join(found.tags),
                        source_url=found.source_url, notes=found.notes)
    return templates.TemplateResponse(request, "form.html", ctx)


@app.post("/api/recipes", status_code=201)
async def create_recipe_api(request: Request, conn=Depends(get_db)):
    """Create a recipe from a JSON body — what the `recipe-import` skill posts.

    Unlike the form routes this takes application/json, which browsers preflight
    rather than send blind, so it is not reachable cross-site the way a form
    post is.
    """
    try:
        doc = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    found = importer.recipe_from_dict(doc)
    if found is None:
        raise HTTPException(status_code=400, detail="Needs at least a non-empty title")
    rid = db.create_recipe(conn, found.title, ingredients=found.ingredients,
                           steps=found.steps, notes=found.notes,
                           source_url=found.source_url or None, tags=found.tags)
    return {"id": rid, "title": found.title, "url": f"/recipe/{rid}"}


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a date: {raw!r}")


def _this_week_ids(conn) -> set[int]:
    """Recipe ids on the current week — for the browse pages' toggle button."""
    return {r["id"] for r in db.get_plan(conn, date.today())}


def _plan_ctx(conn, start: date) -> dict:
    chosen = db.get_plan(conn, start)
    picked = {r["id"] for r in chosen}
    return {
        "chosen": chosen,
        "start": start,
        "prev": start - timedelta(days=7),
        "next": start + timedelta(days=7),
        # Only offer recipes not already on the list, so adding twice is impossible.
        "recipes": [r for r in db.list_recipes(conn, sort="title") if r["id"] not in picked],
    }


@app.get("/plan", response_class=HTMLResponse)
def plan_week(request: Request, week: str = "", conn=Depends(get_db)):
    start = db.week_start(_parse_date(week) if week else date.today())
    return templates.TemplateResponse(request, "plan.html", _plan_ctx(conn, start))


def _week_items(conn, week: str, staples=None):
    """The whole proposed list for a week: recipe ingredients + staples, with
    each item flagged by what the kitchen check said.

    `staples` is injectable only so a caller that already fetched them (the
    kitchen page renders the list too) does not query twice.
    """
    start = db.week_start(_parse_date(week) if week else date.today())
    planned = db.get_plan(conn, start)
    items = grocery.build_list(
        planned,
        known_aisles=db.get_aisles(conn),
        staples=db.get_staples(conn) if staples is None else staples,
        have=db.get_pantry(conn, start),
    )
    return start, planned, items


def _kitchen_ctx(conn, start: date, staples_open: bool = False) -> dict:
    staples = db.get_staples(conn)
    _, planned, items = _week_items(conn, start.isoformat(), staples=staples)
    return {
        "groups": grocery.group_by_aisle(items),
        "items": items,
        "recipes": planned,
        "staples": staples,
        "start": start,
        "prev": start - timedelta(days=7),
        "next": start + timedelta(days=7),
        "have_count": sum(1 for i in items if i.have),
        "buy_count": len(grocery.to_buy(items)),
        # Keep the panel open across a swap when the swap came from using it.
        "staples_open": staples_open,
    }


@app.get("/kitchen", response_class=HTMLResponse)
def kitchen_check(request: Request, week: str = "", conn=Depends(get_db)):
    """The step between planning and shopping: tick off what you already have."""
    start = db.week_start(_parse_date(week) if week else date.today())
    return templates.TemplateResponse(request, "kitchen.html", _kitchen_ctx(conn, start))


@app.post("/kitchen/have", response_class=HTMLResponse)
def kitchen_toggle(request: Request, week: str = Form(""), item_key: str = Form(""),
                   have: str = Form(""), conn=Depends(get_db)):
    start = db.week_start(_parse_date(week) if week else date.today())
    key = grocery.check_key(item_key)
    if not key:
        raise HTTPException(status_code=400, detail="Which item?")
    db.set_pantry(conn, start, key, have=have.lower() in ("1", "true", "on", "yes"))
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/kitchen?week={start.isoformat()}", status_code=303)
    return templates.TemplateResponse(request, "_kitchen.html", _kitchen_ctx(conn, start))


@app.post("/staples/add", response_class=HTMLResponse)
def staple_add(request: Request, week: str = Form(""), line: str = Form(""),
               conn=Depends(get_db)):
    start = db.week_start(_parse_date(week) if week else date.today())
    try:
        db.add_staple(conn, line)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/kitchen?week={start.isoformat()}", status_code=303)
    return templates.TemplateResponse(
        request, "_kitchen.html", _kitchen_ctx(conn, start, staples_open=True))


@app.post("/staples/remove", response_class=HTMLResponse)
def staple_remove(request: Request, week: str = Form(""), staple_id: str = Form(""),
                  conn=Depends(get_db)):
    start = db.week_start(_parse_date(week) if week else date.today())
    try:
        db.remove_staple(conn, int(staple_id))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a staple id: {staple_id!r}")
    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/kitchen?week={start.isoformat()}", status_code=303)
    return templates.TemplateResponse(
        request, "_kitchen.html", _kitchen_ctx(conn, start, staples_open=True))


@app.get("/grocery", response_class=HTMLResponse)
def grocery_week(request: Request, week: str = "", conn=Depends(get_db)):
    start, planned, items = _week_items(conn, week)
    buying = grocery.to_buy(items)
    return templates.TemplateResponse(
        request, "grocery.html",
        {
            "groups": grocery.group_by_aisle(buying),
            "items": buying,
            "dinners": planned,
            "start": start,
            "end": start + timedelta(days=6),
            "unsorted": sum(1 for i in buying if i.aisle == grocery.UNSORTED),
            # Shown so the list never looks mysteriously short.
            "have_count": len(items) - len(buying),
        },
    )


@app.get("/api/grocery")
def grocery_api(week: str = "", conn=Depends(get_db)):
    """The week's list as JSON, for the `grocery-sort` skill.

    `key` is the stable identity to send back to /api/aisles; `aisle` is
    "unsorted" for anything not yet learned. `have` is what the kitchen check
    ticked off — those are excluded from the shopping list but still listed
    here, so an aisle gets learned once even for something usually in stock.
    """
    start, _planned, items = _week_items(conn, week)
    return {
        "week_start": start.isoformat(),
        "aisles": list(grocery.AISLES),
        "items": [
            {"line": i.line, "key": i.key, "aisle": i.aisle,
             "sources": i.sources, "have": i.have}
            for i in items
        ],
        "unsorted_keys": sorted({i.key for i in items if i.aisle == grocery.UNSORTED}),
    }


@app.post("/api/aisles")
async def set_aisles_api(request: Request, conn=Depends(get_db)):
    """Learn which aisle ingredients live in: {"ground beef": "meat", ...}.

    Keyed by the `key` field from /api/grocery, so the answer is reused for
    every future week and the agent only ever works on what is new.
    """
    try:
        doc = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(doc, dict) or not doc:
        raise HTTPException(status_code=400, detail='Body must be a non-empty {"key": "aisle"} object')

    mapping, bad = {}, []
    for key, aisle in doc.items():
        name = aisle.strip().lower() if isinstance(aisle, str) else ""
        if name not in grocery.AISLES:
            bad.append(f"{key!r}: {aisle!r}")
        elif key.strip():
            mapping[key.strip()] = name
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Not valid aisles ({', '.join(sorted(grocery.AISLES))}): {'; '.join(bad)}",
        )

    return {"learned": db.set_aisles(conn, mapping), "known": len(db.get_aisles(conn))}


def _plan_change(request: Request, conn, week: str, recipe_id: str, remove: bool):
    """Shared by add and remove — both validate the same way and answer the
    same two ways (fragment for htmx, redirect for a plain form post)."""
    start = db.week_start(_parse_date(week) if week else date.today())
    try:
        rid = int(recipe_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a recipe id: {recipe_id!r}")
    if db.get_recipe(conn, rid) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    (db.remove_from_plan if remove else db.add_to_plan)(conn, start, rid)

    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/plan?week={start.isoformat()}", status_code=303)
    return templates.TemplateResponse(request, "_plan.html", _plan_ctx(conn, start))


@app.post("/plan/add", response_class=HTMLResponse)
def plan_add(request: Request, week: str = Form(""), recipe_id: str = Form(""),
             conn=Depends(get_db)):
    return _plan_change(request, conn, week, recipe_id, remove=False)


@app.post("/plan/remove", response_class=HTMLResponse)
def plan_remove(request: Request, week: str = Form(""), recipe_id: str = Form(""),
                conn=Depends(get_db)):
    return _plan_change(request, conn, week, recipe_id, remove=True)


@app.post("/plan/toggle", response_class=HTMLResponse)
def plan_toggle(request: Request, recipe_id: str = Form(""), on: str = Form("1"),
                conn=Depends(get_db)):
    """Add or drop a recipe from the current week, from wherever you are
    browsing. Always the current week — a button on the recipe list has no
    other week in view, and picking one is what /plan is for."""
    try:
        rid = int(recipe_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Not a recipe id: {recipe_id!r}")
    if db.get_recipe(conn, rid) is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    want_on = on.lower() in ("1", "true", "on", "yes")
    today = date.today()
    (db.add_to_plan if want_on else db.remove_from_plan)(conn, today, rid)

    if not request.headers.get("HX-Request"):
        return RedirectResponse(f"/recipe/{rid}", status_code=303)
    return templates.TemplateResponse(
        request, "_weekbtn.html", {"rid": rid, "on_week": want_on})
