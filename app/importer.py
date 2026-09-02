"""Import a recipe from a URL by reading its schema.org JSON-LD.

Every major recipe site emits JSON-LD because Google requires it for rich
results, so this needs no site-specific code. If a site you care about turns
out not to emit it, the `recipe-scrapers` library is the upgrade path.
"""

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; recipe-dashboard/0.1)"
TIMEOUT = 10.0


@dataclass
class ImportedRecipe:
    title: str
    ingredients: str = ""
    steps: str = ""
    tags: list[str] = field(default_factory=list)
    source_url: str = ""


class _JsonLdCollector(HTMLParser):
    """Collects the body of every <script type="application/ld+json"> block."""

    def __init__(self):
        super().__init__()
        self.blocks: list[str] = []
        self._capturing = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._capturing = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._capturing = False

    def handle_data(self, data):
        if self._capturing:
            self.blocks.append(data)


def _walk(node):
    """Yield every dict nested anywhere inside a decoded JSON document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == "recipe" for x in types)


def _lines(value) -> str:
    """Normalize a string / list of strings / list of HowToStep into text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    out = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("name") or ""
        else:
            text = ""
        text = text.strip()
        if text:
            out.append(text)
    return "\n".join(out)


def _tags(value) -> list[str]:
    raw = [value] if isinstance(value, str) else (value or [])
    seen = {str(t).strip().lower() for t in raw if isinstance(t, (str, int))}
    return sorted(t for t in seen if t)


def parse_recipe(html: str, source_url: str = "") -> ImportedRecipe | None:
    collector = _JsonLdCollector()
    try:
        collector.feed(html)
    except Exception:
        return None

    for block in collector.blocks:
        try:
            doc = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(doc):
            if not _is_recipe(node):
                continue
            title = (node.get("name") or "").strip()
            if not title:
                continue
            return ImportedRecipe(
                title=title,
                ingredients=_lines(node.get("recipeIngredient")),
                steps=_lines(node.get("recipeInstructions")),
                tags=_tags(node.get("recipeCategory")),
                source_url=source_url,
            )
    return None


def fetch_recipe(url: str) -> ImportedRecipe | None:
    """Fetch and parse. Returns None on any failure — import never blocks entry."""
    try:
        resp = httpx.get(
            url,
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return parse_recipe(resp.text, url)
    except Exception:
        return None
