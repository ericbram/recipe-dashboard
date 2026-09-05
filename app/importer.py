"""Import a recipe from a URL by reading its schema.org JSON-LD.

Every major recipe site emits JSON-LD because Google requires it for rich
results, so this needs no site-specific code. If a site you care about turns
out not to emit it, the `recipe-scrapers` library is the upgrade path.
"""

import ipaddress
import json
import socket
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
    notes: str = ""


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


def _extract_title(name_value) -> str:
    """Extract a single string title from name, which may be string, list, or other type."""
    if isinstance(name_value, str):
        return name_value.strip()
    if isinstance(name_value, list):
        for item in name_value:
            if isinstance(item, str):
                title = item.strip()
                if title:
                    return title
    return ""


def parse_recipe(html: str, source_url: str = "") -> ImportedRecipe | None:
    collector = _JsonLdCollector()
    try:
        collector.feed(html)
    except Exception:
        return None

    for block in collector.blocks:
        try:
            doc = json.loads(block)
            for node in _walk(doc):
                if not _is_recipe(node):
                    continue
                title = _extract_title(node.get("name"))
                if not title:
                    continue
                return ImportedRecipe(
                    title=title,
                    ingredients=_lines(node.get("recipeIngredient")),
                    steps=_lines(node.get("recipeInstructions")),
                    tags=_tags(node.get("recipeCategory")),
                    source_url=source_url,
                )
        except Exception:
            continue
    return None


def recipe_from_dict(doc) -> ImportedRecipe | None:
    """Build a recipe from a decoded JSON object, or None if it is not one.

    Every field runs through the same normalizers the JSON-LD path uses, so a
    list and a newline-delimited string are accepted interchangeably.
    """
    if not isinstance(doc, dict):
        return None
    title = _extract_title(doc.get("title"))
    if not title:
        return None
    url = doc.get("source_url")
    return ImportedRecipe(
        title=title,
        ingredients=_lines(doc.get("ingredients")),
        steps=_lines(doc.get("steps")),
        tags=_tags(doc.get("tags")),
        source_url=url.strip() if isinstance(url, str) else "",
        notes=_lines(doc.get("notes")),
    )


def parse_pasted(text: str) -> ImportedRecipe | None:
    """Parse the JSON blob the `recipe-import` Claude skill produces."""
    blob = text.strip()
    if blob.startswith("```"):
        # Tolerate a markdown code fence copied along with the JSON.
        blob = blob.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return recipe_from_dict(json.loads(blob))
    except ValueError:
        return None


def _is_public(host: str | None) -> bool:
    """True only if every address `host` resolves to is a public one."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


def _block_private(request: httpx.Request) -> None:
    """Refuse to fetch anything that is not a public http(s) address.

    Runs on every request the client makes, redirects included, so a public
    URL cannot bounce us onto the LAN.
    """
    if request.url.scheme not in ("http", "https") or not _is_public(request.url.host):
        raise ValueError(f"refusing to fetch non-public address: {request.url}")


def fetch_recipe(url: str) -> ImportedRecipe | None:
    """Fetch and parse. Returns None on any failure — import never blocks entry."""
    # ponytail: resolve-then-connect, so a DNS rebind between the two could
    # still land on a private address. Closing that means a custom transport
    # that dials the address it checked; not worth it for a household app.
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [_block_private]},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return parse_recipe(resp.text, url)
    except Exception:
        return None
