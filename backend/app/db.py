"""Persistence: outbound call jobs and finished call results.

Synchronous SQLAlchemy behind a small repository; the async service calls it in
a worker thread. SQLite by default, any SQLAlchemy URL in production. Tables are
created on start-up: there are no migrations yet.
"""

import functools
import json
import threading
import time
from contextlib import nullcontext
from typing import Any

from sqlalchemy import JSON, Float, Integer, String, Text, create_engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "call_jobs"

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


def _row_dict(row: Base) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def locked(method):
    """Serialise a repository call when the connection is shared between threads."""

    @functools.wraps(method)
    def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Repository:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        # A single shared connection (in-memory SQLite) must not be used by two
        # threads at once. File and server databases pool connections and don't
        # need this.
        self._lock: Any = nullcontext()

        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}

            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool  # one shared in-memory database
                self._lock = threading.RLock()

        self.engine = create_engine(url, **kwargs)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """create_all() makes new tables but never touches existing ones, so a
        database created by an older version would lack newer columns. This adds
        them (nullable or defaulted columns only). Renames, drops and type changes
        need a real migration tool such as Alembic."""
        inspector = inspect(self.engine)

        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue

                existing = {column["name"] for column in inspector.get_columns(table.name)}

                for column in table.columns:
                    if column.name in existing:
                        continue
                    if not column.nullable and column.default is None:
                        raise RuntimeError(
                            f"Cannot add required column {table.name}.{column.name} to an existing table"
                        )

                    ddl = column.type.compile(self.engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}'))

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
                existing = db.scalar(select(JobRow).where(JobRow.reference == values["reference"]))

                if existing is None:
                    raise

                return _row_dict(existing), False

            return _row_dict(db.get(JobRow, values["id"])), True

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
    def pending_callbacks(self) -> list[str]:
        with self._sessions() as db:
            return list(db.scalars(select(JobRow.id).where(JobRow.callback_status == "pending")))

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
            db.merge(
                ResultRow(
                    call_id=summary["call_id"],
                    job_id=job_id,
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
