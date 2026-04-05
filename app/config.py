from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me"

    # Database
    database_url: str = "postgresql+asyncpg://aimobiliaria:password@db:5432/aimobiliaria"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Claude API
    anthropic_api_key: str = ""

    # Meta WhatsApp
    meta_whatsapp_verify_token: str = ""

    # Logging
    log_level: str = "INFO"

    # Session
    session_ttl_seconds: int = 86400  # 24 hours
    max_conversation_history: int = 30  # Max messages in context

    # Admin Dashboard
    admin_password: str = "change-me-now"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
