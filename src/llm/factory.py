"""Factory: l'unico posto che decide quale provider usare, da una riga di configurazione."""

from src.config import require_secret, settings
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.client import LLMProvider
from src.llm.openai_provider import OpenAIProvider

_provider: LLMProvider | None = None


def get_llm_provider(model: str | None = None) -> LLMProvider:
    """Singleton da usare come dependency (`Depends`): il client e il suo pool HTTP
    vengono costruiti una volta sola e riusati. Nei test si sostituisce con un fake
    tramite `app.dependency_overrides`.

    `model` esplicito costruisce un provider dedicato (es. il judge del Giorno 6,
    che DEVE essere un modello diverso e piu' capace del generatore)."""
    global _provider
    if model is not None:
        return _build_provider(model)
    if _provider is None:
        _provider = _build_provider(settings.default_model)
    return _provider


def _build_provider(model: str) -> LLMProvider:
    if model.startswith("gpt"):
        return OpenAIProvider(require_secret(settings.openai_api_key, "OPENAI_API_KEY"), model)
    if model.startswith("claude"):
        return AnthropicProvider(
            require_secret(settings.anthropic_api_key, "ANTHROPIC_API_KEY"), model
        )
    raise ValueError(f"Unknown model: {model}")
