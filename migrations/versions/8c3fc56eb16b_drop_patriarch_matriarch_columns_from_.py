"""drop patriarch matriarch columns from families

Revision ID: 8c3fc56eb16b
Revises: 62948a3f893c
Create Date: 2026-06-07 14:55:56.937339

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c3fc56eb16b'
down_revision = '62948a3f893c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('families', schema=None) as batch_op:
        batch_op.drop_column('patriarch_id')
        batch_op.drop_column('matriarch_id')


def downgrade():
    with op.batch_alter_table('families', schema=None) as batch_op:
        batch_op.add_column(sa.Column('matriarch_id', sa.INTEGER(), nullable=True))
        batch_op.add_column(sa.Column('patriarch_id', sa.INTEGER(), nullable=True))
