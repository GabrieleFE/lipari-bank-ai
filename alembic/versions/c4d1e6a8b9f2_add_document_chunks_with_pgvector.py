"""add document_chunks table with pgvector embedding

Revision ID: c4d1e6a8b9f2
Revises: b53cd3dbae33
Create Date: 2026-09-16 10:00:00.000000

Scritta a mano: CREATE EXTENSION vector non e' un modello, quindi
--autogenerate non lo propone. Deve stare prima della create_table:
il tipo vector non esiste finche' l'extension non e' attiva.

La dimensione (384) e' quella di EMBEDDING_DIM in src/config.py, scelta
Giorno 5 = embedding locale multilingual (paraphrase-multilingual-MiniLM-L12-v2).
Cambiare modello di embedding richiede una migration qui.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "c4d1e6a8b9f2"
down_revision: Union[str, Sequence[str], None] = "b53cd3dbae33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), nullable=False, index=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), default={}),
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")