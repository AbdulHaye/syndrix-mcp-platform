"""embedding_text_to_vector

Revision ID: d0df74ae4dc4
Revises: 107e90c007cf
Create Date: 2026-05-05 17:03:58.550876

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import os

try:
    import pgvector.sqlalchemy  # type: ignore[import]
    _VECTOR_AVAILABLE = os.environ.get("PGVECTOR_ENABLED", "true").lower() != "false"
except ImportError:
    _VECTOR_AVAILABLE = False


# revision identifiers, used by Alembic.
revision: str = 'd0df74ae4dc4'
down_revision: Union[str, None] = '107e90c007cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only applicable when the pgvector extension is installed in PostgreSQL.
    # Skipped when PGVECTOR_ENABLED=false or the extension is absent.
    if not _VECTOR_AVAILABLE:
        return
    op.alter_column('knowledge_documents', 'embedding',
               existing_type=sa.TEXT(),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               existing_nullable=True)


def downgrade() -> None:
    if not _VECTOR_AVAILABLE:
        return
    op.alter_column('knowledge_documents', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               type_=sa.TEXT(),
               existing_nullable=True)
