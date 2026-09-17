"""Eval tests (Giorno 6): girano solo con `-m eval` — chiamano modelli veri, costano.

Cadenza: NON a ogni commit (gli unit test sono gratis e girano sempre). Gli eval
chiamano un LLM, quindi sono lenti, costosi e possono oscillare fra due esecuzioni
identiche. Il marker li tiene nella stessa suite ma li attiva su richiesta.
"""

from pathlib import Path

import pytest

from src.eval.runners import eval_categorize, eval_rag

pytestmark = pytest.mark.eval

DATASET_DIR = Path(__file__).resolve().parents[2] / "src" / "eval" / "datasets"


@pytest.mark.asyncio
async def test_categorize_accuracy_threshold() -> None:
    result = await eval_categorize(DATASET_DIR / "categorize_golden.jsonl")
    assert result["accuracy"] >= 0.80, (
        f"Accuracy regression: {result['accuracy']:.1%} (failures: {result['failures']})"
    )
    # Il secondo assert e' quello che si dimentica: la latenza. Un prompt che
    # migliora di 2 punti e raddoppia il tempo di risposta e' uno SCAMBIO, non
    # un miglioramento — senza questa riga lo scambio non lo vede nessuno.
    assert result["avg_latency_s"] < 2.0


@pytest.mark.asyncio
async def test_rag_recall_5() -> None:
    result = await eval_rag(DATASET_DIR / "rag_golden.jsonl", top_k=5)
    assert result["recall_at_5"] >= 0.70
    assert result["recall_at_1"] >= 0.50  # top-1 piu' stringente
