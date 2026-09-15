"""Provider Anthropic: le tre divergenze con OpenAI vivono solo qui, dentro il provider."""

from collections.abc import AsyncIterator

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from src.config import settings
from src.llm.client import LLMResponse, Message, StreamChunk
from src.llm.retry import llm_retry


class AnthropicProvider:
    PRICING: dict[str, tuple[float, float]] = {  # EUR per 1k token (input, output)
        "claude-haiku-4-5-20251001": (0.000226, 0.001129),
        "claude-sonnet-4-6": (0.00271, 0.01355),
    }

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        self.client = AsyncAnthropic(
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
        self.model = model

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        input_price, output_price = self.PRICING[self.model]
        return (input_tokens * input_price + output_tokens * output_price) / 1000

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[MessageParam]]:
        system = next((m.content for m in messages if m.role == "system"), "")
        user_messages: list[MessageParam] = [
            {
                "role": "assistant" if m.role == "assistant" else "user",
                "content": m.content,
            }
            for m in messages
            if m.role != "system"
        ]
        return system, user_messages

    @llm_retry(anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError)
    async def complete(self, messages: list[Message], max_tokens: int = 500) -> LLMResponse:
        system, user_messages = self._split_system(messages)
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=user_messages,
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        content = "".join(b.text for b in response.content if b.type == "text")
        return LLMResponse(
            content=content,
            tokens_used=input_tokens + output_tokens,
            cost_eur=self._cost(input_tokens, output_tokens),
            model=self.model,
        )

    async def complete_stream(
        self, messages: list[Message], max_tokens: int = 500
    ) -> AsyncIterator[StreamChunk]:
        system, user_messages = self._split_system(messages)
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=user_messages,
        ) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(text=text)
            final = await stream.get_final_message()
            input_tokens = final.usage.input_tokens
            output_tokens = final.usage.output_tokens
            yield StreamChunk(
                text="",
                tokens_used=input_tokens + output_tokens,
                cost_eur=self._cost(input_tokens, output_tokens),
                model=self.model,
            )
