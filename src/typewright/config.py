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
    # "text" (human-readable console, default) or "json" (structured, for a log aggregator — D54).
    log_format: str = "text"

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
    llm_max_tokens: int = 2048
    llm_max_tokens_codegen: int = 4096
        # detected-property lists are small
    llm_temperature: float = 0.0  # deterministic detection; low temp curbs fabrication


# --- Kestrel sandbox (Phase 5) ---
    # Where TypeWright reaches Kestrel's /execute endpoint, and the bearer key to call
    # it with (None when Kestrel auth is off). Both read from the TYPEWRIGHT_ prefix:
    # TYPEWRIGHT_KESTREL_BASE_URL / TYPEWRIGHT_KESTREL_API_KEY (D37).
    kestrel_base_url: str = "http://localhost:8000"
    kestrel_api_key: str | None = None
    # Default per-run sandbox budget (seconds). A request's max_test_runtime_seconds
    # overrides it; Kestrel additionally clamps any value down to its own ceiling (D41).
    kestrel_timeout_seconds: float = 30.0
    # The httpx read timeout is the run budget + this margin, so the HTTP call outlives
    # a legitimately long sandbox run instead of aborting it client-side.
    kestrel_http_timeout_buffer_seconds: float = 15.0


    # --- GitHub App (Phase 7) ---
    # HMAC secret for verifying webhook deliveries (X-Hub-Signature-256). When unset/empty,
    # signature verification is SKIPPED with a warning (local dev only) — set it in any real
    # deployment. App-auth + Redis settings arrive with the units that use them (D1).
    github_webhook_secret: str | None = None
    # GitHub App identity for minting installation tokens (Phase 7, D48). The private key is the
    # .pem GitHub generates for the App; point at the FILE rather than inlining a multi-line PEM
    # in an env var. Unset in dev/tests (the client is mocked).
    github_app_id: str | None = None
    github_app_private_key_path: str | None = None
    # arq job queue (Phase 7, D46): Redis between the webhook and the background worker.
    redis_url: str = "redis://localhost:6379"
    # --- Web demo run store (Phase 8, D50) ---
    # SQLite file backing GET /v1/runs/{id} (shareable links). A single-file DB needs no service;
    # in a container point this at a mounted volume so links survive a redeploy.
    runs_db_path: str = "runs.db"
    # --- Cost controls (Phase 9, D52) ---
    # Hard per-analysis LLM-cost ceiling (USD). A request's max_cost_usd can only lower it; an
    # analysis that crosses it aborts with 402. Tune to your model tiers + typical function size.
    max_cost_usd: float = 0.50
    # --- Cost controls (Phase 10, D58): global monthly cap ---
    # Aggregate LLM-spend ceiling (USD) across ALL analyses and entry points (web + GitHub App +
    # fix) within a calendar month, persisted in the runs_db_path SQLite file. Once the month's
    # running total reaches this, further LLM calls return 503 + Retry-After until the month rolls
    # over. Set to 0 (or negative) to disable the monthly cap. Both the web and worker processes
    # must share the same runs_db_path for one global counter.
    max_monthly_cost_usd: float = 10.00
    # --- Bug verification (Phase 10, D60): second-opinion precision filter ---
    # When True (default), each reported bug gets a skeptical second-opinion LLM verdict — is the
    # violated property contractual, and is the failing input in-domain? — to suppress the
    # over-inference/out-of-domain false positives the bug-hunt eval surfaced. Best-effort: a
    # verification failure leaves the bug unverified, never fails the analysis. A request's
    # verify_findings overrides this. Adds +1 LLM call per bug found (0 when an analysis is clean).
    bug_verification_enabled: bool = True
    # --- Rate limiting (Phase 9, D53) ---
    # Per-IP on /v1/analyze and per-installation on the webhook; 429 + Retry-After. Backend "memory"
    # (default, per-process — fine for one instance) or "redis" (shared across replicas, uses redis_url).
    rate_limit_enabled: bool = True
    rate_limit_backend: str = "memory"          # "memory" | "redis"
    rate_limit_analyze_per_minute: int = 10     # per client IP
    rate_limit_webhook_per_minute: int = 30     # per GitHub installation
    # Trust the first X-Forwarded-For IP as the client — ONLY behind a proxy/tunnel you control
    # (otherwise a client could spoof it). Default off = use the peer address.
    trust_forwarded_for: bool = False

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
