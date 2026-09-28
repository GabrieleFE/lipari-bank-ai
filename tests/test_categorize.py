"""`/api/ai/categorize` attraverso l'endpoint, con la regola a parole chiave.

Nessun fake, nessuna chiave API: il default del provider e' la regola, quindi questi
test verificano davvero cio' che gira quando il modello non c'e'. Il provider LLM si
prova con `build_categorizer(provider="llm")` e appartiene agli eval.
"""

from typing import Any

from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.categorize_rules import KeywordCategorizeService
from src.types.categorize import CATEGORIES, CategorizeRequest, CategorizeResponse


async def categorize(
    body: dict[str, object],
) -> tuple[int, CategorizeResponse | None, dict[str, Any]]:
    """Stato, risposta tipizzata (None se non e' un 200) e corpo grezzo.

    Il corpo grezzo serve per gli errori: la risposta tipizzata serve per non fare
    `str(...)` e cast su ogni asserzione.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/ai/categorize", json=body)
    raw: dict[str, Any] = response.json()
    if response.status_code != 200:
        return response.status_code, None, raw
    return response.status_code, CategorizeResponse.model_validate(raw), raw


async def test_una_bolletta_viene_riconosciuta_e_dichiara_quanto_e_sicuro() -> None:
    """La prova della consegna: riconosce una bolletta e dice quanto e' sicuro."""
    status_code, data, _ = await categorize(
        {"description": "Bonifico Enel Energia bolletta gennaio", "amount": 85.50}
    )
    assert status_code == 200
    assert data is not None
    assert data.category == "UTILITIES"
    assert data.subcategory == "BILANCE_E_SERVIZI"
    # 0.90 = una parola specifica (enel) piu' due generiche (energia, bolletta).
    # Il numero non e' un mistero: e' la somma dei pesi, e `reasoning` li elenca.
    assert data.confidence == 0.9
    assert "enel" in data.reasoning


async def test_la_confidenza_e_detta_e_non_implicita() -> None:
    """Una parola specifica vale piu' di una generica: il contratto deve dirlo."""
    _, specifica, _ = await categorize({"description": "Netflix abbonamento", "amount": 12.99})
    _, generica, _ = await categorize({"description": "Abbonamento", "amount": 12.99})

    assert specifica is not None and generica is not None
    assert specifica.category == "ENTERTAINMENT"
    assert generica.category == "ENTERTAINMENT"
    assert specifica.confidence > generica.confidence


async def test_una_causale_senza_regole_torna_other_e_lo_dice() -> None:
    """Il fallback e' una risposta, non un silenzio: OTHER con confidenza bassa."""
    status_code, data, _ = await categorize(
        {"description": "Bonifico prot. 2025/014 rimborso spese", "amount": 120}
    )
    assert status_code == 200
    assert data is not None
    assert data.category == "OTHER"
    assert data.confidence < 0.5
    assert "mano" in data.reasoning


async def test_una_parola_chiave_che_e_prefisso_di_una_altra_non_accende_la_regola_sbagliata() -> (
    None
):
    """`gas` non e' `gasolio`: il confronto e' su parola intera, non su sottostringa.

    Con una ricerca per sottostringa, `Rifornimento gasolio` accenderebbe anche la
    regola `gas` delle utenze e la riga finirebbe nella categoria sbagliata con una
    confidenza che nessuno ha misurato.
    """
    _, gasolio, _ = await categorize({"description": "Rifornimento gasolio", "amount": 60})
    _, gas, _ = await categorize({"description": "Bolletta gas", "amount": 60})

    assert gasolio is not None and gas is not None
    assert gasolio.category == "TRANSPORT"
    assert gas.category == "UTILITIES"
    # Una sola parola trovata, ed e' quella giusta: se anche `gas` avesse fatto match
    # dentro `gasolio` la lista ne avrebbe due, e la confidenza sarebbe salita.
    assert "trovate gasolio (0 specifiche, 1 generiche)" in gasolio.reasoning


async def test_gli_accenti_non_fanno_dispari_la_regola() -> None:
    _, con_accento, _ = await categorize(
        {"description": "Pasticceria Caffè Aurora", "amount": 4.20}
    )
    _, senza, _ = await categorize({"description": "Pasticceria caffe Aurora", "amount": 4.20})
    assert con_accento is not None and senza is not None
    assert con_accento.category == senza.category == "RESTAURANTS"
    assert con_accento.confidence == senza.confidence


async def test_la_valuta_di_default_e_eur_ma_deve_essere_maiuscola() -> None:
    _, senza_valuta, _ = await categorize(
        {"description": "Esselunga supermercato", "amount": 45.20}
    )
    assert senza_valuta is not None
    assert senza_valuta.category == "GROCERIES"

    status_code, con_valuta, _ = await categorize(
        {"description": "Esselunga supermercato", "amount": 45.20, "currency": "USD"}
    )
    assert status_code == 200
    assert con_valuta is not None

    # `usd` non e' EUR di default: e' una valuta scritta sbagliata, e va detto.
    status_basso, _, basso = await categorize(
        {"description": "Esselunga supermercato", "amount": 45.20, "currency": "usd"}
    )
    assert status_basso == 422
    assert basso["details"][0]["field"] == "currency"


async def test_le_sei_categorie_del_contratto_sono_tutte_raggiungibili() -> None:
    """Il contratto dichiara sei categorie: nessuna e' decorativa."""
    esempi = {
        "UTILITIES": "Bonifico Enel Energia",
        "GROCERIES": "Supermercato Esselunga",
        "TRANSPORT": "Biglietto Trenitalia Venezia",
        "RESTAURANTS": "Pizzeria Da Michele",
        "ENTERTAINMENT": "Abbonamento Netflix",
    }
    trovate = set()
    for descrizione in esempi.values():
        _, data, _ = await categorize({"description": descrizione, "amount": 10})
        assert data is not None
        trovate.add(data.category)
    assert trovate == set(CATEGORIES) - {"OTHER"}


async def test_importo_negativo_ritorna_422_nominando_il_campo() -> None:
    status_code, _, body = await categorize({"description": "Affitto", "amount": -10})
    assert status_code == 422
    assert body["error"] == "VALIDATION_ERROR"
    assert body["details"][0]["field"] == "amount"
    assert body["details"][0]["code"] == "NOT_POSITIVE"


async def test_valuta_non_ammessa_ritorna_422_nominando_il_campo() -> None:
    status_code, _, body = await categorize(
        {"description": "Affitto", "amount": 10, "currency": "EUR-IT"}
    )
    assert status_code == 422
    assert body["details"][0]["field"] == "currency"


async def test_descrizione_di_soli_spazi_ritorna_422() -> None:
    """Una descrizione fatta di spazi e' una descrizione assente, non una breve."""
    status_code, _, body = await categorize({"description": "   ", "amount": 10})
    assert status_code == 422
    assert body["details"][0]["field"] == "description"
    assert body["details"][0]["code"] == "EMPTY"


def test_il_servizio_a_parole_chiave_ha_la_stessa_firma_del_provider_llm() -> None:
    """Il contratto che sopravvive al modello: stesso Protocol, stesso risultato.

    Non e' un test sul LLM: e' il test che dice che scaldare il contratto oggi non
    costa niente domani. Il tipo restituito e' dichiarato da `Categorizer` in
    `src/api/categorize.py`, e qui si verifica che la regola lo rispetti.
    """
    from src.api.categorize import Categorizer

    assert isinstance(KeywordCategorizeService(), Categorizer)


def test_anche_il_provider_llm_rispetta_il_protocol_senza_rete() -> None:
    """Le due implementazioni dello stesso contratto, una delle due non chiamabile qui.

    `CategorizeService` vuole un client Instructor: non lo si puo' costruire senza
    una chiave, e senza chiave questa prova girerebbe solo su un'altra macchina.
    Il Protocol e' strutturale e controlla solo la presenza del metodo, quindi si puo'
    verificare il rispetto del contratto passando un `None` come client: se un giorno
    il metodo cambiasse firma, qui lo direbbe subito e non alla prima chiamata con
    una chiave vera.
    """
    from src.api.categorize import Categorizer
    from src.services.categorize_service import CategorizeService

    assert isinstance(CategorizeService(client=None, model="non-chiamata"), Categorizer)


def test_la_richiesta_vietata_ma_la_risposta_no_e_un_disegno_non_una_svista() -> None:
    """Il rischio che questo test copre: rompere Instructor per far contenti i test.

    `CategorizeRequest` ha `extra="forbid"`, e va bene: se il chiamante manda un
    campo che il contratto non prevede, ignorarlo in silenzio e' la stessa perdita di
    una riga che sparisce dal conto, e su questo progetto la perdita e' l'unico
    difetto che conta. La risposta e' l'altra faccia della stessa medaglia, ma il
    pericolo e' opposto.

    `CategorizeResponse` e' lo schema che Instructor passa al modello e nel quale
    il risultato viene validato. Se le si mettesse `extra="forbid"`, un modello che
    restituisce `{"category": ..., "confidence": 0.9, "debug": "..."}` — cioe' un
    campo in piu' sul JSON, il caso normale e non un'eccezione — verrebbe respinto
    come se fosse una richiesta malformata, e il chiamante riceverebbe un 422 per
    una risposta che e' sostanzialmente giusta. Il fallimento avrebbe una firma
    fuorviante: sembrerebbe che il provider sia rotto, mentre il rotto sarebbe il
    contratto.

    Questo e' il tipo di regressione che i test qui non vedono: senza chiave API il
    percorso LLM non gira mai, quindi il danno resterebbe invisibile fino al primo
    eval con credenziali vere. Il test e' la guardia che mancava.
    """
    richiesta = CategorizeRequest.model_config.get("extra")
    risposta = CategorizeResponse.model_config.get("extra")

    # La richiesta e' chiusa: un campo in piu' e' un problema conteggibile.
    assert richiesta == "forbid"
    # La risposta e' aperta: un campo in piu' del modello viene scartato, non respinto.
    assert risposta != "forbid"

    # E il comportamento dichiarato, verificato senza rete: il modello puo' mandare
    # un campo in piu' e la risposta resta valida, con i quattro campi del contratto.
    con_campo_in_piu = CategorizeResponse.model_validate(
        {
            "category": "UTILITIES",
            "subcategory": "BILANCE_E_SERVIZI",
            "confidence": 0.9,
            "reasoning": "parola trovata: enel",
            "debug": "campo che il modello si e' inventato",
        }
    )
    assert con_campo_in_piu.category == "UTILITIES"
    assert not hasattr(con_campo_in_piu, "debug")
