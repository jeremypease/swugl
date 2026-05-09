"""Add patriarch and matriarch to family

Revision ID: 569c25670903
Revises: d6315d4da1c5
Create Date: 2026-05-09 16:57:42.907021

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '569c25670903'
down_revision = 'd6315d4da1c5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('families', schema=None) as batch_op:
        batch_op.add_column(sa.Column('patriarch_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('matriarch_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_families_patriarch', 'people', ['patriarch_id'], ['id'])
        batch_op.create_foreign_key('fk_families_matriarch', 'people', ['matriarch_id'], ['id'])


def downgrade():
    with op.batch_alter_table('families', schema=None) as batch_op:
        batch_op.drop_constraint('fk_families_matriarch', type_='foreignkey')
        batch_op.drop_constraint('fk_families_patriarch', type_='foreignkey')
        batch_op.drop_column('matriarch_id')
        batch_op.drop_column('patriarch_id')
