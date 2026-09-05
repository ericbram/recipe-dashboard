from app import grocery


class FakeRecipe(dict):
    """db.get_plan returns sqlite3.Row; a dict indexes the same way."""


def plan(*recipes):
    """db.get_plan() now returns plain recipe rows — no days, no gaps."""
    return [r for r in recipes if r is not None]


def recipe(title, ingredients):
    return FakeRecipe(title=title, ingredients=ingredients)


def test_collects_every_ingredient_from_the_planned_dinners():
    items = grocery.build_list(plan(
        recipe("Chili", "1 lb ground beef\n1 onion, diced"),
        recipe("Pasta", "1 lb spaghetti"),
    ))
    assert [i.line for i in items] == ["1 lb ground beef", "1 onion, diced", "1 lb spaghetti"]


def test_a_week_with_nothing_picked_is_empty():
    assert grocery.build_list(plan()) == []


def test_identical_lines_collapse_and_name_both_recipes():
    items = grocery.build_list(plan(
        recipe("Chili", "1 onion, diced"),
        recipe("Soup", "1 onion, diced"),
    ))
    assert len(items) == 1
    assert items[0].sources == ["Chili", "Soup"]
    assert items[0].shared is True
    assert items[0].summary == "Chili, Soup"


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


def staple(line):
    return {"id": hash(line) % 10000, "line": line}


def test_staples_join_the_proposed_list():
    items = grocery.build_list(plan(recipe("Chili", "beef")), staples=[staple("Milk")])
    assert [i.line for i in items] == ["beef", "Milk"]
    milk = next(i for i in items if i.line == "Milk")
    assert milk.sources == [grocery.STAPLE]
    assert milk.is_staple is True


def test_a_staple_that_is_also_an_ingredient_is_one_row_with_both_sources():
    items = grocery.build_list(plan(recipe("Cake", "2 eggs")), staples=[staple("2 eggs")])
    assert len(items) == 1
    assert items[0].sources == ["Cake", grocery.STAPLE]
    assert items[0].is_staple is True


def test_have_marks_come_from_the_pantry_set():
    items = grocery.build_list(
        plan(recipe("Chili", "1 lb beef\n1 onion")),
        have={"1 lb beef"},
    )
    by_line = {i.line: i.have for i in items}
    assert by_line["1 lb beef"] is True
    assert by_line["1 onion"] is False


def test_to_buy_drops_what_the_kitchen_already_has():
    items = grocery.build_list(
        plan(recipe("Chili", "1 lb beef\n1 onion")),
        have={"1 lb beef"},
    )
    assert [i.line for i in grocery.to_buy(items)] == ["1 onion"]


def test_check_key_is_case_and_whitespace_insensitive():
    assert grocery.check_key("  1 LB   Beef ") == "1 lb beef"
    # Two amounts of the same thing are separate rows to tick off...
    assert grocery.check_key("1 lb beef") != grocery.check_key("2 lb beef")
    # ...but they still share one aisle.
    assert grocery.sort_key("1 lb beef") == grocery.sort_key("2 lb beef")


def test_a_have_mark_follows_the_line_not_the_recipe():
    """Two recipes needing the same line share one tick."""
    items = grocery.build_list(
        plan(recipe("Chili", "1 onion, diced"), recipe("Soup", "1 onion, diced")),
        have={"1 onion, diced"},
    )
    assert len(items) == 1
    assert items[0].have is True
    assert grocery.to_buy(items) == []


def test_accents_fold_so_an_aisle_is_learned_once():
    assert grocery.sort_key("2 jalapeños, sliced") == "jalapenos sliced"
    assert grocery.sort_key("1 cup crème fraîche") == grocery.sort_key("1 cup creme fraiche")
    assert grocery.sort_key("2 jalapeños") == grocery.sort_key("2 jalapenos")


def test_folding_does_not_swallow_the_whole_line():
    """A line that is only non-ASCII must still get a usable key."""
    assert grocery.sort_key("½ cup sugar") == "sugar"
    assert grocery.sort_key("米") == "米"     # falls back to the line itself
