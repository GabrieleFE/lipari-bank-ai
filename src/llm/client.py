"""Contract dell'abstraction layer LLM: l'unico formato che il codice applicativo conosce."""

from collections.abc import AsyncIterator
from typing import Protocol

from src.llm.types import LLMResponse, Message, StreamChunk

__all__ = ["LLMProvider", "LLMResponse", "Message", "StreamChunk"]


class LLMProvider(Protocol):
    """Tipizzazione strutturale: chiunque abbia `model` + `complete`
    (+ `complete_stream`) e' un provider.

    Non esiste una classe base: le implementazioni non importano questo Protocol.
    """

    model: str

    async def complete(self, messages: list[Message], max_tokens: int = 500) -> LLMResponse: ...

    def complete_stream(
        self, messages: list[Message], max_tokens: int = 500
    ) -> AsyncIterator[StreamChunk]: ...
