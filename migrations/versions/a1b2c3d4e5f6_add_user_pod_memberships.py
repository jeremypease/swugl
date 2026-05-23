"""Add user_pod_memberships for multi-pod support

Revision ID: a1b2c3d4e5f6
Revises: cd7ae0de8032
Create Date: 2026-05-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'cd7ae0de8032'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_pod_memberships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='member'),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['family_id'], ['families.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'family_id'),
    )
    op.create_index('ix_user_pod_memberships_user_id', 'user_pod_memberships', ['user_id'])
    op.create_index('ix_user_pod_memberships_family_id', 'user_pod_memberships', ['family_id'])

    # Populate from existing User rows so every existing user has a membership.
    # role = 'admin' if the user is_admin, else 'delegate' if is_delegate, else 'member'.
    op.execute("""
        INSERT INTO user_pod_memberships (user_id, family_id, role, joined_at)
        SELECT id, family_id,
               CASE WHEN is_admin THEN 'admin'
                    WHEN is_delegate THEN 'delegate'
                    ELSE 'member' END,
               CURRENT_TIMESTAMP
        FROM users
        WHERE status IN ('approved', 'invited')
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.drop_index('ix_user_pod_memberships_family_id', table_name='user_pod_memberships')
    op.drop_index('ix_user_pod_memberships_user_id', table_name='user_pod_memberships')
    op.drop_table('user_pod_memberships')
