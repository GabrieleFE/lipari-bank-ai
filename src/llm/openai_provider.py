"""Provider OpenAI: traduce Message<->formato OpenAI e calcola il costo reale (input vs output)."""

from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from src.config import settings
from src.llm.client import LLMResponse, Message, StreamChunk
from src.llm.retry import llm_retry


class OpenAIProvider:
    PRICING: dict[str, tuple[float, float]] = {  # EUR per 1k token (input, output)
        "gpt-4o-mini": (0.00014, 0.00056),
        "gpt-4o": (0.0023, 0.0091),
    }

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # retry con backoff gestito da tenacity (vedi llm/retry.py)
        )
        self.model = model

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        input_price, output_price = self.PRICING[self.model]
        return (input_tokens * input_price + output_tokens * output_price) / 1000

    @staticmethod
    def _to_openai_messages(messages: list[Message]) -> list[ChatCompletionMessageParam]:
        openai_messages: list[ChatCompletionMessageParam] = []
        for m in messages:
            if m.role == "system":
                openai_messages.append({"role": "system", "content": m.content})
            elif m.role == "user":
                openai_messages.append({"role": "user", "content": m.content})
            else:
                openai_messages.append({"role": "assistant", "content": m.content})
        return openai_messages

    @llm_retry(openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)
    async def complete(self, messages: list[Message], max_tokens: int = 500) -> LLMResponse:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            max_tokens=max_tokens,
            temperature=0.3,
        )
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        return LLMResponse(
            content=response.choices[0].message.content or "",
            tokens_used=input_tokens + output_tokens,
            cost_eur=self._cost(input_tokens, output_tokens),
            model=self.model,
        )

    async def complete_stream(
        self, messages: list[Message], max_tokens: int = 500
    ) -> AsyncIterator[StreamChunk]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
            stream_options={"include_usage": True},
        )
        input_tokens = output_tokens = 0
        async for chunk in stream:
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(text=chunk.choices[0].delta.content)
        yield StreamChunk(
            text="",
            tokens_used=input_tokens + output_tokens,
            cost_eur=self._cost(input_tokens, output_tokens),
            model=self.model,
        )
