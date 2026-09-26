import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db import Repository
from tests.helpers import migrate

# The schema exactly as the application created it (via create_all) before Alembic existed,
# copied from a real development database. It has no alembic_version table.
LEGACY_SCHEMA = """
CREATE TABLE auth_sessions (token_hash VARCHAR(64) NOT NULL, user_id INTEGER NOT NULL,
    created_at FLOAT NOT NULL, expires_at FLOAT NOT NULL, PRIMARY KEY (token_hash));
CREATE TABLE call_jobs (id VARCHAR(40) NOT NULL, reference VARCHAR(64), profile_id VARCHAR(40) NOT NULL,
    callee_name VARCHAR(80) NOT NULL, customer_ref VARCHAR(64), callee_phone VARCHAR(24) NOT NULL,
    reason VARCHAR(200) NOT NULL, callback_url TEXT, status VARCHAR(20) NOT NULL, answer_token VARCHAR(64) NOT NULL,
    created_at FLOAT NOT NULL, expires_at FLOAT NOT NULL, max_duration_seconds INTEGER NOT NULL,
    answered_at FLOAT, finished_at FLOAT, call_id VARCHAR(64), end_reason VARCHAR(24),
    callback_status VARCHAR(12) NOT NULL, callback_attempts INTEGER NOT NULL, channel VARCHAR(8),
    twilio_call_sid VARCHAR(64), PRIMARY KEY (id));
CREATE TABLE call_results (call_id VARCHAR(64) NOT NULL, job_id VARCHAR(40), profile_id VARCHAR(40) NOT NULL,
    started_at FLOAT NOT NULL, outcome VARCHAR(32) NOT NULL, payload JSON NOT NULL, PRIMARY KEY (call_id));
CREATE TABLE customers (profile_id VARCHAR(40) NOT NULL, ref VARCHAR(64) NOT NULL, display_name VARCHAR(80) NOT NULL,
    data JSON NOT NULL, updated_at FLOAT NOT NULL, PRIMARY KEY (profile_id, ref));
CREATE TABLE users (id INTEGER NOT NULL, email VARCHAR(254) NOT NULL, password_hash VARCHAR(200) NOT NULL,
    role VARCHAR(12) NOT NULL, disabled INTEGER NOT NULL, created_at FLOAT NOT NULL, PRIMARY KEY (id));
CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);
CREATE UNIQUE INDEX ix_call_jobs_reference ON call_jobs (reference);
CREATE INDEX ix_call_jobs_status ON call_jobs (status);
CREATE INDEX ix_call_results_job_id ON call_results (job_id);
CREATE UNIQUE INDEX ix_users_email ON users (email);
"""

ROWS = [
    "INSERT INTO users VALUES (7, 'admin@example.com', 'hash', 'admin', 0, 1.5)",
    "INSERT INTO auth_sessions VALUES ('tok', 7, 1.5, 9e9)",
    "INSERT INTO call_jobs (id, reference, profile_id, callee_name, callee_phone, reason, status, answer_token, "
    "created_at, expires_at, max_duration_seconds, callback_status, callback_attempts) "
    "VALUES ('JOB-1', 'crm-1', 'bank', 'Priya', '+911234567890', 'r', 'completed', 't', 1, 2, 300, 'none', 0)",
    "INSERT INTO call_results VALUES ('call-1', 'JOB-1', 'bank', 1.5, 'completed', '{\"summary\": \"ok\"}')",
    "INSERT INTO customers VALUES ('bank', 'demo', 'Priya Sharma', '{\"balance\": 84250.75}', 1.5)",
]


def config(url: str) -> Config:
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def legacy_database(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)

    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        for row in ROWS:
            conn.execute(text(row))

    engine.dispose()
    return url


def test_a_database_created_before_alembic_upgrades_and_keeps_every_row(tmp_path):
    url = legacy_database(tmp_path)

    migrate(url)  # no manual `alembic stamp` needed

    repo = Repository(url)
    assert repo.migrated()
    assert repo.get_user_by_email("admin@example.com")["organization_id"] is None
    assert repo.get_job("JOB-1")["status"] == "completed"
    assert repo.get_job("JOB-1")["organization_id"] is None
    assert repo.get_result("call-1")["summary"] == "ok"
    assert repo.get_customer("bank", "demo")["data"] == {"balance": 84250.75}

    engine = create_engine(url)
    inspector = inspect(engine)
    assert {"organizations", "agents", "contacts", "workflows"} <= set(inspector.get_table_names())
    assert "ix_call_jobs_reference" in {i["name"] for i in inspector.get_indexes("call_jobs")}, "old indexes survive"
    assert engine.connect().execute(text("select version_num from alembic_version")).scalar() == "0003"
    command.check(config(url))  # and it now matches the models exactly (old databases lacked one index)


def test_the_new_migration_only_adds_on_postgresql():
    cfg = config("postgresql+psycopg://u:p@localhost/db")
    cfg.output_buffer = io.StringIO()

    command.upgrade(cfg, "0001:0002", sql=True)
    sql = cfg.output_buffer.getvalue().upper()

    for destructive in ("DROP ", "DELETE ", "TRUNCATE", "RENAME"):
        assert destructive not in sql
    assert sql.count("CREATE TABLE") == 4 and sql.count("ADD COLUMN") == 3
    assert "JSONB" in sql and "TIMESTAMP WITH TIME ZONE" in sql


def test_the_migrations_produce_exactly_the_schema_the_models_declare(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    migrate(url)

    command.check(config(url))  # raises if autogenerate would still find a difference


def test_downgrading_the_domain_migration_removes_only_what_it_added(tmp_path):
    url = legacy_database(tmp_path)
    migrate(url)
    command.downgrade(config(url), "0001")

    engine = create_engine(url)
    inspector = inspect(engine)
    assert not {"organizations", "agents", "contacts", "workflows"} & set(inspector.get_table_names())
    assert "organization_id" not in {c["name"] for c in inspector.get_columns("users")}
    assert engine.connect().execute(text("select email from users")).scalar() == "admin@example.com"
    assert engine.connect().execute(text("select id from call_jobs")).scalar() == "JOB-1"


def test_the_call_job_migration_only_adds_on_postgresql():
    cfg = config("postgresql+psycopg://u:p@localhost/db")
    cfg.output_buffer = io.StringIO()

    command.upgrade(cfg, "0002:0003", sql=True)
    sql = cfg.output_buffer.getvalue().upper()

    for destructive in ("DROP ", "DELETE ", "TRUNCATE", "RENAME", "CREATE TABLE"):
        assert destructive not in sql
    assert sql.count("ADD COLUMN") == 3 and sql.count("CREATE INDEX") == 3
    assert sql.count("ADD CONSTRAINT UQ_") == 2, "contacts and workflows become valid foreign key targets"
    assert sql.count("FOREIGN KEY(") == 3
    for link, parent in (("AGENT", "AGENTS"), ("CONTACT", "CONTACTS"), ("WORKFLOW", "WORKFLOWS")):
        assert f"FOREIGN KEY({link}_ID, ORGANIZATION_ID) REFERENCES {parent} (ID, ORGANIZATION_ID)" in sql
    assert "CHECK ((AGENT_ID IS NULL AND CONTACT_ID IS NULL AND WORKFLOW_ID IS NULL) OR ORGANIZATION_ID IS NOT NULL)" in sql


def test_an_old_call_job_keeps_every_field_through_the_call_job_migration(tmp_path):
    url = legacy_database(tmp_path)
    migrate(url, "0002")
    before = create_engine(url).connect().execute(text("select * from call_jobs")).mappings().one()

    migrate(url)
    after = create_engine(url).connect().execute(text("select * from call_jobs")).mappings().one()

    assert {k: v for k, v in after.items() if k in before} == dict(before), "no existing value changed"
    assert [after[k] for k in ("organization_id", "agent_id", "contact_id", "workflow_id")] == [None] * 4
    assert Repository(url).get_result("call-1")["summary"] == "ok"


def test_downgrading_the_call_job_migration_removes_only_what_it_added(tmp_path):
    url = legacy_database(tmp_path)
    migrate(url)
    command.downgrade(config(url), "0002")

    engine = create_engine(url)
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("call_jobs")}
    assert {"organization_id", "id", "callee_phone"} <= columns
    assert not {"agent_id", "contact_id", "workflow_id"} & columns
    assert {"organizations", "agents", "contacts", "workflows"} <= set(inspector.get_table_names())
    assert engine.connect().execute(text("select id from call_jobs")).scalar() == "JOB-1"


def test_the_initial_schema_cannot_be_downgraded(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    migrate(url, "0001")

    with pytest.raises(NotImplementedError):
        command.downgrade(config(url), "base")


def test_a_database_without_migrations_is_reported(tmp_path):
    engine_url = f"sqlite:///{tmp_path / 'bare.db'}"
    create_engine(engine_url).connect().close()

    assert not Repository(engine_url).migrated()
    assert Repository("sqlite://").migrated(), "an in-memory database builds its own schema"
