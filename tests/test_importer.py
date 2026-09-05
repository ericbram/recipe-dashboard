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


def test_refuses_to_fetch_loopback():
    """The SSRF guard: no request should ever leave for a private address."""
    assert importer.fetch_recipe("http://127.0.0.1:1/anything") is None
    assert importer.fetch_recipe("http://localhost:1/anything") is None
    assert importer.fetch_recipe("http://192.168.1.1/router/admin") is None


def test_block_private_hook_rejects_private_and_allows_public():
    import httpx
    import pytest

    with pytest.raises(ValueError):
        importer._block_private(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/"))
    with pytest.raises(ValueError):
        importer._block_private(httpx.Request("GET", "file:///etc/passwd"))
    # A public literal passes the same hook, so the guard is not simply
    # rejecting everything.
    importer._block_private(httpx.Request("GET", "http://93.184.216.34/recipe"))


def test_parses_pasted_json():
    blob = """
    {"title": "Skillet Cornbread",
     "source_url": "https://example.com/cornbread",
     "ingredients": ["1 cup cornmeal", "1 cup buttermilk"],
     "steps": ["Heat the skillet.", "Bake 25 minutes."],
     "tags": ["Side", "baking"],
     "notes": "Serves 8."}
    """
    r = importer.parse_pasted(blob)
    assert r is not None
    assert r.title == "Skillet Cornbread"
    assert r.ingredients == "1 cup cornmeal\n1 cup buttermilk"
    assert r.steps == "Heat the skillet.\nBake 25 minutes."
    assert r.tags == ["baking", "side"]
    assert r.notes == "Serves 8."
    assert r.source_url == "https://example.com/cornbread"


def test_pasted_json_tolerates_a_copied_code_fence():
    fenced = '```json\n{"title": "Fenced Toast", "steps": "Toast it."}\n```'
    r = importer.parse_pasted(fenced)
    assert r is not None
    assert r.title == "Fenced Toast"
    assert r.steps == "Toast it."


def test_pasted_json_accepts_strings_where_lists_are_expected():
    r = importer.parse_pasted('{"title": "Toast", "ingredients": "Bread", "tags": "breakfast"}')
    assert r is not None
    assert r.ingredients == "Bread"
    assert r.tags == ["breakfast"]


def test_pasted_junk_returns_none():
    assert importer.parse_pasted("not json at all") is None
    assert importer.parse_pasted("") is None
    assert importer.parse_pasted("[1, 2, 3]") is None          # not an object
    assert importer.parse_pasted('{"steps": "no title"}') is None
    assert importer.parse_pasted('{"title": "   "}') is None
