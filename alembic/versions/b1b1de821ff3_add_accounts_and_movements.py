"""add accounts and movements

Revision ID: b1b1de821ff3
Revises: c4d1e6a8b9f2
Create Date: 2026-09-28 11:45:56.154011

Questa migration e' il primo caso in cui `--autogenerate` ha prodotto piu' di
quello che gli avevo chiesto. Oltre alle due tabelle nuove ha proposed:

    op.alter_column('document_chunks', 'chunk_metadata', nullable=False)
    op.drop_index('ix_document_chunks_embedding', ...)   # postgresql_using='hnsw'

Entrambe riguardano `document_chunks`, che non c'entra niente con i conti: sono
residui di un disallineamento gia' presente tra il database e `Base.metadata`.

La seconda e' pericolosa sul serio. `ix_document_chunks_embedding` non e' mai
stato dichiarato nell'ORM: e' un indice HNSW scritto a mano nella migration
c4d1e6a8b9f2 (pgvector non e' representabile come `Index(...)` con la classe di
operatori per il coseno). Perche' l'ORM non lo vede, Alembic lo deduce "indice
che esiste nel DB e non nell'ORM" e lo marca come da eliminare. Applicando la
migration cosi' com'era, il retrieval di `/api/ai/advice` sarebbe passato da
indice a seq scan: funzionerebbe lo stesso, un po' piu' lento, e nessun test
sarebbe fallito. Sono due righe da togliere a mano e una lezione che vale piu'
delle due righe.

Quindi qui sotto ci sono solo le due tabelle del Giorno 3. `document_chunks` non
si tocca e il suo allineamento con l'ORM resta un debito aperto, dichiarato in
docs/ai-review/G3.md invece che risolto di nascosto qui dentro.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1b1de821ff3'
down_revision: Union[str, Sequence[str], None] = 'c4d1e6a8b9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crea il conto e i suoi movimenti, piu' l'indice che le due query del giorno richiedono."""
    op.create_table('accounts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('holder', sa.String(length=120), nullable=False),
    sa.Column('iban', sa.String(length=34), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('opened_on', sa.Date(), nullable=False),
    sa.Column('balance', sa.Numeric(precision=14, scale=2), server_default=sa.text('0'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('iban')
    )
    op.create_index(op.f('ix_accounts_holder'), 'accounts', ['holder'], unique=False)
    op.create_table('movements',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('account_id', sa.UUID(), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount <> 0', name='ck_movements_amount_not_zero'),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_movements_account_id'), 'movements', ['account_id'], unique=False)
    # Le due query del giorno filtrano sempre per (account_id, occurred_on).
    op.create_index('ix_movements_account_occurred_on', 'movements', ['account_id', 'occurred_on'], unique=False)


def downgrade() -> None:
    """Rimuove conti e movimenti. La direction e' l'opposto esatto di upgrade()."""
    op.drop_index('ix_movements_account_occurred_on', table_name='movements')
    op.drop_index(op.f('ix_movements_account_id'), table_name='movements')
    op.drop_table('movements')
    op.drop_index(op.f('ix_accounts_holder'), table_name='accounts')
    op.drop_table('accounts')
