"""add polls and greeting cards tables

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-03 00:00:00.000000


"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'polls',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('question', sa.String(length=250), nullable=False),
        sa.Column('closes_at', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['family_id'], ['families.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_polls_family_id', 'polls', ['family_id'])

    op.create_table(
        'poll_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('poll_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=150), nullable=False),
        sa.ForeignKeyConstraint(['poll_id'], ['polls.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_poll_options_poll_id', 'poll_options', ['poll_id'])

    op.create_table(
        'poll_votes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('poll_id', sa.Integer(), nullable=False),
        sa.Column('option_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['option_id'], ['poll_options.id']),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.ForeignKeyConstraint(['poll_id'], ['polls.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('option_id', 'person_id', name='uq_poll_vote'),
    )
    op.create_index('ix_poll_votes_poll_id', 'poll_votes', ['poll_id'])

    op.create_table(
        'greeting_cards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.Integer(), nullable=False),
        sa.Column('recipient_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('occasion', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('send_date', sa.Date(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['family_id'], ['families.id']),
        sa.ForeignKeyConstraint(['recipient_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_greeting_cards_family_id', 'greeting_cards', ['family_id'])

    op.create_table(
        'card_signatures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['greeting_cards.id']),
        sa.ForeignKeyConstraint(['person_id'], ['people.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('card_id', 'person_id', name='uq_card_signature'),
    )
    op.create_index('ix_card_signatures_card_id', 'card_signatures', ['card_id'])


def downgrade():
    op.drop_index('ix_card_signatures_card_id', table_name='card_signatures')
    op.drop_table('card_signatures')
    op.drop_index('ix_greeting_cards_family_id', table_name='greeting_cards')
    op.drop_table('greeting_cards')
    op.drop_index('ix_poll_votes_poll_id', table_name='poll_votes')
    op.drop_table('poll_votes')
    op.drop_index('ix_poll_options_poll_id', table_name='poll_options')
    op.drop_table('poll_options')
    op.drop_index('ix_polls_family_id', table_name='polls')
    op.drop_table('polls')
