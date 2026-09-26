"""domain model: organizations, agents, contacts, workflows

Adds the four tenant-owned tables and a nullable organization_id (with foreign key and
index) to users, call_jobs and call_results. Purely additive: existing rows are kept and
simply have no organization yet. Nothing is dropped or rewritten.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20 17:20:54.633043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('industry', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('active', 'suspended')", name=op.f('ck_organizations_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations'))
    )
    op.create_table('agents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.String(length=120), nullable=True),
    sa.Column('industry', sa.String(length=64), nullable=True),
    sa.Column('purpose', sa.Text(), nullable=True),
    sa.Column('target_users', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('primary_tasks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('behavior_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('instructions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('language', sa.String(length=16), server_default='en', nullable=False),
    sa.Column('voice', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name=op.f('ck_agents_status_valid')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_agents_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agents')),
    sa.UniqueConstraint('id', 'organization_id', name='uq_agents_id_organization_id'),
    sa.UniqueConstraint('organization_id', 'name', name='uq_agents_organization_id_name')
    )
    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('phone', sa.String(length=24), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('consent_status', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('preferred_language', sa.String(length=16), nullable=True),
    sa.Column('preferred_contact_time', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("consent_status IN ('unknown', 'granted', 'revoked')", name=op.f('ck_contacts_consent_status_valid')),
    sa.CheckConstraint("status IN ('active', 'inactive')", name=op.f('ck_contacts_status_valid')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_contacts_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts'))
    )
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.create_index('ix_contacts_organization_id_phone', ['organization_id', 'phone'], unique=False)

    op.create_table('workflows',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('agent_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('trigger_type', sa.String(length=32), nullable=False),
    sa.Column('trigger_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('conditions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('action_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('retry_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'active', 'paused', 'archived')", name=op.f('ck_workflows_status_valid')),
    sa.ForeignKeyConstraint(['agent_id', 'organization_id'], ['agents.id', 'agents.organization_id'], name='fk_workflows_agent_id_agents'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_workflows_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflows')),
    sa.UniqueConstraint('organization_id', 'name', name='uq_workflows_organization_id_name')
    )
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.create_index('ix_workflows_agent_id', ['agent_id'], unique=False)

    with op.batch_alter_table('call_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_call_jobs_organization_id'), ['organization_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_call_jobs_organization_id_organizations'), 'organizations', ['organization_id'], ['id'])

    with op.batch_alter_table('call_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_call_results_organization_id'), ['organization_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_call_results_organization_id_organizations'), 'organizations', ['organization_id'], ['id'])

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_organization_id'), ['organization_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_users_organization_id_organizations'), 'organizations', ['organization_id'], ['id'])



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_users_organization_id_organizations'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_organization_id'))
        batch_op.drop_column('organization_id')

    with op.batch_alter_table('call_results', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_call_results_organization_id_organizations'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_call_results_organization_id'))
        batch_op.drop_column('organization_id')

    with op.batch_alter_table('call_jobs', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_call_jobs_organization_id_organizations'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_call_jobs_organization_id'))
        batch_op.drop_column('organization_id')

    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.drop_index('ix_workflows_agent_id')

    op.drop_table('workflows')
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_index('ix_contacts_organization_id_phone')

    op.drop_table('contacts')
    op.drop_table('agents')
    op.drop_table('organizations')
