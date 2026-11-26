"""passwordless magic links: nullable password, marketing consent, nonce

Revision ID: e7f2a90c4d11
Revises: cb7ca9a25719
Create Date: 2026-11-26 06:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f2a90c4d11'
down_revision: Union[str, None] = 'cb7ca9a25719'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive + a constraint relaxation only; existing rows keep working
    # untouched (server_default makes the NOT NULL column safe to add).
    op.add_column(
        'users',
        sa.Column('marketing_opt_in', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('users', sa.Column('marketing_opt_in_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('magic_nonce', sa.String(length=64), nullable=True))
    op.alter_column('users', 'hashed_password', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    # Refuse to silently delete accounts: passwordless rows (NULL hash) get a
    # locked sentinel no bcrypt verify will ever match, then the constraint
    # comes back.
    op.execute("UPDATE users SET hashed_password = '!passwordless' WHERE hashed_password IS NULL")
    op.alter_column('users', 'hashed_password', existing_type=sa.String(length=255), nullable=False)
    op.drop_column('users', 'magic_nonce')
    op.drop_column('users', 'marketing_opt_in_at')
    op.drop_column('users', 'marketing_opt_in')
