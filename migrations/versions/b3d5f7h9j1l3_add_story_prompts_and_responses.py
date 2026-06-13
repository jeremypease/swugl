"""Add story_prompts and story_responses tables

Revision ID: b3d5f7h9j1l3
Revises: a1c3e5g7i9k1
Create Date: 2026-06-13 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b3d5f7h9j1l3'
down_revision = 'a1c3e5g7i9k1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'story_prompts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('family_id', sa.Integer(), sa.ForeignKey('families.id'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('week_of', sa.Date(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_story_prompts_family_id', 'story_prompts', ['family_id'])

    op.create_table(
        'story_responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prompt_id', sa.Integer(), sa.ForeignKey('story_prompts.id'), nullable=False),
        sa.Column('person_id', sa.Integer(), sa.ForeignKey('people.id'), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('prompt_id', 'person_id', name='uq_story_response'),
    )
    op.create_index('ix_story_responses_prompt_id', 'story_responses', ['prompt_id'])
    op.create_index('ix_story_responses_person_id', 'story_responses', ['person_id'])


def downgrade():
    op.drop_table('story_responses')
    op.drop_table('story_prompts')
