"""Single typed settings owner for API and worker. Application code reads LLM_*, never OPENAI_*."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import ClassVar, Literal, Optional, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ppap_core.deep_policy import DeepExecutionPolicy

ISOLATED_DEV_SECRET = "ppap_isolated_dev"
ISOLATED_DEV_S3_ENDPOINT = "http://127.0.0.1:9000"
ISOLATED_DEV_LLM_BASE_URL = "https://api.trustedtunnel.app/v1"  # pragma: allowlist secret
ISOLATED_DEV_LLM_MODEL = "gpt-5.6-luna"  # pragma: allowlist secret


def _held_production_secret(value: str) -> bool:
    return bool(value.strip()) and value != ISOLATED_DEV_SECRET


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True, case_sensitive=True, env_file=None)

    profile: Literal["isolated-dev", "remote-dev", "production"] = Field(default="isolated-dev", alias="PPAP_PROFILE")
    git_sha: str = Field(default="", alias="PPAP_BUILD_SHA")

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")

    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_notifications_enabled: bool = Field(default=False, alias="PPAP_FEISHU_NOTIFICATIONS_ENABLED")
    feishu_verification_token: str = Field(default="", alias="FEISHU_VERIFICATION_TOKEN")
    feishu_ai_item_id: str = Field(default="", alias="FEISHU_AI_ITEM_ID")
    feishu_free_trial_quota_points: int = Field(default=10000, alias="FEISHU_FREE_TRIAL_QUOTA_POINTS")
    partner_ai_endpoint: str = Field(default="", alias="PPAP_PARTNER_AI_ENDPOINT")
    legacy_engine_url: str = Field(default="", alias="PPAP_LEGACY_ENGINE_URL")
    onlyoffice_url: str = Field(default="", alias="PPAP_ONLYOFFICE_URL")
    onlyoffice_api_url: str = Field(default="", alias="PPAP_ONLYOFFICE_API_URL")
    onlyoffice_secret: str = Field(default="", alias="PPAP_ONLYOFFICE_SECRET")

    pg_host: str = Field(default="127.0.0.1", alias="PPAP_PG_HOST")
    pg_port: int = Field(default=5432, alias="PPAP_PG_PORT")
    pg_database: str = Field(default="ppap", alias="PPAP_PG_DATABASE")
    pg_api_user: str = Field(default="ppap_api", alias="PPAP_PG_API_USER")
    pg_api_password: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_PG_API_PASSWORD")
    pg_worker_user: str = Field(default="ppap_worker", alias="PPAP_PG_WORKER_USER")
    pg_worker_password: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_PG_WORKER_PASSWORD")
    pg_migrator_user: str = Field(default="ppap_migrator", alias="PPAP_PG_MIGRATOR_USER")
    pg_migrator_password: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_PG_MIGRATOR_PASSWORD")
    pg_bootstrap_user: str = Field(default="ppap", alias="PPAP_PG_BOOTSTRAP_USER")
    pg_bootstrap_password: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_PG_BOOTSTRAP_PASSWORD")

    s3_endpoint: str = Field(default=ISOLATED_DEV_S3_ENDPOINT, alias="PPAP_S3_ENDPOINT")
    s3_region: str = Field(default="us-east-1", alias="PPAP_S3_REGION")
    s3_bucket: str = Field(default="ppap-objects", alias="PPAP_S3_BUCKET")
    s3_access_key: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_S3_ACCESS_KEY")
    s3_secret_key: str = Field(default=ISOLATED_DEV_SECRET, alias="PPAP_S3_SECRET_KEY")

    api_bind: str = Field(default="127.0.0.1", alias="PPAP_API_BIND")
    api_port: int = Field(default=8080, alias="PPAP_API_PORT")
    public_api_url: str = Field(default="http://127.0.0.1:8080", alias="PPAP_PUBLIC_API_URL")
    web_bind: str = Field(default="127.0.0.1", alias="PPAP_WEB_BIND")
    web_port: int = Field(default=4173, alias="PPAP_WEB_PORT")
    web_url: str = Field(default="", alias="PPAP_WEB_URL")
    worker_id: str = Field(default="", alias="PPAP_WORKER_ID")
    deep_controller_socket: str = Field(default="", alias="PPAP_DEEP_CONTROLLER_SOCKET")
    deep_executor_revision: str = Field(default="", alias="PPAP_DEEP_EXECUTOR_REVISION")
    deep_sandbox_profile: str = Field(default="off", alias="PPAP_DEEP_SANDBOX_PROFILE")
    deep_customer_enabled: bool = Field(default=False, alias="PPAP_DEEP_CUSTOMER_ENABLED")
    current_tenant_allowlist: str = Field(default="", alias="PPAP_CURRENT_TENANT_ALLOWLIST")
    usage_charge_enabled: bool = Field(default=True, alias="PPAP_USAGE_CHARGE_ENABLED")
    mcp_enabled: bool = Field(default=False, alias="PPAP_MCP_ENABLED")
    mcp_allowed_origins: list[str] = Field(default_factory=list, alias="PPAP_MCP_ALLOWED_ORIGINS")
    support_phone: str = Field(default="", alias="PPAP_SUPPORT_PHONE", max_length=200)
    support_email: str = Field(default="", alias="PPAP_SUPPORT_EMAIL", max_length=320)
    support_service_hours: str = Field(default="", alias="PPAP_SUPPORT_SERVICE_HOURS", max_length=200)
    support_qr_file: str = Field(default="", alias="PPAP_SUPPORT_QR_FILE")

    # Checker/OCR names kept from the migrated engine. Product code still reads llm_*.
    DEFAULT_OCR_OPENAI_BASE_URL: ClassVar[str] = "https://gateway.ai.cloudflare.com/v1/b1a6ed92ee3fad00dcbb1e3e57327c02/openrouter/openrouter/v1"
    DEFAULT_OCR_MODEL_NAME: ClassVar[str] = "google/gemma-3-27b-it:free"
    APP_NAME: str = "PPAP"
    APP_VERSION: str = "0.1.0"
    APP_GIT_SHA: str = "unknown"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    DEVELOPMENT: bool = False
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    MODEL_NAME: str = ""
    OPENAI_EXTRA_BODY_JSON: str = ""
    OPENAI_REASONING_EFFORT: Optional[str] = None
    OPENAI_TEMPERATURE: Optional[float] = None
    OPENAI_TOP_P: Optional[float] = None
    AI_RATE_LIMIT_PER_MINUTE: int = 60
    MAX_PARALLEL_WORKERS: int = 7
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_HOST: str = ""
    MAX_FILE_SIZE: str = "100MB"
    ALLOWED_EXTENSIONS: str = ".pdf,.doc,.docx,.pptx,.xlsx,.xls,.xlsm,.jpg,.jpeg,.png,.gif,.md"
    OCR_ADAPTER_MODE: str = "auto"
    OCR_ENGINE: str = "easyocr"
    OCR_LANGUAGES: str = "ch_sim,en"
    OCR_REMOTE_URL: str = ""
    OCR_OPENAI_API_KEY: str = ""
    OCR_OPENAI_BASE_URL: str = ""
    OCR_MODEL_NAME: str = ""
    OCR_LOCAL_OSS_DIR: str = ""
    OCR_LOCAL_SQLITE_DB: str = ""
    PADDLE_OCR_URL: str = ""
    PADDLE_TOKEN: str = ""

    @model_validator(mode="after")
    def production_must_not_inherit_isolated_dev(self) -> Self:
        if self.mcp_enabled:
            api = urlsplit(self.public_api_url)
            if (
                not api.hostname
                or api.username
                or api.password
                or api.query
                or api.fragment
                or (api.scheme != "https" and not (self.profile != "production" and api.scheme == "http" and api.hostname in {"127.0.0.1", "localhost", "::1"}))
            ):
                raise ValueError("MCP requires a public HTTPS API URL; HTTP is permitted only on a development loopback address")
            for origin in self.mcp_allowed_origins:
                parsed = urlsplit(origin)
                if (
                    not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or "*" in origin
                    or origin != f"{parsed.scheme}://{parsed.netloc}"
                    or (parsed.scheme != "https" and not (self.profile != "production" and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))
                ):
                    raise ValueError("MCP browser origins must be explicit HTTPS origins, or development loopback origins")
        if self.profile == "isolated-dev":
            if not self.llm_base_url.strip():
                self.llm_base_url = ISOLATED_DEV_LLM_BASE_URL
            if not self.llm_model.strip():
                self.llm_model = ISOLATED_DEV_LLM_MODEL
            self._fill_checker_llm_from_product()
            return self
        role_passwords = (
            self.pg_api_password,
            self.pg_worker_password,
            self.pg_migrator_password,
            self.pg_bootstrap_password,
        )
        held_roles = tuple(value for value in role_passwords if _held_production_secret(value))
        if not held_roles:
            raise ValueError("production must set the PostgreSQL password for this process")
        isolated_values = {
            self.s3_access_key,
            self.s3_secret_key,
        }
        if ISOLATED_DEV_SECRET in isolated_values or self.s3_endpoint == ISOLATED_DEV_S3_ENDPOINT:
            raise ValueError("production must not inherit isolated-dev credentials or object-storage endpoint")
        if not self.llm_base_url.strip() or not self.llm_model.strip():
            raise ValueError("production must set LLM_BASE_URL and LLM_MODEL explicitly")
        if not self.web_url.strip():
            raise ValueError("production must set PPAP_WEB_URL explicitly")
        self._fill_checker_llm_from_product()
        return self

    def _fill_checker_llm_from_product(self) -> None:
        if not self.OPENAI_API_KEY.strip():
            self.OPENAI_API_KEY = self.llm_api_key
        if not self.OPENAI_BASE_URL.strip():
            self.OPENAI_BASE_URL = self.llm_base_url
        if not self.MODEL_NAME.strip():
            self.MODEL_NAME = self.llm_model

    def get_allowed_extensions(self) -> list[str]:
        return [ext.strip() for ext in self.ALLOWED_EXTENSIONS.split(",")]

    def deep_execution_policy(self) -> DeepExecutionPolicy:
        return DeepExecutionPolicy.model_validate(
            {
                "app_revision": self.git_sha,
                "model": self.llm_model,
                "model_revision": hashlib.sha256((self.llm_base_url + "\n" + self.llm_model).encode()).hexdigest(),
                "sandbox_profile": self.deep_sandbox_profile,
                **({"executor_label": self.deep_executor_revision.strip()} if self.deep_executor_revision.strip() else {}),
            }
        )

    def deep_execution_revision(self) -> str:
        return self.deep_execution_policy().engine_revision()

    def get_ocr_languages(self) -> list[str]:
        return [lang.strip() for lang in self.OCR_LANGUAGES.split(",")]

    def get_ocr_adapter_mode(self) -> str:
        mode = self.OCR_ADAPTER_MODE.strip().lower()
        if mode in {"auto", "local", "remote"}:
            return mode
        raise ValueError(f"Invalid OCR_ADAPTER_MODE: {self.OCR_ADAPTER_MODE}")

    def get_ocr_openai_api_key(self) -> str:
        return (self.OCR_OPENAI_API_KEY or self.OPENAI_API_KEY).strip()

    def get_ocr_openai_base_url(self) -> str:
        return (self.OCR_OPENAI_BASE_URL or self.OPENAI_BASE_URL or self.DEFAULT_OCR_OPENAI_BASE_URL).strip()

    def get_ocr_model_name(self) -> str:
        return (self.OCR_MODEL_NAME or self.MODEL_NAME or self.DEFAULT_OCR_MODEL_NAME).strip()

    def get_ocr_paddle_url(self) -> str:
        return self.PADDLE_OCR_URL.strip()

    def get_ocr_paddle_token(self) -> str:
        return self.PADDLE_TOKEN.strip()

    def dsn(self, role: Literal["api", "worker", "migrator", "bootstrap"]) -> str:
        user, password = {
            "api": (self.pg_api_user, self.pg_api_password),
            "worker": (self.pg_worker_user, self.pg_worker_password),
            "migrator": (self.pg_migrator_user, self.pg_migrator_password),
            "bootstrap": (self.pg_bootstrap_user, self.pg_bootstrap_password),
        }[role]
        database = "postgres" if role == "bootstrap" else self.pg_database
        return f"postgresql://{user}:{password}@{self.pg_host}:{self.pg_port}/{database}?connect_timeout=15"

    def canonical_web_url(self) -> str:
        explicit = self.web_url.strip().rstrip("/")
        if explicit:
            return explicit
        return f"http://127.0.0.1:{self.web_port}"

    def web_origins(self) -> list[str]:
        origins = [self.canonical_web_url()]
        if self.profile != "production":
            for extra in (f"http://127.0.0.1:{self.web_port}", f"http://localhost:{self.web_port}"):
                if extra not in origins:
                    origins.append(extra)
        return origins

    def deep_offered(self) -> bool:
        return self.profile == "isolated-dev" or self.deep_customer_enabled

    def allowed_tenant_keys(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.current_tenant_allowlist.split(",") if part.strip())

    def tenant_allowed(self, tenant_key: str) -> bool:
        allowed = self.allowed_tenant_keys()
        return not allowed or tenant_key.strip() in allowed

    def public_config(self) -> dict[str, object]:
        return {
            "git_sha": self.git_sha,
            "profile": self.profile,
            "api_url": self.public_api_url.rstrip("/"),
            "deep_offered": self.deep_offered(),
            "feishu_app_id": self.feishu_app_id,
            "current_access": "allowlist" if self.allowed_tenant_keys() else "open",
            "usage_charge_enabled": self.usage_charge_enabled,
        }


def load_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def cached_settings() -> Settings:
    return Settings()


get_settings = cached_settings
