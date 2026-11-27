"""meta ad-attribution cookies on users

Revision ID: f3d8b21c6a07
Revises: e7f2a90c4d11
Create Date: 2026-11-27 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3d8b21c6a07'
down_revision: Union[str, None] = 'e7f2a90c4d11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive only: nullable columns, existing rows untouched.
    op.add_column('users', sa.Column('meta_fbp', sa.String(length=128), nullable=True))
    op.add_column('users', sa.Column('meta_fbc', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'meta_fbc')
    op.drop_column('users', 'meta_fbp')
