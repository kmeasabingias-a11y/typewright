"""Application configuration, loaded from environment variables.

A single ``Settings`` object is the typed, validated source of truth for all
runtime configuration. Phase 1 needs almost nothing here, but establishing the
pattern now means LLM keys (Phase 2) and Kestrel settings (Phase 5) slot in by
adding fields — not by introducing a new mechanism later.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix ="TYPEWRIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra = "ignore",
    )

    app_name: str = "TypeWright"
    environment: str = "dev"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton.

      ``lru_cache`` makes this return the same instance every call, so settings
      are read from the environment exactly once.
      """
    return Settings()