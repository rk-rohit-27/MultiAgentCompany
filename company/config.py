from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    vault_dir: Path = Path("vault")
    workspace_dir: Path = Path("workspace")
    telegram_token: SecretStr
    owner_user_id: int = Field(gt=0)
    owner_chat_id: int = Field(gt=0)
    openai_api_key: SecretStr
    openai_base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    # Intentionally operator-configured, never chosen by the model.
    searxng_url: str = "http://127.0.0.1:8080"
    sandbox_image: str = "company-qa:1"
    sandbox_timeout: int = Field(default=180, ge=10, le=600)
    max_retries: int = Field(default=3, ge=0, le=3)
    max_revisions: int = Field(default=5, ge=1, le=10)
    max_jobs: int = Field(default=20, ge=1, le=100)
    context_tokens: int = Field(default=48000, ge=2000, le=64000)
    run_token_budget: int = Field(default=300000, ge=20000, le=1000000)
    admin_token: SecretStr | None = None
    admin_users: str | None = None
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = Field(default=8080, ge=1, le=65535)

    def prepare(self):
        for path in (self.data_dir, self.vault_dir, self.workspace_dir):
            path.mkdir(parents=True, exist_ok=True)
