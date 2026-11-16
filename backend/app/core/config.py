import logging
import os
from typing import List, Optional

import httpx

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load the backend .env regardless of the process working directory.
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))
load_dotenv()

# Default DB file lives next to the backend package, anchored to an absolute
# path so it doesn't matter what directory the server is launched from.
_DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "sql_app.db").replace("\\", "/")
_DEFAULT_DATABASE_URL = f"sqlite:///{_DEFAULT_DB_PATH}"

class Settings(BaseSettings):
    # API Config
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Adaptive Quiz Engine"
    
    # Runtime environment: "development" or "production"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()

    # Comma-separated list of frontend origins allowed to call the API
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7 days
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", _DEFAULT_DATABASE_URL)
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # Groq LLM
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    # Comma-separated models tried in order when the primary model is rate-limited
    GROQ_FALLBACK_MODELS: str = os.getenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b,llama-3.1-8b-instant")
    # Keep TLS verification on. Only set to "false" on a dev machine with a broken
    # certificate store; it is ignored in production.
    GROQ_SSL_VERIFY: bool = os.getenv("GROQ_SSL_VERIFY", "true").lower() not in ("0", "false", "no")

    # Upload limits for document-to-quiz
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "10"))

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    model_config = SettingsConfigDict(case_sensitive=True)

settings = Settings()

_DEV_SECRET_KEY = "dev-only-insecure-secret-key-do-not-use-in-production"
if not settings.SECRET_KEY:
    if settings.is_production:
        raise RuntimeError("SECRET_KEY must be set when ENVIRONMENT=production")
    logging.getLogger(__name__).warning("SECRET_KEY not set; using an insecure development key")
    settings.SECRET_KEY = _DEV_SECRET_KEY
if settings.is_production and settings.DATABASE_URL.startswith("sqlite"):
    logging.getLogger(__name__).warning(
        "ENVIRONMENT=production is using SQLite; data is lost on redeploy unless the disk is persistent. Set DATABASE_URL to Postgres."
    )
if settings.is_production and (len(settings.SECRET_KEY) < 32 or "change_in_production" in settings.SECRET_KEY):
    raise RuntimeError("SECRET_KEY is too weak for production; use at least 32 random characters")

# Alias for compatibility with agents that import `config`
config = settings


def resolve_groq_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Prefer an explicit per-request API key over the configured GROQ_API_KEY.

    Used consistently across every endpoint that calls Groq directly, so all of
    them support the same optional per-request override behavior.
    """
    return explicit_key or settings.GROQ_API_KEY or None


def _ssl_verify() -> bool:
    return True if settings.is_production else settings.GROQ_SSL_VERIFY


def groq_http_client(timeout: Optional[float] = 60.0) -> httpx.Client:
    """Shared sync HTTP client for Groq with TLS verification enabled."""
    return httpx.Client(verify=_ssl_verify(), timeout=timeout)


def groq_async_http_client(timeout: Optional[float] = 60.0) -> httpx.AsyncClient:
    """Shared async HTTP client for Groq with TLS verification enabled."""
    return httpx.AsyncClient(verify=_ssl_verify(), timeout=timeout)


def safe_groq_completion(client, **kwargs):
    """Executes a Groq chat completion with automatic model fallbacks

    If a 429 RateLimitError (TPD/RPM limit) occurs on the primary model,
    it automatically retries with secondary fast models so the application
    never fails with a 500 Server Error.
    """
    requested_model = kwargs.get("model") or settings.GROQ_MODEL
    fallbacks = [m.strip() for m in settings.GROQ_FALLBACK_MODELS.split(",") if m.strip()]
    candidate_models = [requested_model, *fallbacks]

    seen = set()
    models = [m for m in candidate_models if not (m in seen or seen.add(m))]

    last_exc = None
    for model in models:
        try:
            kwargs["model"] = model
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower() or "Rate limit" in err_str:
                last_exc = e
                continue
            raise
    if last_exc:
        raise last_exc

