"""The application's own SQLite connections enforce foreign keys, as PostgreSQL does.

Nothing here turns the pragma on: every check goes through a Repository, so the configuration
under test is the one production and development run with."""

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app.db import Repository, _enable_sqlite_foreign_keys
from tests.test_call_job_links import repo, seed, values  # noqa: F401  (repo is a fixture)


def foreign_keys(connection) -> int:
    return connection.execute(text("PRAGMA foreign_keys")).scalar()


# --- the pragma is on for every runtime connection -----------------------------------------------


def test_repository_connections_to_a_file_database_enforce_foreign_keys(repo):
    assert repo.engine.dialect.name == "sqlite"

    with repo.engine.connect() as connection:
        assert foreign_keys(connection) == 1


def test_every_connection_from_the_pool_enforces_them_not_just_the_first(repo):
    """The setting belongs to a connection. Hold several open at once so the pool must create new ones."""
    opened = [repo.engine.connect() for _ in range(4)]

    try:
        assert len({id(c.connection.dbapi_connection) for c in opened}) == 4, "four distinct connections"
        assert [foreign_keys(c) for c in opened] == [1, 1, 1, 1]
    finally:
        for connection in opened:
            connection.close()


def test_an_in_memory_repository_enforces_them_too():
    with Repository("sqlite://").engine.connect() as connection:
        assert foreign_keys(connection) == 1


def test_the_pragma_is_only_registered_for_sqlite():
    sqlite = Repository("sqlite://").engine
    postgres = Repository("postgresql+psycopg://user:pw@localhost/none").engine  # no connection is made

    assert event.contains(sqlite, "connect", _enable_sqlite_foreign_keys)
    assert not event.contains(postgres, "connect", _enable_sqlite_foreign_keys)


# --- tenant integrity through the normal runtime connection --------------------------------------


@pytest.mark.parametrize("link", ["agent", "contact", "workflow"])
def test_the_runtime_connection_rejects_another_organizations_agent_contact_or_workflow(repo, link):
    ids = seed(repo)

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        repo.create_job(values(organization_id=ids["acme"], **{f"{link}_id": ids[f"shield_{link}"]}))

    assert repo.get_job("JOB-1") is None, "nothing was stored"


@pytest.mark.parametrize("link", ["agent", "contact", "workflow"])
def test_the_runtime_connection_still_accepts_the_organizations_own_links(repo, link):
    ids = seed(repo)
    job, created = repo.create_job(values(organization_id=ids["acme"], **{f"{link}_id": ids[f"acme_{link}"]}))

    assert created and job[f"{link}_id"] == ids[f"acme_{link}"]

