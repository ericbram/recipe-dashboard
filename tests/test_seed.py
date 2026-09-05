import json

import pytest

from app import db, seed


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "seed.db"))
    db.init_schema(c)
    yield c
    c.close()


def write(tmp_path, entries):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(entries))
    return path


def test_seeds_entries_with_tags_and_notes(conn, tmp_path):
    path = write(tmp_path, [
        {"title": "Farro Bowl", "source_url": "https://example.com/farro",
         "tags": ["lunch", "bowls"], "notes": "double the chickpeas"},
    ])
    assert seed.seed(conn, path) == (1, 0, [])
    row = db.list_recipes(conn)[0]
    assert row["title"] == "Farro Bowl"
    assert row["tags"] == "bowls, lunch"
    assert row["notes"] == "double the chickpeas"
    assert row["source_url"] == "https://example.com/farro"


def test_running_twice_adds_nothing_the_second_time(conn, tmp_path):
    path = write(tmp_path, [{"title": "Farro Bowl", "tags": ["lunch"]}])
    assert seed.seed(conn, path) == (1, 0, [])
    assert seed.seed(conn, path) == (0, 1, [])
    assert len(db.list_recipes(conn)) == 1


def test_an_existing_recipe_is_not_overwritten(conn, tmp_path):
    """A recipe you have already filled in must survive a re-seed intact."""
    db.create_recipe(conn, "Farro Bowl", ingredients="farro\nbroccoli", tags=["mine"])
    path = write(tmp_path, [{"title": "farro bowl", "tags": ["lunch"]}])
    assert seed.seed(conn, path) == (0, 1, [])
    row = db.list_recipes(conn)[0]
    assert row["ingredients"] == "farro\nbroccoli"
    assert row["tags"] == "mine"


def test_a_recipe_with_no_url_still_seeds(conn, tmp_path):
    path = write(tmp_path, [{"title": "Pickled Cauliflower", "source_url": "", "tags": ["veggie"]}])
    assert seed.seed(conn, path) == (1, 0, [])
    assert db.list_recipes(conn)[0]["source_url"] is None


def test_untitled_entries_are_reported_not_inserted(conn, tmp_path):
    path = write(tmp_path, [{"title": "", "tags": ["lunch"]}, {"title": "Good One"}])
    added, skipped, rejected = seed.seed(conn, path)
    assert (added, skipped) == (1, 0)
    assert len(rejected) == 1
    assert [r["title"] for r in db.list_recipes(conn)] == ["Good One"]


def test_dry_run_writes_nothing(conn, tmp_path):
    path = write(tmp_path, [{"title": "Farro Bowl"}])
    assert seed.seed(conn, path, dry_run=True) == (1, 0, [])
    assert db.list_recipes(conn) == []


def test_the_shipped_seed_file_is_loadable_and_consistent(conn):
    """Guards the real data file, not a fixture."""
    added, skipped, rejected = seed.seed(conn, dry_run=True)
    assert rejected == []
    assert added == 165
    assert skipped == 0

    entries = json.loads(seed.SEED_FILE.read_text())
    titles = [e["title"].strip().lower() for e in entries]
    assert len(titles) == len(set(titles)), "duplicate titles would silently drop recipes"
    assert all(e["title"].strip() for e in entries)
    assert all(e["tags"] for e in entries), "every recipe should be findable by tag"
