"""Add notification_preferences table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-23 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

# Default preferences seeded for every existing approved user.
_DEFAULTS = [
    ('digest',        True),
    ('new_event',     True),
    ('announcement',  True),
    ('new_member',    False),
    ('rsvp_reminder', True),
]


def upgrade():
    op.create_table(
        'notification_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False, server_default='email'),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'event_type', 'channel'),
    )
    op.create_index('ix_notification_preferences_user_id', 'notification_preferences', ['user_id'])

    # Seed defaults for every existing approved user.
    conn = op.get_bind()
    users = conn.execute(
        sa.text("SELECT id FROM users WHERE status = 'approved'")
    ).fetchall()
    rows = [
        {'user_id': u[0], 'event_type': et, 'channel': 'email', 'enabled': en}
        for u in users
        for et, en in _DEFAULTS
    ]
    if rows:
        conn.execute(
            sa.text(
                "INSERT INTO notification_preferences (user_id, event_type, channel, enabled) "
                "VALUES (:user_id, :event_type, :channel, :enabled) "
                "ON CONFLICT DO NOTHING"
            ),
            rows,
        )


def downgrade():
    op.drop_index('ix_notification_preferences_user_id', table_name='notification_preferences')
    op.drop_table('notification_preferences')
