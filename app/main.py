import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, importer

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
    }
    # Full page normally; the bare list fragment when HTMX asks for it.
    name = "_list.html" if request.headers.get("HX-Request") else "index.html"
    return templates.TemplateResponse(request, name, ctx)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
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
                        source_url=found.source_url)
    return templates.TemplateResponse(request, "form.html", ctx)


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
    if recipe_id.strip():
        try:
            rid = int(recipe_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Not a recipe id: {recipe_id!r}")
    else:
        rid = None
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
