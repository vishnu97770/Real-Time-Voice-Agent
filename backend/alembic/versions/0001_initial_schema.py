"""initial schema

The five tables the application created itself (via create_all) before Alembic was
introduced. Databases that already have them are left as they are: only missing tables
are created, so an existing database upgrades without a manual `alembic stamp`. The one
index that older databases lack (their twilio_call_sid column was added without it) is added.

Revision ID: 0001
Revises: 
Create Date: 2026-09-20 17:17:10.055781

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def exists(table: str) -> bool:
    """False in offline mode (`--sql`), where there is no database to ask."""
    return not context.is_offline_mode() and sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    """Upgrade schema."""
    if not exists("auth_sessions"):
        op.create_table('auth_sessions',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_auth_sessions'))
        )
        with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_auth_sessions_expires_at'), ['expires_at'], unique=False)
            batch_op.create_index(batch_op.f('ix_auth_sessions_user_id'), ['user_id'], unique=False)

    if not exists("call_jobs"):
        op.create_table('call_jobs',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=True),
        sa.Column('profile_id', sa.String(length=40), nullable=False),
        sa.Column('callee_name', sa.String(length=80), nullable=False),
        sa.Column('customer_ref', sa.String(length=64), nullable=True),
        sa.Column('channel', sa.String(length=8), nullable=True),
        sa.Column('twilio_call_sid', sa.String(length=64), nullable=True),
        sa.Column('callee_phone', sa.String(length=24), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('callback_url', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('answer_token', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.Float(), nullable=False),
        sa.Column('max_duration_seconds', sa.Integer(), nullable=False),
        sa.Column('answered_at', sa.Float(), nullable=True),
        sa.Column('finished_at', sa.Float(), nullable=True),
        sa.Column('call_id', sa.String(length=64), nullable=True),
        sa.Column('end_reason', sa.String(length=24), nullable=True),
        sa.Column('callback_status', sa.String(length=12), nullable=False),
        sa.Column('callback_attempts', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_call_jobs'))
        )
        with op.batch_alter_table('call_jobs', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_call_jobs_reference'), ['reference'], unique=True)
            batch_op.create_index(batch_op.f('ix_call_jobs_status'), ['status'], unique=False)
            batch_op.create_index(batch_op.f('ix_call_jobs_twilio_call_sid'), ['twilio_call_sid'], unique=False)

    if not exists("call_results"):
        op.create_table('call_results',
        sa.Column('call_id', sa.String(length=64), nullable=False),
        sa.Column('job_id', sa.String(length=40), nullable=True),
        sa.Column('profile_id', sa.String(length=40), nullable=False),
        sa.Column('started_at', sa.Float(), nullable=False),
        sa.Column('outcome', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('call_id', name=op.f('pk_call_results'))
        )
        with op.batch_alter_table('call_results', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_call_results_job_id'), ['job_id'], unique=False)

    if not exists("customers"):
        op.create_table('customers',
        sa.Column('profile_id', sa.String(length=40), nullable=False),
        sa.Column('ref', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=80), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('profile_id', 'ref', name=op.f('pk_customers'))
        )

    if not exists("users"):
        op.create_table('users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('password_hash', sa.String(length=200), nullable=False),
        sa.Column('role', sa.String(length=12), nullable=False),
        sa.Column('disabled', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
        )
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    # Databases upgraded by the old self-healing code got this column without its index.
    if exists("call_jobs") and "ix_call_jobs_twilio_call_sid" not in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("call_jobs")
    }:
        with op.batch_alter_table('call_jobs', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_call_jobs_twilio_call_sid'), ['twilio_call_sid'], unique=False)


def downgrade() -> None:
    """Deliberately not reversible: these tables predate Alembic and hold user data."""
    raise NotImplementedError("The initial schema cannot be downgraded; restore from a backup instead.")
