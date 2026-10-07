"""Shared pytest configuration for backend/tests/radar. Keep it minimal: every radar group's tests rely on it.

- Markers ported from the GitHub version: `live` tests need the network and are skipped unless
  RADAR_LIVE=1, so the suite never hits Yahoo or Nasdaq by default; `soak` marks the long simulations.
- `radar_db_url` / `radar_store`: a RadarStore on a fresh SQLite file with every radar table, for the
  store, tick, worker, housekeeping and API tests (no live Postgres in tests).
- Autouse: the worker's RADAR_RUN_ID / RADAR_TICK_CONTEXT are cleared, so every test starts as a tick run
  by hand.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from radar.store import RadarStore


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: needs the network; runs only when RADAR_LIVE=1")
    config.addinivalue_line("markers", "soak: long simulation; run it on its own with -m soak")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RADAR_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RADAR_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests expect a tick run by hand: run id "local" and the loop bookkeeping stored by the previous tick.
    What radar.tick reads from the environment (the worker's run id and tick context) must not leak in from
    the shell, e.g. one that ran the worker; a test that needs them sets them itself."""
    from radar.tick import CONTEXT_ENV, RUN_ID_ENV

    for name in (RUN_ID_ENV, CONTEXT_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def radar_db_url(tmp_path: Path) -> str:
    """A SQLite file URL with every radar table created (a file, not :memory:, so each connection of
    the pool and any subprocess see the same database)."""
    from sqlalchemy import create_engine

    from db.models import Base
    from radar.store import RADAR_TABLES

    url = f"sqlite:///{(tmp_path / 'radar.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=list(RADAR_TABLES))
    engine.dispose()
    return url


@pytest.fixture
def radar_store(radar_db_url: str) -> Iterator["RadarStore"]:
    from radar.store import RadarStore

    store = RadarStore(radar_db_url)
    try:
        yield store
    finally:
        store.close()
