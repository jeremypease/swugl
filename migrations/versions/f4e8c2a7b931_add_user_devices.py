"""add user_devices table for push notification tokens

Revision ID: f4e8c2a7b931
Revises: e07a4f070985
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4e8c2a7b931'
down_revision = 'e07a4f070985'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(length=10), nullable=False),
        sa.Column('token', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'token', name='uq_user_device_token'),
    )
    op.create_index('ix_user_devices_user_id', 'user_devices', ['user_id'], unique=False)


def downgrade():
    op.drop_index('ix_user_devices_user_id', table_name='user_devices')
    op.drop_table('user_devices')
