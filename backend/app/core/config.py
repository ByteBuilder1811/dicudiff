"""Runtime configuration, read from environment variables / a local .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", str(ROOT / ".env")), extra="ignore")

    data_dir: Path = ROOT / "data"
    database_url: str = ""  # default: sqlite file inside data_dir

    max_upload_mb: int = 25
    max_pages: int = 400
    retention_days: int = 0  # 0 = keep forever
    comparison_timeout_s: int = 300
    rate_limit_per_minute: int = 60  # POST/DELETE (uploads, comparisons, exports, LLM calls) per client IP
    rate_limit_read_per_minute: int = 1200  # GET: cheap reads incl. the UI's 1-1.5 s status polling
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
    # Host-header allow-list (DNS-rebinding defence). "*" disables. "backend" = docker-compose nginx -> backend hop.
    allowed_hosts: str = "localhost,127.0.0.1,[::1],testserver,backend"

    # extraction
    ocr_enabled: bool = False
    ocr_language: str = "eng"
    # PDF parsing runs in a child process with a hard wall-clock limit and an RSS watchdog (see extract_pdf_isolated)
    extract_timeout_s: float = 120.0
    extract_max_rss_mb: int = 1500
    isolate_extraction: bool = True  # false = parse in-process (debugging only)

    # optional shared-secret guard for network exposure (empty = off). Not user auth, not TLS.
    docudiff_api_token: str = ""

    # local embeddings (never downloads unless allow_model_download=true)
    embedding_backend: str = "auto"  # auto | sentence-transformers | hashing | none
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    allow_model_download: bool = False
    rerank_model: str = ""  # e.g. cross-encoder/ms-marco-MiniLM-L-6-v2 (optional)

    # local LLM through Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout_s: int = 180

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'docudiff.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.report_dir.mkdir(parents=True, exist_ok=True)
    return s
