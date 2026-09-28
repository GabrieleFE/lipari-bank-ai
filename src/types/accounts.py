"""I contratti di conti e movimenti.

La lezione del giorno ha una domanda che sembra di stile e invece e' di compatibilita':
un `Decimal` di Python, serializzato in JSON, che cosa diventa? Risposta: una
**stringa**, perche' Pydantic non sa cosa sia un numero decimale e non vuole
inventarsi una perdita. Quindi `"amount": "1234.56"`, non `"amount": 1234.56`.

E il punto non e' la pedanteria. Un numero JSON non ha decimali: da come e' scritto
in `1234.56` al doppio in virgola mobile che ci finisce dentro c'e' un passaggio, e
un passaggio e' dove il centesimo si perde. La lezione lo dice esattamente: se il
totale torna dal database in `float`, il centesimo che hai appena vinto lo perdi in
quel passaggio. La colonna e' `NUMERIC` e la `SUM` e' `Decimal` proprio per non
perderlo, e rimandare il danno al JSON lo butterebbe via tutto.

Percio' qui il denaro e' `Decimal` in Python e stringa in JSON, e ogni cifra
arriva al chiamante esatta. Se un domani serve un `number`, e' una decisione da
prendere sapendo che si reintroduce il `float`: si puo' fare, ma e' una scelta e va
detta, non un default silenzioso.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MovementCreateRequest(BaseModel):
    """Il corpo della richiesta che scrive. `amount` con segno: negativo e' un'uscita.

    `account_id` non c'e' qui dentro: e' gia' nell'URL, e chiederlo anche nel corpo
    significa che il chiamante lo manda due volte in due posti che possono
    contraddirsi. "La rotta nomina il conto, il corpo nomina il movimento."

    `amount != 0` e' dichiarato qui e non solo come `CHECK` nel database perche' una
    richiesta sbagliata si rifiuta con un 422 e un campo che spiega quale, mentre il
    vincolo del database resta la rete che non si puo' aggirare. Il servizio non si
    fida del primo: il test di atomicita' fa fallire proprio questo.

    Il formato e' il punto in cui il Giorno 2 e il Giorno 3 si toccano, e qui si
    toccano davvero: `MovementRow` del CSV accetta solo importi positivi perche' quel
    file non porta il verso del movimento. Questo endpoint accetta il segno perche'
    e' un'operazione singola e il verso si sa. Sono due contratti diversi e non
    vanno fusi perche' l'uno e' piu' facile da soddisfare dell'altro.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    occurred_on: date
    description: str = Field(..., min_length=1, max_length=200)
    amount: Decimal = Field(..., decimal_places=2, max_digits=14)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")

    @field_validator("amount")
    @classmethod
    def amount_must_not_be_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError(
                "un movimento da 0.00 non cambia il conto e occupa una riga per sempre"
            )
        return value


class BalanceView(BaseModel):
    """Il conto con il suo saldo. Il saldo arriva come stringa per i motivi sopra."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    holder: str
    iban: str
    currency: str
    opened_on: date
    balance: Decimal


class AccountSummaryList(BaseModel):
    accounts: list[BalanceView]


class MovementView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    occurred_on: date
    description: str
    amount: Decimal
    currency: str


class MovementList(BaseModel):
    """Query 1 del giorno: i movimenti del conto con l'intestatario.

    `holder` e `iban` vengono fuori dallo stesso JOIN che porta i movimenti, non
    da una seconda interrogazione. E' la risposta alla domanda "ma serve fare un
    `get` per ogni movimento?": no, e il numero di query resta due qualunque sia
    il numero di righe.
    """

    account_id: UUID
    holder: str
    iban: str
    count: int = Field(..., ge=0)
    movements: list[MovementView]


class MonthlyBucket(BaseModel):
    """Un mese del GROUP BY. Le righe crescono coi mesi, non coi movimenti."""

    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    movement_count: int = Field(..., ge=0)
    total_out: Decimal


class AccountSummary(BaseModel):
    """Query 2 e 3 del giorno: il periodo in una riga, e il dettaglio per mese.

    `total_out` e' la somma delle sole uscite, che e' la domanda che lo sportello
    fa davvero ("quanto ho speso nel trimestre"), e non il segno della somma. Il
    `net` c'e' per il conto intero. Sono due numeri diversi e tenerli separati
    evita di dover ricordare a chi legge che il totale delle spese e' negativo.
    """

    account_id: UUID
    holder: str
    iban: str
    currency: str
    date_from: date
    date_to: date
    movement_count: int = Field(..., ge=0)
    total_in: Decimal
    total_out: Decimal
    net: Decimal
    balance: Decimal
    monthly: list[MonthlyBucket]
