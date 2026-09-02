import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

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
