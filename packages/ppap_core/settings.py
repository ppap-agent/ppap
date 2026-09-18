"""Single typed settings owner for API and worker. Application code reads LLM_*, never OPENAI_*."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ISOLATED_DEV_SECRET = "ppap_isolated_dev"
ISOLATED_DEV_S3_ENDPOINT = "http://127.0.0.1:9000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True, case_sensitive=True, env_file=None)

    profile: Literal["isolated-dev", "production"] = Field(default="isolated-dev", alias="PPAP_PROFILE")
    git_sha: str = Field(default="", alias="PPAP_BUILD_SHA")

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")

    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    partner_ai_endpoint: str = Field(default="", alias="PPAP_PARTNER_AI_ENDPOINT")
    legacy_engine_url: str = Field(default="", alias="PPAP_LEGACY_ENGINE_URL")

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
    worker_id: str = Field(default="", alias="PPAP_WORKER_ID")

    @model_validator(mode="after")
    def production_must_not_inherit_isolated_dev(self) -> Self:
        if self.profile != "production":
            return self
        isolated_values = {
            self.pg_api_password,
            self.pg_worker_password,
            self.pg_migrator_password,
            self.pg_bootstrap_password,
            self.s3_access_key,
            self.s3_secret_key,
        }
        if ISOLATED_DEV_SECRET in isolated_values or self.s3_endpoint == ISOLATED_DEV_S3_ENDPOINT:
            raise ValueError("production must not inherit isolated-dev credentials or object-storage endpoint")
        if not self.llm_base_url.strip() or not self.llm_model.strip():
            raise ValueError("production must set LLM_BASE_URL and LLM_MODEL explicitly")
        return self

    def dsn(self, role: Literal["api", "worker", "migrator", "bootstrap"]) -> str:
        user, password = {
            "api": (self.pg_api_user, self.pg_api_password),
            "worker": (self.pg_worker_user, self.pg_worker_password),
            "migrator": (self.pg_migrator_user, self.pg_migrator_password),
            "bootstrap": (self.pg_bootstrap_user, self.pg_bootstrap_password),
        }[role]
        database = "postgres" if role == "bootstrap" else self.pg_database
        return (
            f"postgresql://{user}:{password}@{self.pg_host}:{self.pg_port}/{database}"
            "?connect_timeout=5"
        )

    def web_origins(self) -> list[str]:
        port = self.web_port
        return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]

    def public_config(self) -> dict[str, str]:
        return {
            "git_sha": self.git_sha,
            "profile": self.profile,
            "api_url": self.public_api_url.rstrip("/"),
        }


def load_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def cached_settings() -> Settings:
    return Settings()
