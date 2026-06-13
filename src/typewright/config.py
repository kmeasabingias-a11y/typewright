"""Application configuration, loaded from environment variables.

A single ``Settings`` object is the typed, validated source of truth for all
runtime configuration (D9). Phase 2 adds the LLM model-tier mapping and call
tuning (D13, D17). The provider key is read from ``ANTHROPIC_API_KEY`` (or
``TYPEWRIGHT_ANTHROPIC_API_KEY``) and passed explicitly to the LLM client.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TYPEWRIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TypeWright"
    environment: str = "dev"
    log_level: str = "INFO"

    # --- LLM / property detection (Phase 2) ---
    # Read from ANTHROPIC_API_KEY (no TYPEWRIGHT_ prefix) so the standard
    # provider env var works, or TYPEWRIGHT_ANTHROPIC_API_KEY if you prefer the
    # project prefix. Passed explicitly to the LLM client (D13).
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "TYPEWRIGHT_ANTHROPIC_API_KEY"
        ),
    )

    # model_tier -> LiteLLM model string. The ``anthropic/`` prefix tells
    # LiteLLM which provider to route to (D17). IDs from the current catalog.
    model_economy: str = "anthropic/claude-haiku-4-5"
    model_standard: str = "anthropic/claude-sonnet-4-6"
    model_premium: str = "anthropic/claude-opus-4-8"
    default_model_tier: str = "standard"

    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2      # Instructor reask attempts on invalid output
    llm_max_tokens: int = 1024    # detected-property lists are small
    llm_temperature: float = 0.0  # deterministic detection; low temp curbs fabrication

    def model_for_tier(self, tier: str) -> str:
        """Resolve a request's ``model_tier`` to a concrete model string.

        An unknown tier falls back to the standard model, so a bad value
        degrades to a sensible default rather than erroring.
        """
        return {
            "economy": self.model_economy,
            "standard": self.model_standard,
            "premium": self.model_premium,
        }.get(tier, self.model_standard)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

    ``lru_cache`` makes this return the same instance every call, so settings
    are read from the environment exactly once.
    """
    return Settings()
