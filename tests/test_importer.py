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


def test_name_as_list_of_strings():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": ["Weeknight Chili", "Easy Chili"],
     "recipeIngredient": ["beef"], "recipeInstructions": "Cook."}</script>"""
    r = importer.parse_recipe(html)
    assert r is not None
    assert r.title == "Weeknight Chili"


def test_name_as_non_string_non_list_returns_none():
    html = """<script type="application/ld+json">
    {"@type": "Recipe", "name": 42, "recipeIngredient": ["beef"],
     "recipeInstructions": "Cook."}</script>"""
    assert importer.parse_recipe(html) is None


def test_deeply_nested_json_returns_none():
    # Build deeply nested JSON using arrays to potentially trigger RecursionError in _walk
    # Start with deeply nested structure
    deeply_nested = '['
    for _ in range(2000):
        deeply_nested += '['
    deeply_nested += '{"@type": "Recipe", "name": "Test"}'
    for _ in range(2000):
        deeply_nested += ']'
    html = f'<script type="application/ld+json">{deeply_nested}</script>'
    # Should return None (caught by exception handler) not raise
    result = importer.parse_recipe(html)
    assert result is None


def test_malformed_json_followed_by_valid_recipe():
    html = '''<script type="application/ld+json">{not json</script>
    <script type="application/ld+json">
    {"@type": "Recipe", "name": "Found It", "recipeIngredient": ["x"],
     "recipeInstructions": "Go."}</script>'''
    r = importer.parse_recipe(html)
    assert r is not None
    assert r.title == "Found It"
