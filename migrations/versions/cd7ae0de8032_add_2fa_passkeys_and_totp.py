"""Add 2FA: passkeys and TOTP

Revision ID: cd7ae0de8032
Revises: e054946c6c46
Create Date: 2026-05-23 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'cd7ae0de8032'
down_revision = 'e054946c6c46'
branch_labels = None
depends_on = None


def upgrade():
    # Add TOTP columns to users
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('totp_secret', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('totp_enabled', sa.Boolean(), server_default='0', nullable=False))

    # Create user_credentials table for WebAuthn passkeys
    op.create_table(
        'user_credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.Text(), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('device_name', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('credential_id'),
    )
    op.create_index('ix_user_credentials_user_id', 'user_credentials', ['user_id'])


def downgrade():
    op.drop_index('ix_user_credentials_user_id', table_name='user_credentials')
    op.drop_table('user_credentials')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('totp_enabled')
        batch_op.drop_column('totp_secret')
