"""Shared fixtures — a seeded SQLite database for the test session.

Seeds once into a temp DB and points the app/config at it so every test runs
against the real dataset rather than fixtures.
"""

from __future__ import annotations

import pytest

from energyintelligence.config import get_settings
from energyintelligence.db import connect


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "test.db"
    # Point config at the temp DB before seeding.
    get_settings.cache_clear()  # type: ignore[attr-defined]
    import os
    os.environ["DARKENERGY_DB_PATH"] = str(path)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    from energyintelligence.seed.loader import seed
    seed(db_path=path)
    return path


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    yield c
    c.close()
