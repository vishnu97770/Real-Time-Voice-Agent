"""Persistence: outbound call jobs and finished call results.

Synchronous SQLAlchemy behind a small repository; the async service calls it in
a worker thread. PostgreSQL in production (SQLite in memory in the tests). The schema
belongs to Alembic (../alembic): run `alembic upgrade head`. Only a throwaway in-memory
database creates its own tables.
"""

import functools
import json
import threading
import time
from contextlib import nullcontext
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    event,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Agent, Base, Contact, Organization, Workflow
from app.models.enums import WorkflowStatus


class JobRow(Base):
    __tablename__ = "call_jobs"
    __table_args__ = (
        # Each link is checked together with organization_id, so a job can only point at an agent,
        # contact or workflow of its own organization. (A composite foreign key is not checked when
        # any of its columns is NULL, hence the CHECK below.)
        ForeignKeyConstraint(
            ["agent_id", "organization_id"], ["agents.id", "agents.organization_id"], name="fk_call_jobs_agent_id_agents"
        ),
        ForeignKeyConstraint(
            ["contact_id", "organization_id"],
            ["contacts.id", "contacts.organization_id"],
            name="fk_call_jobs_contact_id_contacts",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_call_jobs_workflow_id_workflows",
        ),
        CheckConstraint(
            "(agent_id IS NULL AND contact_id IS NULL AND workflow_id IS NULL) OR organization_id IS NOT NULL",
            name="domain_links_need_organization",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # The business's own idempotency key: sending the same one twice makes one job.
    reference: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    profile_id: Mapped[str] = mapped_column(String(40))
    callee_name: Mapped[str] = mapped_column(String(80))
    customer_ref: Mapped[str | None] = mapped_column(String(64))
    # "web" (the callee opens a link) or "phone" (Twilio rings them). NULL = web.
    channel: Mapped[str | None] = mapped_column(String(8))
    twilio_call_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    callee_phone: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(200))
    callback_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    answer_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)
    max_duration_seconds: Mapped[int] = mapped_column(Integer)
    answered_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    call_id: Mapped[str | None] = mapped_column(String(64))
    end_reason: Mapped[str | None] = mapped_column(String(24))
    callback_status: Mapped[str] = mapped_column(String(12), default="none")
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # The tenant that owns the job. NULL for rows that predate multi-tenancy.
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    # Which agent makes the call, who is called, and the workflow that created the job. All optional:
    # a job created by hand or through the plain API has none of them. Set any of them and the
    # organization_id must be set too (and they must belong to it).
    agent_id: Mapped[int | None] = mapped_column(index=True)
    contact_id: Mapped[int | None] = mapped_column(index=True)
    workflow_id: Mapped[int | None] = mapped_column(index=True)

    # Each relationship also fills organization_id, and the composite foreign keys keep them consistent.
    organization: Mapped[Organization | None] = relationship(
        back_populates="call_jobs", overlaps="agent,contact,workflow,call_jobs"
    )
    agent: Mapped[Agent | None] = relationship(
        back_populates="call_jobs", overlaps="organization,contact,workflow,call_jobs"
    )
    contact: Mapped[Contact | None] = relationship(
        back_populates="call_jobs", overlaps="organization,agent,workflow,call_jobs"
    )
    workflow: Mapped[Workflow | None] = relationship(
        back_populates="call_jobs", overlaps="organization,agent,contact,call_jobs"
    )


class CustomerRow(Base):
    """A customer's data as one profile's tools see it (same shape as the profile's
    demo data). Changes made by confirmed actions are written back here."""

    __tablename__ = "customers"

    profile_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    data: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[float] = mapped_column(Float)


class UserRow(Base):
    """A person who can sign in to the operator console."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(12))  # admin | operator
    disabled: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float)
    # NULL for users that predate multi-tenancy (not yet assigned to an organization).
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)

    organization: Mapped[Organization | None] = relationship(back_populates="users")


class AuthSessionRow(Base):
    """A signed-in browser. Only the hash of the cookie's token is stored."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class ResultRow(Base):
    """The call result of every finished call, inbound or outbound."""

    __tablename__ = "call_results"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    profile_id: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(32))
    payload: Mapped[Any] = mapped_column(JSON)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)


def _row_dict(row: Base) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def locked(method):
    """Serialise a repository call when the connection is shared between threads."""

    @functools.wraps(method)
    def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite ignores foreign keys unless each connection asks for them (PostgreSQL always enforces
    them). Without this, the tenant-integrity constraints would only exist on PostgreSQL."""
    cursor = dbapi_connection.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


class Repository:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        # A single shared connection (in-memory SQLite) must not be used by two
        # threads at once. File and server databases pool connections and don't
        # need this.
        self._lock: Any = nullcontext()
        self._ephemeral = False

        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}

            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool  # one shared in-memory database
                self._lock = threading.RLock()
                self._ephemeral = True

        # hide_parameters keeps caller data out of the SQL error messages that get logged.
        self.engine = create_engine(url, hide_parameters=True, **kwargs)

        if self.engine.dialect.name == "sqlite":
            # The setting belongs to a connection, not the database, so it runs for every new
            # connection the pool opens, not just the first.
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

        if self._ephemeral:
            # A throwaway database has no history to migrate (this is what the tests use).
            # Every other database gets its schema from Alembic and nothing else.
            Base.metadata.create_all(self.engine)

    def migrated(self) -> bool:
        """Whether Alembic has ever been run on this database."""
        return self._ephemeral or inspect(self.engine).has_table("alembic_version")

    # --- jobs -------------------------------------------------------------

    @locked
    def create_job(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Returns (job, created). If the reference already exists, the existing
        job comes back with created=False."""
        with self._sessions() as db:
            try:
                db.add(JobRow(**values))
                db.commit()
            except IntegrityError:
                db.rollback()

                # Only a repeated reference means "this job already exists". Without one, the
                # error is something else (e.g. a link to another organization's agent).
                if values.get("reference") is None:
                    raise

                existing = db.scalar(select(JobRow).where(JobRow.reference == values["reference"]))

                if existing is None:
                    raise

                return _row_dict(existing), False

            return _row_dict(db.get(JobRow, values["id"])), True

    @locked
    def job_link_problem(
        self, organization_id: int, agent_id: int | None, contact_id: int | None, workflow_id: int | None
    ) -> str | None:
        """Why a job cannot link to these, or None if it can. The database refuses the same
        mistakes; this gives the caller a readable reason first. One message for "missing" and
        "belongs to someone else", so a caller cannot probe other organizations' ids."""
        with self._sessions() as db:
            if db.get(Organization, organization_id) is None:
                return f"Unknown organization {organization_id}"

            for model, label, ident in ((Agent, "agent", agent_id), (Contact, "contact", contact_id), (Workflow, "workflow", workflow_id)):
                if ident is None:
                    continue

                found = db.scalar(select(model.id).where(model.id == ident, model.organization_id == organization_id))

                if found is None:
                    return f"Unknown {label} {ident} in organization {organization_id}"

            return None

    @locked
    def get_job_by_reference(self, reference: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.scalar(select(JobRow).where(JobRow.reference == reference))
            return _row_dict(row) if row else None

    @locked
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.get(JobRow, job_id)
            return _row_dict(row) if row else None

    @locked
    def list_jobs(self, limit: int) -> list[dict[str, Any]]:
        with self._sessions() as db:
            rows = db.scalars(select(JobRow).order_by(JobRow.created_at.desc()).limit(limit))
            return [_row_dict(row) for row in rows]

    @locked
    def transition(self, job_id: str, from_statuses: list[str], to_status: str, **fields: Any) -> bool:
        """Move a job between states atomically. Returns False if it was not in
        one of `from_statuses`, which is how two racing requests (two answers, an
        answer and an expiry) are resolved to exactly one winner."""
        with self._sessions() as db:
            result = db.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.status.in_(from_statuses))
                .values(status=to_status, **fields)
            )
            db.commit()
            return result.rowcount == 1

    @locked
    def update_job(self, job_id: str, **fields: Any) -> None:
        with self._sessions() as db:
            db.execute(update(JobRow).where(JobRow.id == job_id).values(**fields))
            db.commit()

    @locked
    def due_ringing(self, now: float) -> list[str]:
        with self._sessions() as db:
            return list(
                db.scalars(select(JobRow.id).where(JobRow.status == "ringing", JobRow.expires_at <= now))
            )

    @locked
    def due_in_progress(self, now: float, slack: float) -> list[tuple[str, str | None]]:
        """(job id, call id) of the jobs answered longer ago than their own time limit plus `slack`."""
        with self._sessions() as db:
            rows = db.execute(
                select(JobRow.id, JobRow.call_id).where(
                    JobRow.status == "in_progress",
                    JobRow.answered_at + JobRow.max_duration_seconds + slack <= now,
                )
            )
            return [(job_id, call_id) for job_id, call_id in rows]

    @locked
    def list_scheduled_phone_jobs(self, limit: int) -> list[dict[str, Any]]:
        """The oldest jobs that are recorded but not yet placed, up to `limit`. Uses the status
        index; the order (created_at, then id) makes a run's selection deterministic."""
        with self._sessions() as db:
            rows = db.scalars(
                select(JobRow)
                .where(JobRow.status == "scheduled", JobRow.channel == "phone")
                .order_by(JobRow.created_at, JobRow.id)
                .limit(limit)
            )
            return [_row_dict(row) for row in rows]

    @locked
    def pending_callbacks(self) -> list[str]:
        with self._sessions() as db:
            return list(db.scalars(select(JobRow.id).where(JobRow.callback_status == "pending")))

    # --- domain records (read-only) -----------------------------------------
    # Detached model objects: their columns are loaded, their relationships are not.

    @locked
    def create_organization(self, name: str, industry: str | None = None) -> Organization:
        """Test/admin use only: nothing in the HTTP API creates an organization (Step 18B added
        read-only organization routes; provisioning one is still direct database access, same as
        it was before this step)."""
        with self._sessions() as db:
            row = Organization(name=name, industry=industry)
            db.add(row)
            db.commit()
            return db.get(Organization, row.id)

    @locked
    def get_organization(self, organization_id: int) -> Organization | None:
        with self._sessions() as db:
            return db.get(Organization, organization_id)

    @locked
    def get_agent(self, agent_id: int) -> Agent | None:
        with self._sessions() as db:
            return db.get(Agent, agent_id)

    @locked
    def list_agents(self, organization_id: int) -> list[Agent]:
        with self._sessions() as db:
            return list(db.scalars(select(Agent).where(Agent.organization_id == organization_id).order_by(Agent.id)))

    @locked
    def create_agent(self, organization_id: int, values: dict[str, Any]) -> Agent:
        """May raise IntegrityError (a duplicate name within the organization); the caller
        translates that into a readable Conflict, same as create_job does for a job reference."""
        with self._sessions() as db:
            row = Agent(organization_id=organization_id, **values)
            db.add(row)
            db.commit()
            return db.get(Agent, row.id)

    @locked
    def update_agent(self, agent_id: int, organization_id: int, fields: dict[str, Any]) -> Agent | None:
        """None if no agent with that id belongs to that organization (unknown, or another
        tenant's) - the caller cannot tell those two cases apart from this return value alone,
        which is what keeps one organization from probing another's ids. May raise IntegrityError."""
        with self._sessions() as db:
            if fields:
                result = db.execute(
                    update(Agent).where(Agent.id == agent_id, Agent.organization_id == organization_id).values(**fields)
                )
                db.commit()

                if result.rowcount == 0:
                    return None
            elif db.scalar(select(Agent.id).where(Agent.id == agent_id, Agent.organization_id == organization_id)) is None:
                return None

            return db.get(Agent, agent_id)

    @locked
    def get_workflow(self, workflow_id: int) -> Workflow | None:
        with self._sessions() as db:
            return db.get(Workflow, workflow_id)

    @locked
    def list_workflows(self, organization_id: int) -> list[Workflow]:
        with self._sessions() as db:
            return list(db.scalars(select(Workflow).where(Workflow.organization_id == organization_id).order_by(Workflow.id)))

    @locked
    def create_workflow(self, organization_id: int, values: dict[str, Any]) -> Workflow:
        """May raise IntegrityError: a duplicate name within the organization, or (the caller
        checks this first with job_link_problem, for a readable reason) an agent_id that does not
        belong to this organization - the composite foreign key refuses it either way."""
        with self._sessions() as db:
            row = Workflow(organization_id=organization_id, **values)
            db.add(row)
            db.commit()
            return db.get(Workflow, row.id)

    @locked
    def update_workflow(self, workflow_id: int, organization_id: int, fields: dict[str, Any]) -> Workflow | None:
        """None if no workflow with that id belongs to that organization - unknown and "someone
        else's" are indistinguishable from this return value, same as update_agent/update_contact."""
        with self._sessions() as db:
            if fields:
                result = db.execute(
                    update(Workflow).where(Workflow.id == workflow_id, Workflow.organization_id == organization_id).values(**fields)
                )
                db.commit()

                if result.rowcount == 0:
                    return None
            elif db.scalar(select(Workflow.id).where(Workflow.id == workflow_id, Workflow.organization_id == organization_id)) is None:
                return None

            return db.get(Workflow, workflow_id)

    @locked
    def delete_workflow(self, workflow_id: int, organization_id: int) -> bool:
        """True if a row belonging to that organization was deleted. May raise IntegrityError if a
        call job still references this workflow (no ON DELETE behavior is defined for that
        foreign key, same as delete_contact)."""
        with self._sessions() as db:
            result = db.execute(delete(Workflow).where(Workflow.id == workflow_id, Workflow.organization_id == organization_id))
            db.commit()
            return result.rowcount == 1

    @locked
    def list_active_workflow_ids(self, limit: int) -> list[int]:
        """The ids of workflows whose status is active, lowest id first, at most `limit`. Ids only:
        the workflow engine loads each workflow itself, and is the judge of whether it should run."""
        with self._sessions() as db:
            return list(
                db.scalars(
                    select(Workflow.id).where(Workflow.status == WorkflowStatus.ACTIVE).order_by(Workflow.id).limit(limit)
                )
            )

    @locked
    def get_contact(self, contact_id: int) -> Contact | None:
        with self._sessions() as db:
            return db.get(Contact, contact_id)

    @locked
    def list_contacts(self, organization_id: int) -> list[Contact]:
        with self._sessions() as db:
            return list(db.scalars(select(Contact).where(Contact.organization_id == organization_id).order_by(Contact.id)))

    @staticmethod
    def _contact_columns(values: dict[str, Any]) -> dict[str, Any]:
        """`metadata` (the schema's field name, and JSON's) is not the ORM attribute (`metadata_`:
        `metadata` is reserved by SQLAlchemy's declarative base). Passing the schema's dict straight
        to `Contact(...)` or `update(Contact).values(...)` does not raise - it silently sets an
        unrelated instance attribute and leaves the real column untouched - so this rename is not
        optional. See tests/test_contacts_api.py for the regression this guards."""
        values = dict(values)

        if "metadata" in values:
            values["metadata_"] = values.pop("metadata")

        return values

    @locked
    def create_contact(self, organization_id: int, values: dict[str, Any]) -> Contact:
        """May raise IntegrityError; the caller decides what that means (Contact has no unique
        constraint of its own today, but this stays consistent with create_agent)."""
        with self._sessions() as db:
            row = Contact(organization_id=organization_id, **self._contact_columns(values))
            db.add(row)
            db.commit()
            return db.get(Contact, row.id)

    @locked
    def update_contact(self, contact_id: int, organization_id: int, fields: dict[str, Any]) -> Contact | None:
        """None if no contact with that id belongs to that organization - unknown and "someone
        else's" are indistinguishable from this return value, same as update_agent."""
        with self._sessions() as db:
            fields = self._contact_columns(fields)

            if fields:
                result = db.execute(
                    update(Contact).where(Contact.id == contact_id, Contact.organization_id == organization_id).values(**fields)
                )
                db.commit()

                if result.rowcount == 0:
                    return None
            elif db.scalar(select(Contact.id).where(Contact.id == contact_id, Contact.organization_id == organization_id)) is None:
                return None

            return db.get(Contact, contact_id)

    @locked
    def delete_contact(self, contact_id: int, organization_id: int) -> bool:
        """True if a row belonging to that organization was deleted. May raise IntegrityError if a
        call job still references this contact (no ON DELETE behavior is defined for that foreign
        key, so the database refuses - the caller turns that into a readable Conflict)."""
        with self._sessions() as db:
            result = db.execute(delete(Contact).where(Contact.id == contact_id, Contact.organization_id == organization_id))
            db.commit()
            return result.rowcount == 1

    # --- users and sign-in sessions -----------------------------------------

    @locked
    def create_user(self, email: str, password_hash: str, role: str) -> dict[str, Any]:
        with self._sessions() as db:
            row = UserRow(
                email=email.lower(), password_hash=password_hash, role=role, disabled=0, created_at=time.time()
            )
            db.add(row)
            db.commit()
            return _row_dict(row)

    @locked
    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.scalar(select(UserRow).where(UserRow.email == email.lower()))
            return _row_dict(row) if row else None

    @locked
    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.get(UserRow, user_id)
            return _row_dict(row) if row else None

    @locked
    def list_users(self) -> list[dict[str, Any]]:
        with self._sessions() as db:
            return [_row_dict(row) for row in db.scalars(select(UserRow).order_by(UserRow.email))]

    @locked
    def update_user(self, user_id: int, **fields: Any) -> None:
        with self._sessions() as db:
            db.execute(update(UserRow).where(UserRow.id == user_id).values(**fields))
            db.commit()

    @locked
    def create_auth_session(self, token_hash: str, user_id: int, expires_at: float) -> None:
        with self._sessions() as db:
            db.add(
                AuthSessionRow(
                    token_hash=token_hash, user_id=user_id, created_at=time.time(), expires_at=expires_at
                )
            )
            db.commit()

    @locked
    def get_auth_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.get(AuthSessionRow, token_hash)
            return _row_dict(row) if row else None

    @locked
    def delete_auth_session(self, token_hash: str) -> None:
        with self._sessions() as db:
            db.execute(delete(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash))
            db.commit()

    @locked
    def delete_user_sessions(self, user_id: int) -> None:
        with self._sessions() as db:
            db.execute(delete(AuthSessionRow).where(AuthSessionRow.user_id == user_id))
            db.commit()

    @locked
    def purge_expired_sessions(self, now: float) -> None:
        with self._sessions() as db:
            db.execute(delete(AuthSessionRow).where(AuthSessionRow.expires_at <= now))
            db.commit()

    # --- customers ----------------------------------------------------------

    @locked
    def upsert_customer(self, profile_id: str, ref: str, display_name: str, data: dict[str, Any]) -> None:
        with self._sessions() as db:
            db.merge(
                CustomerRow(
                    profile_id=profile_id,
                    ref=ref,
                    display_name=display_name,
                    data=json.loads(json.dumps(data)),
                    updated_at=time.time(),
                )
            )
            db.commit()

    @locked
    def get_customer(self, profile_id: str, ref: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.get(CustomerRow, (profile_id, ref))
            return _row_dict(row) if row else None

    @locked
    def list_customers(self, profile_id: str) -> list[dict[str, Any]]:
        with self._sessions() as db:
            rows = db.scalars(select(CustomerRow).where(CustomerRow.profile_id == profile_id).order_by(CustomerRow.ref))
            return [{"ref": r.ref, "display_name": r.display_name, "updated_at": r.updated_at} for r in rows]

    @locked
    def save_customer_data(self, profile_id: str, ref: str, data: dict[str, Any]) -> None:
        """Write back what a confirmed action changed."""
        with self._sessions() as db:
            db.execute(
                update(CustomerRow)
                .where(CustomerRow.profile_id == profile_id, CustomerRow.ref == ref)
                .values(data=json.loads(json.dumps(data)), updated_at=time.time())
            )
            db.commit()

    # --- results ----------------------------------------------------------

    @locked
    def save_result(self, summary: dict[str, Any]) -> None:
        job_id = (summary.get("outbound") or {}).get("job_id")

        with self._sessions() as db:
            # The column exists for "the results of organization X". A result has no tenant of its own:
            # it belongs to its call job's (NULL for a job that predates organizations, and for an
            # inbound call, which has no job).
            organization_id = db.scalar(select(JobRow.organization_id).where(JobRow.id == job_id)) if job_id else None
            db.merge(
                ResultRow(
                    call_id=summary["call_id"],
                    job_id=job_id,
                    organization_id=organization_id,
                    profile_id=summary["profile_id"],
                    started_at=time.time(),
                    outcome=summary["outcome"],
                    # Round-trip through JSON so anything non-serialisable fails here.
                    payload=json.loads(json.dumps(summary, default=str)),
                )
            )
            db.commit()

    @locked
    def list_results(self, limit: int, direction: str | None = None) -> list[dict[str, Any]]:
        """Finished calls, newest first, as short rows (no transcript or audit)."""
        with self._sessions() as db:
            rows = db.scalars(select(ResultRow).order_by(ResultRow.started_at.desc()).limit(limit * 3))
            out = []

            for row in rows:
                p = row.payload
                if direction and p.get("direction") != direction:
                    continue

                out.append(
                    {
                        "call_id": p["call_id"],
                        "finished_at": row.started_at,
                        "started_at": p.get("started_at"),
                        "duration_seconds": p.get("duration_seconds"),
                        "profile_id": p.get("profile_id"),
                        "profile_name": p.get("profile_name"),
                        "direction": p.get("direction", "inbound"),
                        "channel": p.get("channel", "web"),
                        "outcome": p.get("outcome"),
                        "end_reason": p.get("end_reason"),
                        "job_id": (p.get("outbound") or {}).get("job_id"),
                        "callee_name": (p.get("outbound") or {}).get("callee_name"),
                        "summary": p.get("summary"),
                    }
                )

                if len(out) == limit:
                    break

            return out

    @locked
    def get_result(self, call_id: str) -> dict[str, Any] | None:
        with self._sessions() as db:
            row = db.get(ResultRow, call_id)
            return row.payload if row else None
