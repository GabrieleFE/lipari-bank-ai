"""Eval runners: misurano il sistema probabilistico su dataset golden.

Struttura invariata per ogni task: **carica -> cicla -> confronta -> aggrega -> decide**.
Cambia solo la metrica, non la forma.

Gli eval NON girano a ogni push: ogni esempio e' una chiamata al modello, quindi sono
lenti e costano. Si lanciano su richiesta (`uv run pytest -m eval`) e in CI con
cadenza settimanale (`.github/workflows/eval.yml`). Gli unit test restano a ogni commit.

Il pezzo piu' prezioso del risultato NON e' l'accuratezza: e' la lista dei `failures`
con input, atteso e ottenuto. Il numero dice CHE C'E' un problema; i casi falliti
dicono QUALE.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from src.api.categorize import get_categorize_service
from src.config import settings
from src.db.session import async_session_factory
from src.llm.client import LLMProvider, Message
from src.llm.embedding_client import EmbeddingClient
from src.llm.factory import get_llm_provider
from src.services.rag_service import RAGService
from src.services.retrieval_service import RetrievalService
from src.types.advice import AdviceRequest
from src.types.categorize import CategorizeRequest

DATASET_DIR = Path(__file__).resolve().parent / "datasets"


def _load_jsonl(dataset_path: Path) -> list[dict[str, Any]]:
    raw = dataset_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


async def eval_categorize(dataset_path: Path, model: str | None = None) -> dict[str, Any]:
    """Accuratezza della categorizzazione sull'intero golden dataset.

    Il confronto e' esatto (un valore di un elenco chiuso), quindi la metrica e'
    l'accuracy aggregata + i failures.

    Nota sul costo: CategorizeService non espone token/usage (ritorna solo
    CategorizeResponse), quindi qui il costo NON viene tracciato — meglio un dato
    mancante dichiarato che un numero inventato.
    """
    examples = await asyncio.to_thread(_load_jsonl, dataset_path)
    service = get_categorize_service(model=model)

    total_latency = 0.0
    correct = 0
    failures: list[dict[str, Any]] = []

    for ex in examples:
        start = time.perf_counter()
        try:
            result = await service.categorize(CategorizeRequest(**ex["input"]))
            is_correct = result.category == ex["expected"]["category"]
            total_latency += time.perf_counter() - start
        except Exception as exc:  # noqa: BLE001  errori provider = failure diagnostici
            failures.append(
                {
                    "input": ex["input"],
                    "expected": ex["expected"],
                    "actual": str(exc),
                    "error": type(exc).__name__,
                }
            )
            total_latency += time.perf_counter() - start
            continue

        if is_correct:
            correct += 1
        else:
            failures.append(
                {
                    "input": ex["input"],
                    "expected": ex["expected"],
                    "actual": result.model_dump(),
                }
            )

    n = len(examples)
    return {
        "accuracy": correct / n if n else 0.0,
        "n_examples": n,
        "n_failures": len(failures),
        "avg_latency_s": total_latency / n if n else 0.0,
        "model_used": model or settings.categorize_model,
        "failures": failures[:10],  # top 10: il pattern sta nei primi 10
    }


# ---------------------------------------------------------------------------
# Retrieval (RAG)
# ---------------------------------------------------------------------------


async def eval_rag(dataset_path: Path, top_k: int = 5) -> dict[str, Any]:
    """Recall@k e recall@1 per il retrieval.

    Non chiama NESSUN LLM: solo l'embedding locale della domanda (quasi gratis);
    e' l'eval che puoi permetterti di lanciare spesso.

    La coppia @k/@1 separa le diagnosi:
      - recall@k alto + @1 basso -> il chunk giusto c'e' ma in fondo: serve re-ranking;
      - recall@k basso -> il documento giusto non arriva mai al modello: toccare il
        prompt di generazione non servira'.
    """
    examples = await asyncio.to_thread(_load_jsonl, dataset_path)
    embedding_client = EmbeddingClient()
    total_latency = 0.0
    correct_at_k = 0
    correct_at_1 = 0

    async with async_session_factory() as session:
        retrieval = RetrievalService(session, embedding_client)
        for ex in examples:
            start = time.perf_counter()
            chunks = await retrieval.retrieve(ex["query"], top_k=top_k)
            total_latency += time.perf_counter() - start
            doc_ids = [c.document_id for c in chunks]
            if ex["expected_doc_id"] in doc_ids:
                correct_at_k += 1
            if doc_ids and doc_ids[0] == ex["expected_doc_id"]:
                correct_at_1 += 1

    n = len(examples)
    return {
        f"recall_at_{top_k}": correct_at_k / n if n else 0.0,
        "recall_at_1": correct_at_1 / n if n else 0.0,
        "top_k": top_k,
        "n_examples": n,
        "avg_latency_ms": (total_latency / n * 1000) if n else 0.0,
    }


# ---------------------------------------------------------------------------
# Generation (LLM-as-judge)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = (
    "Sei un valutatore di risposte di un assistente bancario AI. "
    "Rispondi SOLO con un JSON valido, senza testo aggiuntivo."
)

JUDGE_USER_PROMPT = """Valuta se la risposta dell'assistente AI è adeguata.

DOMANDA: {question}
CONTESTO FORNITO: {context}
RISPOSTA: {answer}

Criteri:
1. Faithfulness: la risposta è basata sul contesto (no allucinazioni)?
2. Relevance: la risposta affronta la domanda?
3. Completeness: tutti i punti rilevanti sono coperti?
4. Citation: cita correttamente il documento?

Output JSON (solo il JSON, niente testo):
{{
    "faithfulness": <0.0-1.0>,
    "relevance": <0.0-1.0>,
    "completeness": <0.0-1.0>,
    "citation_correct": <true/false>,
    "overall": <0.0-1.0>,
    "reasoning": "<breve motivazione>"
}}"""


async def llm_judge(
    question: str,
    context: str,
    answer: str,
    judge: LLMProvider,
) -> dict[str, Any]:
    """Valuta una risposta con un SECONDO modello (judge).

    Vincolo di design (Giorno 6): il judge deve essere un modello DIVERSO e piu'
    capace del generatore. Judge = generatore produce self-bias: approva cio' che
    somiglia a come genera lui stesso — e' un loop, non una verifica indipendente.
    """
    response = await judge.complete(
        messages=[
            Message(role="system", content=JUDGE_SYSTEM_PROMPT),
            Message(
                role="user",
                content=JUDGE_USER_PROMPT.format(question=question, context=context, answer=answer),
            ),
        ],
        max_tokens=300,
    )
    try:
        return cast(dict[str, Any], json.loads(response.content))
    except json.JSONDecodeError:
        return {
            "faithfulness": 0.0,
            "relevance": 0.0,
            "completeness": 0.0,
            "citation_correct": False,
            "overall": 0.0,
            "reasoning": f"Judge non ha prodotto JSON valido: {response.content[:200]!r}",
        }


async def eval_generation(dataset_path: Path) -> dict[str, Any]:
    """End-to-end sul RAG: retrieve -> genera -> giudica con un secondo modello.

    Misura la faithfulness (il vincolo 'rispondi ESCLUSIVAMENTE dal contesto' del
    Giorno 5) e l'overall, PIU' costo e latenza. E' l'eval piu' costoso dei tre:
    due chiamate LLM per esempio (generatore + judge).
    """
    examples = await asyncio.to_thread(_load_jsonl, dataset_path)

    llm = get_llm_provider()
    judge = get_llm_provider(settings.judge_model)
    embedding_client = EmbeddingClient()

    total_cost = 0.0
    total_latency = 0.0
    per_example: list[dict[str, Any]] = []

    async with async_session_factory() as session:
        retrieval = RetrievalService(session, embedding_client)
        rag = RAGService(retrieval, llm)

        for ex in examples:
            question = ex["input"]["question"]
            start = time.perf_counter()
            answer = await rag.answer(AdviceRequest(question=question))
            latency = time.perf_counter() - start
            total_cost += answer.cost_eur
            total_latency += latency

            context = "\n\n".join(c.excerpt for c in answer.citations) or "NESSUN CONTESTO"
            verdict = await llm_judge(question, context, answer.answer, judge)

            per_example.append(
                {
                    "question": question,
                    "expected_doc_id": ex["expected"]["doc_id"],
                    "answer": answer.answer,
                    "citations": [c.document_id for c in answer.citations],
                    "verdict": verdict,
                }
            )

    n = len(examples)
    overall = [e["verdict"]["overall"] for e in per_example]
    faithfulness = [e["verdict"]["faithfulness"] for e in per_example]
    return {
        "avg_overall": sum(overall) / n if n else 0.0,
        "avg_faithfulness": sum(faithfulness) / n if n else 0.0,
        "pass_rate": (sum(1 for v in overall if v >= 0.7) / n) if n else 0.0,
        "n_examples": n,
        "total_cost_eur": total_cost,
        "avg_latency_s": total_latency / n if n else 0.0,
        "model_generator": settings.default_model,
        "model_judge": settings.judge_model,
        "per_example": per_example,
    }


# ---------------------------------------------------------------------------
# Confronto modelli / prompt (A/B): eval = decide scelte business
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """Vista compatta per decidere: la tabella costo-qualita'-latenza per modello."""

    model_used: str
    accuracy: float
    total_cost_eur: float
    avg_latency_ms: float
    n_examples: int
    failures: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.model_used:<14} acc={self.accuracy:<6.1%} "
            f"cost=€{self.total_cost_eur:<6.2f} lat={self.avg_latency_ms:>6.0f}ms "
            f"fail={len(self.failures):>3}/{self.n_examples}"
        )


async def compare_models(
    dataset_path: Path,
    models: list[str],
    price_per_1k_output_eur: float = 0.0,
) -> list[EvalResult]:
    """Confronto leale fra modelli: STESSO dataset congelato, STESSO prompt.

    Si muove UNA variabile alla volta, altrimenti la differenza non e' attribuibile.
    Nota: il costo qui non e' misurabile da CategorizeService (vedi eval_categorize),
    quindi `total_cost_eur` resta 0.0 — il confronto dominante resta qualidade/latenza.
    """
    results: list[EvalResult] = []
    for model in models:
        r = await eval_categorize(dataset_path, model=model)
        results.append(
            EvalResult(
                model_used=r["model_used"],
                accuracy=r["accuracy"],
                total_cost_eur=price_per_1k_output_eur,  # stimato esterno, non misurato
                avg_latency_ms=r["avg_latency_s"] * 1000,
                n_examples=r["n_examples"],
                failures=r["failures"],
            )
        )
    return results


async def _main() -> None:
    """Gate CLI: `uv run python -m src.eval.runners`."""
    cat = await eval_categorize(DATASET_DIR / "categorize_golden.jsonl")
    print(json.dumps(cat, indent=2, ensure_ascii=False))

    categorize_threshold = 0.80
    if cat["accuracy"] < categorize_threshold:
        print(f"FAIL categorize: accuracy {cat['accuracy']:.1%} < {categorize_threshold:.0%}")
        raise SystemExit(1)
    print(f"OK   categorize: accuracy {cat['accuracy']:.1%}")

    rag = await eval_rag(DATASET_DIR / "rag_golden.jsonl")
    print(json.dumps(rag, indent=2, ensure_ascii=False))

    recall_threshold = 0.70
    if rag["recall_at_5"] < recall_threshold:
        print(f"FAIL rag: recall@5 {rag['recall_at_5']:.1%} < {recall_threshold:.0%}")
        raise SystemExit(1)
    print(f"OK   rag: recall@5 {rag['recall_at_5']:.1%}")


if __name__ == "__main__":
    asyncio.run(_main())
