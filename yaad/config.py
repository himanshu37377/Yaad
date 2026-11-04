from functools import lru_cache
from pathlib import Path
import os
from urllib.parse import urlparse
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from yaad.llm.meta_config import DEFAULT_MODEL, STANDARD_MODELS

load_dotenv()
ALLOWED_META_MODELS = set(STANDARD_MODELS)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Settings(BaseModel):
    db_path: Path = Field(default_factory=lambda: Path(os.getenv("YAAD_DB_PATH", "~/.yaad/yaad.sqlite3")).expanduser())
    model_api_key: str | None = Field(default_factory=lambda: os.getenv("MODEL_API_KEY"))
    model: str = Field(default_factory=lambda: os.getenv("YAAD_MODEL", DEFAULT_MODEL))
    cloud_enabled: bool = Field(default_factory=lambda: os.getenv("YAAD_CLOUD_ENABLED", "false").lower() == "true")
    auto_memory: bool = Field(default_factory=lambda: os.getenv("YAAD_AUTO_MEMORY", "true").lower() == "true")
    semantic_retrieval: bool = Field(default_factory=lambda: os.getenv("YAAD_SEMANTIC_RETRIEVAL", "false").lower() == "true")
    semantic_min_similarity: float = Field(default_factory=lambda: _float_env("YAAD_SEMANTIC_MIN_SIMILARITY", 0.30))
    # Spend ceilings (USD). Per-answer review defaults to $0.05; the
    # indexing-run ceiling and the global (all-receipts) cap default
    # to 0 = disabled, so enabling them is always an explicit choice.
    review_max_spend_usd: float = Field(default_factory=lambda: _float_env("YAAD_REVIEW_MAX_SPEND_USD", 0.05))
    pipeline_stage_max_spend_usd: float = Field(default_factory=lambda: _float_env("YAAD_PIPELINE_STAGE_MAX_SPEND_USD", 0.05))
    verify_model_votes: int = Field(default_factory=lambda: _int_env("YAAD_VERIFY_MODEL_VOTES", 1))
    index_max_spend_usd: float = Field(default_factory=lambda: _float_env("YAAD_INDEX_MAX_SPEND_USD", 0.0))
    cloud_spend_cap_usd: float = Field(default_factory=lambda: _float_env("YAAD_CLOUD_SPEND_CAP_USD", 0.0))
    model_provider: str = Field(default_factory=lambda: os.getenv("YAAD_MODEL_PROVIDER", "cloud"))
    local_endpoint: str = Field(default_factory=lambda: os.getenv("YAAD_LOCAL_ENDPOINT", "http://localhost:11434"))
    local_model: str = Field(default_factory=lambda: os.getenv("YAAD_LOCAL_MODEL", "muse-glimmer:30b"))
    max_scanned_pdf_pages: int = Field(default_factory=lambda: _int_env("YAAD_MAX_SCANNED_PDF_PAGES", 30), ge=1)
    audio_max_minutes: int = Field(default_factory=lambda: _int_env("YAAD_AUDIO_MAX_MINUTES", 10), ge=1)

    @field_validator("local_endpoint")
    @classmethod
    def _validate_local_endpoint(cls, value: str) -> str:
        # SSRF guard: the local client POSTs to this endpoint, so it must
        # be an http(s) URL with a real host and no embedded credentials.
        # Non-loopback hosts additionally require the explicit opt-in
        # YAAD_LOCAL_ALLOW_REMOTE=true (a LAN inference box is legitimate;
        # silently POSTing prompts anywhere else is not).
        parsed = urlparse(str(value or ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("YAAD_LOCAL_ENDPOINT must be an http(s) URL with a host")
        if parsed.username or parsed.password:
            raise ValueError("YAAD_LOCAL_ENDPOINT must not contain credentials")
        host = parsed.hostname.lower()
        loopback = host in ("localhost", "::1") or host.startswith("127.")
        if not loopback and os.getenv("YAAD_LOCAL_ALLOW_REMOTE", "false").lower() != "true":
            raise ValueError(
                "YAAD_LOCAL_ENDPOINT host is not loopback; set "
                "YAAD_LOCAL_ALLOW_REMOTE=true to allow a remote local-model endpoint")
        return str(value).rstrip("/")

    def checked_model(self) -> str:
        if self.model.endswith("-contributor") or self.model not in ALLOWED_META_MODELS:
            raise ValueError("Model is not in the approved Standard-tier allowlist")
        return self.model

@lru_cache
def get_settings() -> Settings:
    return Settings()
