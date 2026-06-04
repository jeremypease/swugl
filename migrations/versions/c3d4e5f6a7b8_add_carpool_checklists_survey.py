"""add carpool, checklists, and event survey tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    # has_carpool column on events
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('has_carpool', sa.Boolean(), nullable=True, server_default='0'))

    op.create_table(
        'carpool_offers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('seats', sa.Integer(), nullable=True),
        sa.Column('notes', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'person_id', name='uq_carpool_offer'),
    )
    op.create_index('ix_carpool_offers_event_id', 'carpool_offers', ['event_id'])

    op.create_table(
        'checklists',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('list_type', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['family_id'], ['families.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_checklists_family_id', 'checklists', ['family_id'])

    op.create_table(
        'checklist_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('checklist_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('is_done', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('claimed_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['checklist_id'], ['checklists.id']),
        sa.ForeignKeyConstraint(['claimed_by_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_checklist_items_checklist_id', 'checklist_items', ['checklist_id'])

    op.create_table(
        'event_survey_responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('what_worked', sa.Text(), nullable=True),
        sa.Column('suggestions', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'person_id', name='uq_survey_response'),
    )
    op.create_index('ix_event_survey_responses_event_id', 'event_survey_responses', ['event_id'])


def downgrade():
    op.drop_index('ix_event_survey_responses_event_id', table_name='event_survey_responses')
    op.drop_table('event_survey_responses')
    op.drop_index('ix_checklist_items_checklist_id', table_name='checklist_items')
    op.drop_table('checklist_items')
    op.drop_index('ix_checklists_family_id', table_name='checklists')
    op.drop_table('checklists')
    op.drop_index('ix_carpool_offers_event_id', table_name='carpool_offers')
    op.drop_table('carpool_offers')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_column('has_carpool')
