"""add podio chat sessions

Revision ID: f4a7b9c1d2e3
Revises: a1b2c3d4e5f6
Create Date: 2026-07-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f4a7b9c1d2e3'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'podio_chat_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_key', sa.String(length=256), nullable=False),
        sa.Column('team_name', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('messages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_podio_chat_sessions_owner_key', 'podio_chat_sessions', ['owner_key']
    )


def downgrade() -> None:
    op.drop_index('ix_podio_chat_sessions_owner_key', table_name='podio_chat_sessions')
    op.drop_table('podio_chat_sessions')
