"""Modelli dati dell'abstraction layer LLM (allineati alla procedura del bootcamp)."""

from typing import Literal

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LLMResponse(BaseModel):
    """La risposta del modello piu' la sua contabilita': i token, il costo e il modello.

    Nessun percorso nel codice puo' chiamare un LLM e dimenticarsi di sapere quanto e' costato.
    """

    content: str
    tokens_used: int
    cost_eur: float
    model: str


class StreamChunk(BaseModel):
    """Pezzo di stream. Solo l'ultimo chunk porta la contabilita' (tokens, cost, model)."""

    text: str
    tokens_used: int = 0
    cost_eur: float = 0.0
    model: str = ""
