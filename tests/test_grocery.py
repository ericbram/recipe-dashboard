from datetime import date, timedelta

from app import grocery


class FakeRecipe(dict):
    """db.get_plan returns sqlite3.Row; a dict indexes the same way."""


def plan(*pairs):
    monday = date(2026, 8, 31)
    return [(monday + timedelta(days=i), r) for i, r in enumerate(pairs)]


def recipe(title, ingredients):
    return FakeRecipe(title=title, ingredients=ingredients)


def test_collects_every_ingredient_from_the_planned_dinners():
    items = grocery.build_list(plan(
        recipe("Chili", "1 lb ground beef\n1 onion, diced"),
        recipe("Pasta", "1 lb spaghetti"),
    ))
    assert [i.line for i in items] == ["1 lb ground beef", "1 onion, diced", "1 lb spaghetti"]


def test_unplanned_days_are_skipped():
    assert grocery.build_list(plan(None, None)) == []


def test_identical_lines_collapse_and_name_both_recipes():
    items = grocery.build_list(plan(
        recipe("Chili", "1 onion, diced"),
        recipe("Soup", "1 onion, diced"),
    ))
    assert len(items) == 1
    assert items[0].sources == ["Chili", "Soup"]
    assert items[0].shared is True
    assert items[0].summary == "Chili, Soup"


def test_a_recipe_cooked_twice_in_a_week_counts_twice():
    """You have to shop for both nights, so this must not collapse to one."""
    items = grocery.build_list(plan(
        recipe("Chili", "1 lb ground beef"),
        recipe("Chili", "1 lb ground beef"),
    ))
    assert items[0].sources == ["Chili", "Chili"]
    assert items[0].summary == "Chili ×2"


def test_one_recipe_listing_a_thing_twice_still_buys_it_once():
    items = grocery.build_list(plan(recipe("Odd", "Salt\nsalt\n  SALT  ")))
    assert len(items) == 1
    assert items[0].sources == ["Odd"]


def test_blank_and_empty_ingredient_text_is_ignored():
    items = grocery.build_list(plan(
        recipe("Empty", ""),
        recipe("Gappy", "Flour\n\n   \nSugar"),
    ))
    assert [i.line for i in items] == ["Flour", "Sugar"]


def test_sort_key_strips_amounts_and_units():
    assert grocery.sort_key("2 lbs ground beef") == "ground beef"
    assert grocery.sort_key("1/4 cup finely chopped red onion") == "finely chopped red onion"
    assert grocery.sort_key("1 (5- or 6-ounce) can tuna in olive oil") == "tuna in olive oil"
    # A line that is nothing but an amount still sorts somewhere stable.
    assert grocery.sort_key("2 cups") == "2 cups"


def test_list_is_sorted_by_the_ingredient_not_the_amount():
    items = grocery.build_list(plan(
        recipe("A", "2 lbs zucchini\n1 cup almonds"),
    ))
    assert [i.line for i in items] == ["1 cup almonds", "2 lbs zucchini"]


def test_items_carry_a_stable_key_for_the_aisle_map():
    items = grocery.build_list(plan(recipe("A", "2 lbs ground beef")))
    assert items[0].key == "ground beef"
    assert items[0].aisle == grocery.UNSORTED


def test_known_aisles_are_applied_by_key():
    items = grocery.build_list(
        plan(recipe("A", "2 lbs ground beef\n1 cup almonds")),
        {"ground beef": "meat"},
    )
    by_line = {i.line: i.aisle for i in items}
    assert by_line["2 lbs ground beef"] == "meat"
    assert by_line["1 cup almonds"] == grocery.UNSORTED


def test_one_learned_key_covers_different_amounts_of_the_same_thing():
    """The whole point of keying on sort_key rather than the raw line."""
    items = grocery.build_list(
        plan(recipe("A", "1 lb ground beef"), recipe("B", "2 lbs ground beef")),
        {"ground beef": "meat"},
    )
    assert [i.aisle for i in items] == ["meat", "meat"]


def test_groups_follow_store_order_and_drop_empty_aisles():
    items = grocery.build_list(
        plan(recipe("A", "beef\nmilk\nlettuce\nmystery")),
        {"beef": "meat", "milk": "dairy", "lettuce": "produce"},
    )
    assert [name for name, _ in grocery.group_by_aisle(items)] == [
        "produce", "meat", "dairy", grocery.UNSORTED,
    ]


def test_an_unknown_aisle_name_falls_into_unsorted_rather_than_vanishing():
    items = grocery.build_list(plan(recipe("A", "beef")), {"beef": "butcher"})
    groups = grocery.group_by_aisle(items)
    assert groups == [(grocery.UNSORTED, items)]
