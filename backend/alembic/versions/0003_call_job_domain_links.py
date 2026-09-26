"""call job domain links: agent, contact and workflow, all inside one organization

Connects call_jobs to the domain model. call_jobs gets nullable agent_id, contact_id and
workflow_id (organization_id came with 0002). Purely additive: existing jobs are kept and simply
have none of these links, and the existing string primary key, columns and indexes are untouched.

Tenant integrity is enforced by the database. Each link is a composite foreign key that includes
organization_id, so a job cannot point at another organization's agent, contact or workflow. That
needs (id, organization_id) to be unique on the parent tables (agents already has it). A composite
foreign key is skipped when any of its columns is NULL, so a CHECK forbids a link without an
organization.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21 11:20:35.295541

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The foreign keys below can only reference columns that are unique together.
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_contacts_id_organization_id', ['id', 'organization_id'])

    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_workflows_id_organization_id', ['id', 'organization_id'])

    with op.batch_alter_table('call_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('contact_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('workflow_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_call_jobs_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_call_jobs_contact_id'), ['contact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_call_jobs_workflow_id'), ['workflow_id'], unique=False)
        batch_op.create_foreign_key('fk_call_jobs_agent_id_agents', 'agents', ['agent_id', 'organization_id'], ['id', 'organization_id'])
        batch_op.create_foreign_key('fk_call_jobs_contact_id_contacts', 'contacts', ['contact_id', 'organization_id'], ['id', 'organization_id'])
        batch_op.create_foreign_key('fk_call_jobs_workflow_id_workflows', 'workflows', ['workflow_id', 'organization_id'], ['id', 'organization_id'])
        batch_op.create_check_constraint(
            op.f('ck_call_jobs_domain_links_need_organization'),
            '(agent_id IS NULL AND contact_id IS NULL AND workflow_id IS NULL) OR organization_id IS NOT NULL',
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('call_jobs', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_call_jobs_domain_links_need_organization'), type_='check')
        batch_op.drop_constraint('fk_call_jobs_workflow_id_workflows', type_='foreignkey')
        batch_op.drop_constraint('fk_call_jobs_contact_id_contacts', type_='foreignkey')
        batch_op.drop_constraint('fk_call_jobs_agent_id_agents', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_call_jobs_workflow_id'))
        batch_op.drop_index(batch_op.f('ix_call_jobs_contact_id'))
        batch_op.drop_index(batch_op.f('ix_call_jobs_agent_id'))
        batch_op.drop_column('workflow_id')
        batch_op.drop_column('contact_id')
        batch_op.drop_column('agent_id')

    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.drop_constraint('uq_workflows_id_organization_id', type_='unique')

    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_constraint('uq_contacts_id_organization_id', type_='unique')
