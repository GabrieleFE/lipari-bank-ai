"""Regola a parole chiave: la categoria si decide senza modello.

Questo e' il punto del giorno. La risposta che il chiamante si aspetta e' un
contratto, non un modello: la regola e' rozza, ma produce esattamente la stessa
`CategorizeResponse` del provider LLM. Quando il modello arriva cambia una riga di
configurazione, non un contratto ne' un test.

Le parole sono in due livelli perche' non valgono tutte lo stesso:

* `specific` - nomi propri ("enel", "esselunga"): quasi un'etichetta con dentro.
* `generic`  - concetti ("bolletta", "supermercato"): dicono la categoria, ma anche
  altre cose dicono "bolletta".

La confidenza non e' un numero magico e' la somma dei pesi di cio' che e' stato
trovato, quindi ogni risposta sa spiegarsi e l'operatore puo' correggere la parola
chiave invece di fidarsi o di non fidarsi.
"""

import unicodedata
from dataclasses import dataclass, field

from src.types.categorize import CategorizeRequest, CategorizeResponse, CategoryEnum

BASE_SPECIFIC = 0.70
BASE_GENERIC = 0.40
STEP = 0.10
CAP_WITH_SPECIFIC = 0.95
CAP_GENERIC_ONLY = 0.85

# Una singola parola riconosciuta basta per *nominare* una categoria, non per
# fidarsi: se il punteggio e' sotto questa soglia la riga resta OTHER. La soglia
# coincide con il peso di una parola generica, quindi la regola e' una riga e non
# un parametro da tarare: quello che dice "quanto sono sicuro" e' `confidence`.
MIN_CONFIDENCE = 0.40
FALLBACK_CONFIDENCE = 0.10

SUBCATEGORY: dict[CategoryEnum, str] = {
    "UTILITIES": "BILANCE_E_SERVIZI",
    "GROCERIES": "SUPERMERCATO",
    "TRANSPORT": "TRASPORTO",
    "RESTAURANTS": "RISTORAZIONE",
    "ENTERTAINMENT": "INTRATTENIMENTO",
    "OTHER": "NON_CLASSIFICATO",
}


@dataclass(frozen=True, slots=True)
class Rule:
    """Le parole chiave di una categoria, separate per quanto sono specifiche."""

    category: CategoryEnum
    specific: tuple[str, ...] = field(default_factory=tuple)
    generic: tuple[str, ...] = field(default_factory=tuple)


RULES: tuple[Rule, ...] = (
    Rule(
        "UTILITIES",
        specific=(
            "enel",
            "illumino",
            "acea",
            "a2a",
            "iliad",
            "telecom italia",
            "windtre",
            "fastweb",
            "hera",
            "deco",
            "acquirente",
        ),
        generic=(
            "energia",
            "gas",
            "acqua",
            "fognatura",
            "luce",
            "telefonica",
            "telefono",
            "fibra",
            "bolletta",
            "canone",
            "tari",
            "imposta di soggiorno",
            "comune di",
        ),
    ),
    Rule(
        "GROCERIES",
        specific=(
            "esselunga",
            "conad",
            "coop",
            "carrefour",
            "eurospin",
            "famila",
            "pam",
            "aldi",
            "lidl",
            "spar",
            "banana di camerino",
            "macelleria",
            "panificio",
        ),
        generic=("supermercato", "supermercati", "alimentari", "grocery", "mercato"),
    ),
    Rule(
        "TRANSPORT",
        specific=(
            "trenitalia",
            "italotreno",
            "frecciarossa",
            "frecciabianca",
            "italia vapore",
            "easyjet",
            "ryanair",
            "wizz air",
            "enil",
            "eni",
            "q8",
            "totalerg",
            "uber",
            "free now",
            "taxi",
        ),
        generic=(
            "carburante",
            "distributore",
            "benzina",
            "gasolio",
            "parcheggio",
            "parking",
            "autobus",
            "autostrada",
            "pedaggio",
            "treno",
            "biglietto",
            "volo",
            "aereo",
            "car sharing",
            "noleggio",
        ),
    ),
    Rule(
        "RESTAURANTS",
        specific=(
            "mcdonald",
            "burger king",
            "starbucks",
            "kfc",
            "poke",
            "sushi",
            "pizzeria",
            "trattoria",
            "ristorante",
            "paninoteca",
            "pasticceria",
            "rosticceria",
        ),
        generic=("bar", "cafe", "caffè", "pranzo", "cena", "colazione", "catering", "menu"),
    ),
    Rule(
        "ENTERTAINMENT",
        specific=("netflix", "spotify", "disney", "amazon prime", "hbo", "sky", "cinema"),
        generic=(
            "multisala",
            "teatro",
            "concert",
            "streaming",
            "abbonamento",
            "palestra",
            "libreria",
            "videogioco",
            "evento",
            "biglietto",
        ),
    ),
)


def _normalize(text: str) -> str:
    """Minuscole e senza accenti: `Caffè` e `CAFFE'` devono cadere sulla stessa regola."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _contains(text: str, keyword: str) -> bool:
    """Parola intera, non sottostringa: `gas` non deve accendersi su `gasolio`.

    Il confronto e' fatto sul testo circondato da spazi, cosi' basta cercare
    `' keyword '` dentro: regge anche le parole chiave con piu' parole interne.
    """
    return f" {_normalize(keyword)} " in f" {text} "


@dataclass(frozen=True, slots=True)
class Match:
    category: CategoryEnum
    specific: tuple[str, ...]
    generic: tuple[str, ...]

    @property
    def score(self) -> float:
        """Confidenza = quanto pesano le parole trovate, non quante ne sono.

        Una sola parola specifica vale piu' di tre generiche: `netflix` e' un
        abbonamento di streaming, `abbonamento` da solo potrebbe essere la palestra.
        """
        if not self.specific and not self.generic:
            return 0.0
        base = BASE_SPECIFIC if self.specific else BASE_GENERIC
        extra = max(len(self.specific) + len(self.generic) - 1, 0) * STEP
        cap = CAP_WITH_SPECIFIC if self.specific else CAP_GENERIC_ONLY
        return min(cap, base + extra)


class KeywordCategorizeService:
    """Implementazione di `CategorizeService` a parole chiave, senza rete.

    Stessa firma del provider LLM, quindi i due sono intercambiabili dietro lo
    stesso `Depends`. Il contratto che il chiamante vede non cambia quando si
    cambia questo oggetto: e' il punto dell'esercizio.
    """

    def __init__(self, rules: tuple[Rule, ...] = RULES) -> None:
        self._rules = rules

    async def categorize(self, req: CategorizeRequest) -> CategorizeResponse:
        return self.classify(req.description)

    def classify(self, description: str) -> CategorizeResponse:
        text = _normalize(description)
        matches = [self._match(rule, text) for rule in self._rules]
        best = max(matches, key=lambda match: match.score, default=None)
        if best is None or best.score < MIN_CONFIDENCE:
            return CategorizeResponse(
                category="OTHER",
                subcategory=SUBCATEGORY["OTHER"],
                confidence=FALLBACK_CONFIDENCE,
                reasoning=(
                    "Nessuna parola chiave delle regole combacia: la riga resta OTHER e "
                    "va verificata a mano."
                ),
            )
        found = best.specific + best.generic
        return CategorizeResponse(
            category=best.category,
            subcategory=SUBCATEGORY[best.category],
            confidence=round(best.score, 2),
            reasoning=(
                f"Regola a parole chiave: trovate {', '.join(found)} "
                f"({len(best.specific)} specifiche, {len(best.generic)} generiche)."
            ),
        )

    def _match(self, rule: Rule, text: str) -> Match:
        return Match(
            category=rule.category,
            specific=tuple(k for k in rule.specific if _contains(text, k)),
            generic=tuple(k for k in rule.generic if _contains(text, k)),
        )
