from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.config import settings


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)


def gen_uuid() -> str:
    return str(uuid4())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[UUID] = _uuid_pk()
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(default=0)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    model_used: Mapped[str] = mapped_column(String(50), default="dummy")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_uuid)
    document_id: Mapped[str] = mapped_column(String, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))
    chunk_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class Account(Base):
    """Il conto del cliente: la fila a cui ogni movimento fa riferimento.

    `balance` e' denormalizzato di proposito, e il ragionamento e' scritto qui perche'
    non resti una scelta anonima: il saldo e' una copia di `SUM(movements.amount)`
    tenuta accanto ai dati, non la verta'. La verta' sono i movimenti, che si
    possono rileggere e ricalcolare quando vuoi (`scripts/day3_smoke.py` lo fa e
    confronta). La copia esiste perche' il saldo e' la domanda che lo sportello
    fa a ogni istante e non puo' aspettare una `SUM` su tre mesi di righe.

    Il prezzo di questa scelta e' che saldo e movimenti possono divergere se
    qualcuno scrive da due posti diversi. Lo si paga solo tenendo i due writer
    nello stesso servizio e nello stesso `commit`: per questo `MovementService`
    scrive entrambi e committa una volta sola.

    L'invariante che regge tutto e' uno solo:

        balance == SUM(movements.amount)   per ogni conto

    Vale perche' un conto apre a 0.00 e ogni variazione del saldo passa da
    `MovementService.record`. Un conto aperto con `balance=100.00` senza i suoi
    cento euro in tabella lo viola, e `MovementService.reconcile` lo dice subito.
    La copia, insomma, non e' libera: e' vincolata alla verta' da costruzione, e il
    vincolo si puo' controllare con una query e non con la fiducia.
    """

    __tablename__ = "accounts"

    id: Mapped[UUID] = _uuid_pk()
    holder: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    iban: Mapped[str] = mapped_column(String(34), nullable=False, unique=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    opened_on: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default=text("0")
    )

    movements: Mapped[list["Movement"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Movement.occurred_on",
    )


class Movement(Base):
    """Una riga di estratto conto, con il segno gia' dentro.

    `amount` e' con segno: negativo e' un'uscita, positivo un'entrata. Non e' una
    pigna e' la convenzione del partito doppio, ed e' quello che permette a
    `SUM(amount)` di essere il saldo senza un `CASE` per ogni riga.

    Il contratto di import del Giorno 2 accetta solo importi positivi
    (`MovementRow.amount: float = Field(gt=0)`): quel file porta l'entita' di un
    movimento ma non il suo verso, quindi non e' la stessa cosa di questa colonna.
    Chi trasformerà una riga CSV in un movimento dovrà prendere il verso da un
    campo che quel formato non ha. E' un buco noto, non un dettaglio aggirato:
    l'import per ora valida e basta, non scrive.

    `ck_movements_amount_not_zero` non e' pignoleria: un movimento da 0.00 non
    cambia niente e occupa una riga per sempre, e il vincolo serve anche al test
    che dimostra l'atomicita' di Giorno 3.
    """

    __tablename__ = "movements"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_movements_amount_not_zero"),
        # Le due query del giorno sono "movimenti di questo conto" e "movimenti di
        # questo conto in un intervallo di date": entrambe filtrano su
        # (account_id, occurred_on). Senza questo indice Postgres le risolve con
        # una seq scan e il tempo cresce con le righe totali del table.
        Index("ix_movements_account_occurred_on", "account_id", "occurred_on"),
    )

    id: Mapped[UUID] = _uuid_pk()
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped[Account] = relationship(back_populates="movements")
