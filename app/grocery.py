"""Turn a week of planned dinners into one shopping list.

Ingredients are free text by design (see the Data Model note in the design
doc), so this consolidates only what it can *prove* is the same — identical
lines — and leaves everything else as its own entry. It deliberately does not
try to add quantities together: "1 onion, diced" and "1/4 cup finely chopped
red onion" are not reliably the same thing, and a list that quietly guesses
wrong is worse at the store than one that shows you both lines.
"""

import re
import unicodedata
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


STAPLE = "Staple"


def check_key(line: str) -> str:
    """The identity of one row on the kitchen check — the line, normalized.

    Distinct from sort_key: two amounts of the same thing are two rows to tick
    off ("1 lb beef" and "2 lbs beef" are separate buys) but share one aisle.
    """
    return " ".join(line.lower().split())


@dataclass
class Item:
    line: str
    #: Titles of the shortlisted recipes that need this line, plus "Staple" if
    #: it is on the always-check list. A recipe appears at most once in a
    #: week's shortlist, so these are always distinct.
    sources: list[str] = field(default_factory=list)
    #: sort_key(line) — the identity the learned aisle map is keyed by.
    key: str = ""
    aisle: str = UNSORTED
    #: True when this week's kitchen check says we already have it.
    have: bool = False

    @property
    def check_id(self) -> str:
        return check_key(self.line)

    @property
    def is_staple(self) -> bool:
        return STAPLE in self.sources

    @property
    def summary(self) -> str:
        return ", ".join(self.sources)

    @property
    def shared(self) -> bool:
        """True when more than one dinner needs this — worth seeing at a glance."""
        return len(self.sources) > 1


def sort_key(line: str) -> str:
    """A rough 'what is this ingredient' key, for putting like next to like.

    Accents are folded first, so "jalapeños" and "jalapenos" reach the same
    key instead of being learned as two different aisles.

    ponytail: word-prefix stripping, not real parsing. It gets quantities and
    units off the front, which is most of the value; it will not group
    "red onion" with "onion". Swap in an ingredient parser if that stops being
    good enough.
    """
    flat = unicodedata.normalize("NFKD", line.lower())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    words = re.sub(r"[^a-z\s]", " ", flat).split()
    while words and (words[0] in _UNITS or words[0] in _NOISE):
        words.pop(0)
    return " ".join(words) or line.lower().strip()


def build_list(planned, known_aisles=None, staples=(), have=()) -> list[Item]:
    """Everything the week proposes: the shortlisted recipes' ingredients plus
    the always-check staples.

    - `planned`  db.get_plan()'s recipe rows
    - `known_aisles`  db.get_aisles(); anything missing stays UNSORTED
    - `staples`  db.get_staples() rows, folded in as their own source
    - `have`  db.get_pantry(); item keys already in the kitchen

    Returns the whole proposed list — filter with `to_buy` for the shopping
    list, so the kitchen check can still show what was ticked off.
    """
    known_aisles = known_aisles or {}
    have = set(have)
    items: dict[str, Item] = {}

    def add(line: str, source: str) -> None:
        line = line.strip()
        if not line:
            return
        cid = check_key(line)
        if cid not in items:
            key = sort_key(line)
            items[cid] = Item(
                line=line, key=key,
                aisle=known_aisles.get(key, UNSORTED),
                have=cid in have,
            )
        if source not in items[cid].sources:
            items[cid].sources.append(source)

    for recipe in planned:
        if recipe is None:
            continue
        for raw in (recipe["ingredients"] or "").splitlines():
            add(raw, recipe["title"])
    for staple in staples:
        add(staple["line"], STAPLE)

    return sorted(items.values(), key=lambda i: (i.key, i.line.lower()))


def to_buy(items) -> list[Item]:
    """Only what the kitchen check did not tick off."""
    return [i for i in items if not i.have]


def group_by_aisle(items) -> list[tuple[str, list[Item]]]:
    """Items bucketed into AISLES order, empty aisles dropped, unsorted last."""
    buckets: dict[str, list[Item]] = {}
    for item in items:
        buckets.setdefault(item.aisle if item.aisle in AISLES else UNSORTED, []).append(item)
    order = [*AISLES, UNSORTED]
    return [(name, buckets[name]) for name in order if buckets.get(name)]
