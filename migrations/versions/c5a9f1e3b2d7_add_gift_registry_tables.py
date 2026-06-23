"""add gift registry tables

Revision ID: c5a9f1e3b2d7
Revises: 9f42e2f51be1
Create Date: 2026-06-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5a9f1e3b2d7'
down_revision = '9f42e2f51be1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gift_registries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('family_id', sa.Integer(), nullable=False),
    sa.Column('recipient_person_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['people.id'], ),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.ForeignKeyConstraint(['family_id'], ['families.id'], ),
    sa.ForeignKeyConstraint(['recipient_person_id'], ['people.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gift_registries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gift_registries_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_gift_registries_family_id'), ['family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_gift_registries_recipient_person_id'), ['recipient_person_id'], unique=False)

    op.create_table('gift_registry_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('registry_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=True),
    sa.Column('notes', sa.String(length=300), nullable=True),
    sa.Column('claimed_by_person_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['claimed_by_person_id'], ['people.id'], ),
    sa.ForeignKeyConstraint(['registry_id'], ['gift_registries.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gift_registry_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gift_registry_items_registry_id'), ['registry_id'], unique=False)


def downgrade():
    with op.batch_alter_table('gift_registry_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gift_registry_items_registry_id'))
    op.drop_table('gift_registry_items')

    with op.batch_alter_table('gift_registries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gift_registries_recipient_person_id'))
        batch_op.drop_index(batch_op.f('ix_gift_registries_family_id'))
        batch_op.drop_index(batch_op.f('ix_gift_registries_event_id'))
    op.drop_table('gift_registries')
