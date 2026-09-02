from pathlib import Path

from app import importer

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_jsonld_recipe():
    html = (FIXTURES / "jsonld_recipe.html").read_text()
    r = importer.parse_recipe(html, "https://example.com/chili")
    assert r is not None
    assert r.title == "Weeknight Chili"
    assert r.ingredients == "1 lb beef\n2 cans beans"
    assert r.steps == "Brown the beef.\nAdd beans and simmer."
    assert r.tags == ["dinner", "main course"]
    assert r.source_url == "https://example.com/chili"


def test_returns_none_when_no_recipe_present():
    assert importer.parse_recipe((FIXTURES / "no_recipe.html").read_text()) is None


def test_returns_none_on_garbage_html():
    assert importer.parse_recipe("<html><body>hello</body></html>") is None


def test_survives_malformed_json():
    html = '<script type="application/ld+json">{not json at all</script>'
    assert importer.parse_recipe(html) is None


def test_instructions_as_plain_string():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": "Toast", "recipeIngredient": ["bread"],
     "recipeInstructions": "Toast the bread."}</script>"""
    r = importer.parse_recipe(html)
    assert r.steps == "Toast the bread."


def test_instructions_as_list_of_strings():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": "Toast", "recipeIngredient": ["bread"],
     "recipeInstructions": ["Slice.", "Toast."]}</script>"""
    r = importer.parse_recipe(html)
    assert r.steps == "Slice.\nToast."


def test_type_may_be_a_list():
    html = """<script type="application/ld+json">
    {"@type": ["Recipe", "NewsArticle"], "name": "Toast",
     "recipeIngredient": ["bread"], "recipeInstructions": "Toast."}</script>"""
    assert importer.parse_recipe(html).title == "Toast"


def test_recipe_without_a_name_is_rejected():
    html = '<script type="application/ld+json">{"@type": "Recipe", "recipeIngredient": ["x"]}</script>'
    assert importer.parse_recipe(html) is None


def test_fetch_returns_none_on_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(importer.httpx, "get", boom)
    assert importer.fetch_recipe("https://example.com/whatever") is None
