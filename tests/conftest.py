"""Tests need a reachable Postgres via the usual PG* variables, e.g.
`docker compose up -d --wait postgres` then

    PGHOST=localhost PGPORT=5433 PGUSER=postgres PGPASSWORD=dev PGDATABASE=recipes pytest

Each test gets its own throwaway schema, so tests never see each other's rows
and nothing is left behind.
"""

import uuid

import pytest

from app import db


@pytest.fixture
def conn():
    c = db.connect()
    schema = f"t_{uuid.uuid4().hex[:12]}"
    c.execute(f"CREATE SCHEMA {schema}")
    c.execute(f"SET search_path TO {schema}")
    db.init_schema(c)
    yield c
    c.execute(f"DROP SCHEMA {schema} CASCADE")
    c.close()
