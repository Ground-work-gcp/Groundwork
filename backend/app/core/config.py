"""One configuration for the whole backend, read from the environment (``.env.example``).

Every external integration has an explicit backend switch with a local default, so local
development needs no credentials and nothing silently pretends to be a cloud service.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT.parent / ".env"), str(ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    service_name: str = "groundwork"

    # PostgreSQL on Cloud SQL in production, e.g.
    #   postgresql+psycopg://groundwork:<pw>@/groundwork?host=/cloudsql/<project>:<region>:<instance>
    # SQLite is the zero-setup local default.
    database_url: str = f"sqlite:///{(ROOT / 'var' / 'groundwork.db').as_posix()}"

    jwt_secret: str = "dev-only-insecure-secret-change-me-0000000"
    jwt_ttl_minutes: int = 12 * 60
    cookie_name: str = "gw_session"
    cookie_secure: bool | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_base_url: str = "http://localhost:5173"

    # ── storage ────────────────────────────────────────────────────────────
    storage_backend: Literal["local", "gcs"] = "local"
    local_storage_dir: str = str(ROOT / "var" / "storage")
    gcs_bucket: str = ""

    # ── Google Cloud project (application default credentials / Workload Identity) ─
    gcp_project: str = ""
    gcp_location: str = "us-central1"

    # ── document extraction ────────────────────────────────────────────────
    extraction_backend: Literal["local", "documentai"] = "local"
    documentai_location: str = "us"
    documentai_processor_id: str = ""

    # ── generative AI (Gemini on Vertex AI) ────────────────────────────────
    #   vertex     → Vertex AI with Application Default Credentials (recommended on Cloud Run)
    #   gemini_api → Google AI Studio API key (GEMINI_API_KEY; quickest for local development)
    ai_backend: Literal["none", "vertex", "gemini_api"] = "none"
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key: str = ""

    # ── PII (Sensitive Data Protection) ────────────────────────────────────
    pii_backend: Literal["local", "dlp"] = "local"

    # ── workflow safety ────────────────────────────────────────────────────
    workflow_execution_enabled: bool = True
    require_separate_approver: bool = True
    action_ttl_days: int = 14

    notify_webhook_url: str = ""
    # HMAC-SHA256 key for signing outbound webhooks and verifying scheduler calls (Secret Manager).
    webhook_signing_secret: str = ""
    max_upload_mb: int = 25
    frontend_dist: str = str(ROOT.parent / "frontend" / "dist")

    # ── database pool (Cloud SQL connection limits are per instance; keep this × max instances below them)
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: int = 10
    db_connect_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 30_000

    # ── provider limits (every external call is bounded) ──────────────────
    provider_timeout_seconds: float = 60.0
    provider_attempts: int = 3
    documentai_max_mb: int = 20          # online (synchronous) processing limit
    documentai_max_pages: int = 15       # OCR processor online page limit
    gemini_max_output_tokens: int = 2048
    gemini_max_prompt_chars: int = 60_000
    assistant_rate_per_minute: int = 20
    studio_rate_per_minute: int = 60

    # ── Canton Network: multi-party external approvals (docs/canton) ─────
    # Disabled by default; the rest of Groundwork never depends on it.
    groundwork_canton_enabled: bool = False
    groundwork_canton_network: Literal["localnet", "devnet", "testnet", "mainnet"] = "localnet"
    canton_api_mode: Literal["json"] = "json"           # JSON Ledger API v2 (verified against Canton 3.4)
    canton_package_name: str = "groundwork-approvals"   # templates are addressed by package name (#name:Module:Entity)
    canton_approval_package_id: str = ""               # optional pin; recorded with every approval
    canton_synchronizer_id: str = ""
    canton_command_timeout_seconds: float = 30.0
    canton_event_poll_interval_seconds: float = 5.0
    canton_request_ttl_days: int = 30
    canton_ca_bundle: str = ""                          # extra CA for participant TLS (self-hosted nodes)
    canton_background_sync: bool = True
    # One block per participant. Auth is either a local HMAC secret (LocalNet only) or OIDC client credentials.
    canton_legal_aid_ledger_url: str = ""
    canton_legal_aid_party_id: str = ""
    canton_legal_aid_user_id: str = "groundwork-legalaid"
    canton_legal_aid_secret_ref: str = ""               # LocalNet HMAC secret (Secret Manager in cloud)
    canton_legal_aid_oidc_token_url: str = ""
    canton_legal_aid_oidc_client_id: str = ""
    canton_legal_aid_oidc_client_secret: str = ""
    canton_legal_aid_oidc_audience: str = ""
    canton_registry_ledger_url: str = ""
    canton_registry_party_id: str = ""
    canton_registry_user_id: str = "groundwork-registry"
    canton_registry_secret_ref: str = ""
    canton_registry_oidc_token_url: str = ""
    canton_registry_oidc_client_id: str = ""
    canton_registry_oidc_client_secret: str = ""
    canton_registry_oidc_audience: str = ""

    log_json: bool | None = None         # default: JSON in production, text locally
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def json_logs(self) -> bool:
        return self.environment == "production" if self.log_json is None else self.log_json

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production" if self.cookie_secure is None else self.cookie_secure

    def validate_runtime(self) -> None:
        """Refuse to start production on development defaults."""
        if self.environment != "production":
            return
        problems: list[str] = []
        if self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a random value of at least 32 characters")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point at PostgreSQL (Cloud SQL) in production")
        if self.storage_backend == "gcs" and not self.gcs_bucket:
            problems.append("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        if self.extraction_backend == "documentai" and not (self.gcp_project and self.documentai_processor_id):
            problems.append("GCP_PROJECT and DOCUMENTAI_PROCESSOR_ID are required for Document AI")
        if self.ai_backend == "vertex" and not self.gcp_project:
            problems.append("GCP_PROJECT is required for Gemini on Vertex AI")
        if self.ai_backend == "gemini_api" and not self.gemini_api_key:
            problems.append("GEMINI_API_KEY is required when AI_BACKEND=gemini_api")
        if self.pii_backend == "dlp" and not self.gcp_project:
            problems.append("GCP_PROJECT is required for Sensitive Data Protection")
        if self.storage_backend != "gcs":
            problems.append("STORAGE_BACKEND must be gcs in production (local disk on Cloud Run is ephemeral)")
        if any(origin == "*" for origin in self.cors_origin_list):
            problems.append("CORS_ORIGINS must not be '*' when credentials are allowed")
        if self.cookie_secure is False:
            problems.append("COOKIE_SECURE must not be false in production")
        if self.notify_webhook_url and not self.notify_webhook_url.startswith("https://"):
            problems.append("NOTIFY_WEBHOOK_URL must use https")
        if self.groundwork_canton_enabled:
            if self.groundwork_canton_network == "localnet":
                problems.append("GROUNDWORK_CANTON_NETWORK=localnet is not allowed in production")
            for role in ("legal_aid", "registry"):
                url = getattr(self, f"canton_{role}_ledger_url")
                if not url.startswith("https://"):
                    problems.append(f"CANTON_{role.upper()}_LEDGER_URL must use https in production")
                if getattr(self, f"canton_{role}_secret_ref"):
                    problems.append(f"CANTON_{role.upper()}_SECRET_REF (HMAC tokens) is LocalNet-only; use OIDC client credentials")
                if not (getattr(self, f"canton_{role}_oidc_token_url") and getattr(self, f"canton_{role}_oidc_client_id")):
                    problems.append(f"OIDC client credentials are required for the {role} participant")
                if not getattr(self, f"canton_{role}_party_id"):
                    problems.append(f"CANTON_{role.upper()}_PARTY_ID is required")
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
