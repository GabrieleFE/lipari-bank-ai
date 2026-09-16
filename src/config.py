from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LipariBank AI"
    debug: bool = False

    database_url: str
    openai_api_key: str
    anthropic_api_key: str
    default_model: str = "gpt-4o-mini"
    categorize_model: str = "gpt-4o-mini"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    max_tokens_per_request: int = 2000
    llm_timeout_seconds: float = 60.0
    max_daily_cost_eur: float = 5.0
    history_max_messages: int = 20
    jwt_secret: str


settings = Settings()  # raise at import if missing required
