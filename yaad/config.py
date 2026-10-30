from functools import lru_cache
from pathlib import Path
import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from yaad.llm.meta_config import DEFAULT_MODEL, STANDARD_MODELS

load_dotenv()
ALLOWED_META_MODELS = set(STANDARD_MODELS)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Settings(BaseModel):
    db_path: Path = Field(default_factory=lambda: Path(os.getenv("YAAD_DB_PATH", "~/.yaad/yaad.sqlite3")).expanduser())
    model_api_key: str | None = Field(default_factory=lambda: os.getenv("MODEL_API_KEY"))
    model: str = Field(default_factory=lambda: os.getenv("YAAD_MODEL", DEFAULT_MODEL))
    cloud_enabled: bool = Field(default_factory=lambda: os.getenv("YAAD_CLOUD_ENABLED", "false").lower() == "true")
    model_provider: str = Field(default_factory=lambda: os.getenv("YAAD_MODEL_PROVIDER", "cloud"))
    local_endpoint: str = Field(default_factory=lambda: os.getenv("YAAD_LOCAL_ENDPOINT", "http://localhost:11434"))
    local_model: str = Field(default_factory=lambda: os.getenv("YAAD_LOCAL_MODEL", "muse-glimmer:30b"))
    max_scanned_pdf_pages: int = Field(default_factory=lambda: _int_env("YAAD_MAX_SCANNED_PDF_PAGES", 30), ge=1)
    audio_max_minutes: int = Field(default_factory=lambda: _int_env("YAAD_AUDIO_MAX_MINUTES", 10), ge=1)

    def checked_model(self) -> str:
        if self.model.endswith("-contributor") or self.model not in ALLOWED_META_MODELS:
            raise ValueError("Model is not in the approved Standard-tier allowlist")
        return self.model

@lru_cache
def get_settings() -> Settings:
    return Settings()
