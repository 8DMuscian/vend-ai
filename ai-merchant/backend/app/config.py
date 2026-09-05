"""Configuration via Pydantic settings - AGENTS.md compliant.

All secrets via environment variables only. No hardcoded credentials.
"""
from __future__ import annotations

import secrets

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global settings loaded from OS environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://localhost:5432/ai_merchant",
        description="SQLAlchemy asyncpg connection URL",
    )

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for caches/locks",
    )

    # ── LLM ───────────────────────────────────────────────────────────
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key (loaded from env, never committed)",
    )
    llm_model: str = Field(
        default="gpt-4o",
        description="Default LLM model for agent reasoning",
    )

    # ── App ───────────────────────────────────────────────────────────
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_env: str = Field(default="development", description="development | staging | production")
    secret_key: str = Field(
        default_factory=lambda: secrets.token_hex(32),
        description="Application secret key. Auto-generated per process if not set.",
    )

    # ── CORS ──────────────────────────────────────────────────────────
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="Comma-separated allowed CORS origins. Use '*' only in development.",
    )

    # ── Policy limits (hardcoded defaults, configurable via env) ──────
    discount_cap_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        description="Maximum discount percentage per line item",
    )
    min_order_value_cents: int = Field(
        default=10_000,
        ge=0,
        description="Minimum order value in paise (₹100)",
    )
    max_order_value_cents: int = Field(
        default=5_000_000,
        ge=0,
        description="Maximum auto-approved order value in paise (₹50k)",
    )

    # ── Rate limiting ─────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=100, ge=1)
    negotiation_turn_cap: int = Field(default=20, ge=1)


# ── Singleton ────────────────────────────────────────────────────────
settings = Settings()  # noqa: E402


def reload_settings() -> Settings:
    """Refresh settings from environment (useful for tests)."""
    global settings  # noqa: PLC0417
    settings = Settings()  # type: ignore
    return settings