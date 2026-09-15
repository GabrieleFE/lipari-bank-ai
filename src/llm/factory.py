"""Factory: l'unico posto che decide quale provider usare, da una riga di configurazione."""

from src.config import settings
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.client import LLMProvider
from src.llm.openai_provider import OpenAIProvider

_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Singleton da usare come dependency (`Depends`): il client e il suo pool HTTP
    vengono costruiti una volta sola e riusati. Nei test si sostituisce con un fake
    tramite `app.dependency_overrides`."""
    global _provider
    if _provider is None:
        _provider = _build_provider()
    return _provider


def _build_provider() -> LLMProvider:
    model = settings.default_model
    if model.startswith("gpt"):
        return OpenAIProvider(settings.openai_api_key, model)
    if model.startswith("claude"):
        return AnthropicProvider(settings.anthropic_api_key, model)
    raise ValueError(f"Unknown model: {model}")
