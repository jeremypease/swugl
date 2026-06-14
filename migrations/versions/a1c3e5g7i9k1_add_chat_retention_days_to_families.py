"""Add chat_retention_days to families

Revision ID: a1c3e5g7i9k1
Revises: f95db89b1c87
Create Date: 2026-06-13 15:50:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1c3e5g7i9k1'
down_revision = 'f95db89b1c87'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('families', sa.Column('chat_retention_days', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('families', 'chat_retention_days')
