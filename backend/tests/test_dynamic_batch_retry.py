"""Only a rolled-back MySQL deadlock may retry a batch database transaction."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pymysql.err import OperationalError as DriverError
from sqlalchemy.exc import OperationalError

from backend.app.db.dynamic_repository import DynamicRepository
from backend.app.db.run_repository import RepositoryError
from backend.app.evidence.repository import digest


def setup_store(monkeypatch, errors, dialect="mysql"):
    engine = MagicMock()
    engine.dialect.name = dialect
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.first.return_value = None
    store = DynamicRepository(engine)
    calls = []

    def guard(conn, claim):
        calls.append(claim)
        error = errors[len(calls) - 1] if len(calls) <= len(errors) else None
        if error:
            raise error
        return {}

    monkeypatch.setattr(store, "_guard", guard)
    return store, calls


def deadlock(code=1213):
    return OperationalError("batch", {}, DriverError(code, "synthetic database failure"))


def save(store):
    store.save_batch(SimpleNamespace(run_id="run"), digest("batch"), digest("source"), {"facts": []})


def test_deadlock_retries_and_revalidates_claim(monkeypatch):
    store, calls = setup_store(monkeypatch, [deadlock()])
    save(store)
    assert len(calls) == 2


@pytest.mark.parametrize("dialect,code,attempts", [("mysql", 1213, 3), ("mysql", 1205, 1), ("sqlite", 1213, 1)])
def test_database_retry_is_bounded_and_only_handles_mysql_deadlock(monkeypatch, dialect, code, attempts):
    error = deadlock(code)
    store, calls = setup_store(monkeypatch, [error] * 4, dialect)
    with pytest.raises(OperationalError) as raised:
        save(store)
    assert raised.value is error
    assert len(calls) == attempts


def test_deadlock_retry_stops_if_claim_has_become_stale(monkeypatch):
    store, calls = setup_store(monkeypatch, [deadlock(), RepositoryError("stale_attempt")])
    with pytest.raises(RepositoryError, match="stale_attempt"):
        save(store)
    assert len(calls) == 2
