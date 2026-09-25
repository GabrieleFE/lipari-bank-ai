from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

_FIELD_HINTS = {
    "bool_parsing": "deve essere true o false",
    "greater_than": "deve essere maggiore di zero",
    "int_parsing": "deve essere un numero intero",
    "missing": "è obbligatoria; impostala nel file .env",
    "string_too_short": "non può essere vuota",
}


def _field_hint(field: str, error_type: str) -> str:
    if field == "jwt_secret" and error_type == "string_too_short":
        return "deve contenere almeno 32 caratteri"
    if field == "environment" and error_type == "literal_error":
        return "deve essere local, test, staging oppure production"
    return _FIELD_HINTS.get(error_type, "controlla tipo e formato del valore")


class ConfigurationError(RuntimeError):
    """Errore leggibile per una configurazione ambientale non valida."""


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="LipariBank AI", min_length=1)
    environment: Environment = "local"
    debug: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:4200", "http://localhost:5173"],
        min_length=1,
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    database_url: str = Field(min_length=1)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    default_model: str = Field(default="gpt-4o-mini", min_length=1)
    categorize_model: str = Field(default="gpt-4o-mini", min_length=1)
    judge_model: str = Field(default="gpt-4o", min_length=1)
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        min_length=1,
    )
    embedding_dim: int = Field(default=384, gt=0)
    max_tokens_per_request: int = Field(default=2000, gt=0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    max_daily_cost_eur: float = Field(default=5.0, ge=0)
    history_max_messages: int = Field(default=20, gt=0)
    jwt_secret: SecretStr = Field(min_length=32)


def load_settings(env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        problems: list[str] = []
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"])
            hint = _field_hint(field, error["type"])
            problems.append(f"{field.upper()}: {hint}")
        details = "; ".join(problems)
        raise ConfigurationError(
            f"Configurazione non valida. Correggi queste variabili nel file .env: {details}"
        ) from None


def secret_is_configured(value: SecretStr | None) -> bool:
    return value is not None and bool(value.get_secret_value().strip())


def require_secret(value: SecretStr | None, variable_name: str) -> str:
    if not secret_is_configured(value):
        raise ConfigurationError(
            f"Configurazione mancante: {variable_name} non è impostata. "
            f"Aggiungila al file .env prima di usare questa funzionalità."
        )
    assert value is not None
    return value.get_secret_value().strip()


settings = load_settings()
