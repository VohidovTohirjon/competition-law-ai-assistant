"""Widen task status/priority columns so every enum value fits.

"shoshilinch" (11 chars) did not fit VARCHAR(10) and "bekor_qilindi" (13 chars)
did not fit VARCHAR(9); both writes failed with a 500.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("status", type_=sa.String(20), existing_type=sa.String(9),
                           existing_nullable=False)
        batch.alter_column("priority", type_=sa.String(20), existing_type=sa.String(10),
                           existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("priority", type_=sa.String(10), existing_type=sa.String(20),
                           existing_nullable=False)
        batch.alter_column("status", type_=sa.String(9), existing_type=sa.String(20),
                           existing_nullable=False)
