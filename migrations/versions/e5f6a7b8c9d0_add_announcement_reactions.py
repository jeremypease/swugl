"""add announcement_reactions table

Revision ID: e5f6a7b8c9d0
Revises: f95db89b1c87
Create Date: 2026-06-03 00:00:00.000000


"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'f95db89b1c87'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'announcement_reactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('announcement_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('emoji', sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(['announcement_id'], ['announcements.id']),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('announcement_id', 'person_id', 'emoji', name='uq_reaction'),
    )
    op.create_index('ix_announcement_reactions_announcement_id',
                    'announcement_reactions', ['announcement_id'])


def downgrade():
    op.drop_index('ix_announcement_reactions_announcement_id',
                  table_name='announcement_reactions')
    op.drop_table('announcement_reactions')
