from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AllergyGuard API"
    environment: str = "development"

    database_url: str = "postgresql+psycopg://allergyguard:allergyguard@localhost:5432/allergyguard"

    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:8080"

    seed_demo_data: bool = True

    # AI providers (used from Part 2 onward). Empty key = feature falls back to non-AI behaviour.
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    llm_model: str = ""

    # Knowledge base changes need a second person to approve (four-eyes). Set false only for a
    # single-administrator deployment.
    knowledge_require_second_reviewer: bool = True

    # Ingredient normalization: RapidFuzz similarity (0-100) needed to accept a fuzzy match for a whole
    # ingredient name / a single word, the minimum length fuzzy matching applies to, and the lead the
    # best candidate needs over the second best (otherwise the match is ambiguous and not used).
    fuzzy_phrase_threshold: float = 90
    fuzzy_word_threshold: float = 88
    fuzzy_min_length: int = 5
    fuzzy_margin: float = 3

    # Evidence freshness: older records still show allergens as present, but are not used to rule
    # allergens out. Restaurant recipes: days since staff last verified them. Product database
    # entries: days since the entry was last updated.
    restaurant_record_max_age_days: int = 365
    # Open Food Facts response cache (seconds; 0 disables). Failures are never cached.
    off_cache_ttl_seconds: int = 6 * 3600
    off_not_found_ttl_seconds: int = 600
    off_search_ttl_seconds: int = 3600
    # Work done after the response (unresolved-ingredient queue, review queue). "inline" runs it
    # before responding (tests, debugging).
    background_inline: bool = False
    background_workers: int = 2
    # At most this many photo-processing jobs (OCR, barcode, recognition) run at once per worker.
    # Photo jobs run at once; 0 = one per CPU core (measured: each OCR job then uses one core; see
    # docs/PERFORMANCE.md).
    max_concurrent_image_jobs: int = 0
    image_job_wait_seconds: int = 30
    # Failed-login throttling per email address and client.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    product_record_max_age_days: int = 1095

    # External product pages (Stage 3). Manufacturer pages are fetched only from approved domains.
    # Web search is off unless a provider and key are set (provider: "brave").
    manufacturer_pages_enabled: bool = True
    web_search_provider: str = ""
    web_search_api_key: str = ""
    web_page_max_bytes: int = 1_000_000
    web_timeout_seconds: float = 8.0

    # External sources (Open Food Facts, manufacturer pages, web search): a source that is down is
    # retried with exponential backoff, and after repeated failures its circuit opens: it is not
    # called for a cool-down period, so analyses don't wait on timeouts (app/sources/base.py).
    source_max_attempts: int = 2  # 1 = no retry
    source_backoff_seconds: float = 0.3  # before the 2nd attempt; doubles each time, plus jitter
    source_breaker_threshold: int = 5  # consecutive failed calls that open the circuit
    source_breaker_cooldown_seconds: int = 60

    # Packaged-food pipeline
    off_base_url: str = "https://world.openfoodfacts.org"
    off_user_agent: str = "AllergyGuard/0.1 (allergen information app)"
    off_timeout_seconds: float = 8.0
    max_upload_mb: int = 10
    tesseract_cmd: str = ""  # empty = use `tesseract` on PATH

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "production" and settings.jwt_secret == DEV_JWT_SECRET:
        raise RuntimeError("Set JWT_SECRET before running in production.")
    return settings
