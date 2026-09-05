"""Turn a week of planned dinners into one shopping list.

Ingredients are free text by design (see the Data Model note in the design
doc), so this consolidates only what it can *prove* is the same — identical
lines — and leaves everything else as its own entry. It deliberately does not
try to add quantities together: "1 onion, diced" and "1/4 cup finely chopped
red onion" are not reliably the same thing, and a list that quietly guesses
wrong is worse at the store than one that shows you both lines.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

# Leading amounts and their units say nothing about *what* the ingredient is,
# so they are stripped before sorting — that puts "2 lb beef" under B.
_UNITS = {
    "cup", "cups", "tablespoon", "tablespoons", "tbsp", "teaspoon", "teaspoons",
    "tsp", "pound", "pounds", "lb", "lbs", "ounce", "ounces", "oz", "gram",
    "grams", "kg", "ml", "liter", "liters", "quart", "quarts", "pint", "pints",
    "can", "cans", "jar", "jars", "package", "packages", "clove", "cloves",
    "stalk", "stalks", "bunch", "bunches", "sprig", "sprigs", "slice", "slices",
    "pinch", "dash", "handful", "large", "medium", "small",
}
_NOISE = {"a", "an", "of", "or", "to", "and"}

#: Aisles in the order you walk a store, not alphabetical — the list is only
#: useful if it matches the trip. Edit this to match your actual store.
AISLES = (
    "produce", "meat", "seafood", "dairy", "bakery",
    "frozen", "pantry", "spices", "drinks", "household",
)
UNSORTED = "unsorted"


@dataclass
class Item:
    line: str
    #: One entry per planned dinner that needs this line, so a recipe cooked
    #: twice in a week counts twice — you have to buy for both.
    sources: list[str] = field(default_factory=list)
    #: sort_key(line) — the identity the learned aisle map is keyed by.
    key: str = ""
    aisle: str = UNSORTED

    @property
    def summary(self) -> str:
        counts = Counter(self.sources)
        return ", ".join(t if n == 1 else f"{t} ×{n}" for t, n in counts.items())

    @property
    def shared(self) -> bool:
        """True when more than one dinner needs this — worth seeing at a glance."""
        return len(self.sources) > 1


def sort_key(line: str) -> str:
    """A rough 'what is this ingredient' key, for putting like next to like.

    ponytail: word-prefix stripping, not real parsing. It gets quantities and
    units off the front, which is most of the value; it will not group
    "red onion" with "onion". Swap in an ingredient parser if that stops being
    good enough.
    """
    words = re.sub(r"[^a-z\s]", " ", line.lower()).split()
    while words and (words[0] in _UNITS or words[0] in _NOISE):
        words.pop(0)
    return " ".join(words) or line.lower().strip()


def build_list(planned, known_aisles: dict[str, str] | None = None) -> list[Item]:
    """planned is db.get_plan()'s [(date, recipe_row_or_None), ...].

    known_aisles is db.get_aisles() — anything not in it stays UNSORTED until
    the grocery-sort skill fills it in.
    """
    known_aisles = known_aisles or {}
    items: dict[str, Item] = {}
    for _day, recipe in planned:
        if recipe is None:
            continue
        seen_today: set[str] = set()
        for raw in (recipe["ingredients"] or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            dupe_key = " ".join(line.lower().split())
            if dupe_key in seen_today:
                continue  # one recipe listing a thing twice still buys it once
            seen_today.add(dupe_key)
            if dupe_key not in items:
                key = sort_key(line)
                items[dupe_key] = Item(
                    line=line, key=key,
                    aisle=known_aisles.get(key, UNSORTED),
                )
            items[dupe_key].sources.append(recipe["title"])
    return sorted(items.values(), key=lambda i: (i.key, i.line.lower()))


def group_by_aisle(items) -> list[tuple[str, list[Item]]]:
    """Items bucketed into AISLES order, empty aisles dropped, unsorted last."""
    buckets: dict[str, list[Item]] = {}
    for item in items:
        buckets.setdefault(item.aisle if item.aisle in AISLES else UNSORTED, []).append(item)
    order = [*AISLES, UNSORTED]
    return [(name, buckets[name]) for name in order if buckets.get(name)]
