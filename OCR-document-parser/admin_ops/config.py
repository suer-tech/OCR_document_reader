from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_", env_file=".env", extra="ignore")

    telegram_token: str = ""
    telegram_proxy_url: str = Field(default="http://awg-gateway:8888", min_length=1)
    admin_ids: str = ""
    internal_token: str = ""
    database_url: str = ""
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    pulse_url: str = "http://awg-gateway:8080"
    fixer_url: str = "http://awg-gateway:8081"
    enable_fixer: bool = True
    enable_release: bool = True
    timezone: str = "Asia/Yekaterinburg"
    state_path: str = "/state/proposals.sqlite3"
    github_token: str = ""
    github_repo: str = "suer-tech/OCR_document_reader"
    github_base_branch: str = "main"
    github_required_check: str = "validate"
    github_deploy_workflow: str = "ocr-production-deploy.yml"
    codex_model: str = ""
    role: str = ""
    work_root: str = "/work"

    @property
    def allowed_admin_ids(self) -> set[int]:
        return {int(item.strip()) for item in self.admin_ids.split(",") if item.strip()}


@lru_cache(maxsize=1)
def get_ops_settings() -> OpsSettings:
    return OpsSettings()
