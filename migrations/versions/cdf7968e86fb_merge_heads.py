"""Merge migration branches

Revision ID: cdf7968e86fb
Revises: d4e5f6a7b8c9, d3bb88a89449
Create Date: 2026-06-04 02:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'cdf7968e86fb'
down_revision = ('d4e5f6a7b8c9', 'd3bb88a89449')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
