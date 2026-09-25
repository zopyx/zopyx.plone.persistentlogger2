import os
from datetime import UTC, datetime

import pytest

from zopyx.plone.persistentlogger.models import LogEvent
from zopyx.plone.persistentlogger.rdbms import DuckDBRepository, PostgresRepository, SQLiteRepository


pytestmark = pytest.mark.integration


def make_event(comment="contract event"):
    return LogEvent(comment=comment, object_uid="contract", actor="tester", created_at=datetime.now(UTC))


@pytest.fixture(scope="module", params=["sqlite", "duckdb", "postgres"])
def backend(request, tmp_path_factory):
    name = request.param
    if name == "sqlite":
        repository = SQLiteRepository("contract", str(tmp_path_factory.mktemp("sqlite") / "audit.sqlite"))
        yield name, repository
        repository.close()
        return
    if name == "duckdb":
        repository = DuckDBRepository("contract", str(tmp_path_factory.mktemp("duckdb") / "audit.duckdb"))
        yield name, repository
        repository.close()
        return
    if not os.environ.get("RUN_INTEGRATION"):
        pytest.skip("PostgreSQL integration tests require RUN_INTEGRATION=1")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        repository = PostgresRepository("contract", container.get_connection_url())
        yield name, repository
        repository.close()


def test_backend_append_search_and_integrity(backend):
    name, repository = backend
    row = repository.append(make_event())
    assert row["object_uid"] == "contract"
    assert repository.search(quick="CONTRACT")[0]["event_id"] == row["event_id"]
    assert repository.verify()["ok"]
    assert repository.health()["backend"] == "rdbms"
    assert name in {"sqlite", "duckdb", "postgres"}


def test_backend_reopens_persisted_events(backend, tmp_path):
    name, repository = backend
    row = repository.append(make_event("persisted"))
    assert repository.get(row["event_id"])["comment"] == "persisted"
    if name == "sqlite":
        replacement = SQLiteRepository("contract", repository.connection.execute("PRAGMA database_list").fetchone()[2])
        assert replacement.search(quick="persisted")
        replacement.close()
    elif name == "duckdb":
        # The contract is exercised against the live reopened state for embedded DuckDB.
        repository._load()
        assert repository.search(quick="persisted")
    else:
        repository._load()
        assert repository.search(quick="persisted")


def test_backend_filters_and_empty_scope(backend):
    _, repository = backend
    repository.append(make_event("first"))
    repository.append(make_event("second"))
    assert repository.count(quick="second") == 1
    assert repository.search(limit=1)
    assert repository.search(quick="missing") == []
