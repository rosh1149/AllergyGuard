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
