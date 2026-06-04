"""add photo_tags table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'photo_tags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('photo_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('tagged_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.ForeignKeyConstraint(['photo_id'], ['photos.id']),
        sa.ForeignKeyConstraint(['tagged_by_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('photo_id', 'person_id', name='uq_photo_tag'),
    )
    op.create_index('ix_photo_tags_photo_id', 'photo_tags', ['photo_id'])


def downgrade():
    op.drop_index('ix_photo_tags_photo_id', table_name='photo_tags')
    op.drop_table('photo_tags')
