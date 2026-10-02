"""Application settings, loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai", "mistral", "google", "stub", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    company_timezone: str = "Asia/Karachi"

    database_url: SecretStr = SecretStr("postgresql://backend_app:backend_app@localhost:5433/novatech")
    db_pool_min_size: int = Field(default=1, ge=0)
    db_pool_max_size: int = Field(default=10, ge=1)

    internal_api_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    expose_api_docs: bool = True

    storage_dir: str = "./data/storage"
    max_upload_mb: int = Field(default=5, ge=1, le=50)
    default_phone_region: str = "PK"

    fault_injection_enabled: bool = False

    # Signed links in emails (candidate slot/offer pages, interviewer feedback, offer approval).
    link_signing_secret: SecretStr | None = None
    link_max_ttl_days: int = Field(default=30, ge=1, le=90)

    # n8n webhooks the backend calls (dispatcher kick, error replay). Empty disables the calls.
    n8n_base_url: str = "http://n8n:5678"
    n8n_api_key: SecretStr | None = None

    llm_provider: LLMProvider = "anthropic"
    llm_model: str = "claude-opus-5"
    llm_timeout_seconds: float = Field(default=60, gt=0, le=600)
    llm_max_tokens: int = Field(default=16000, ge=256, le=64000)
    llm_structured_method: Literal["json_schema", "function_calling"] = "json_schema"

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator("internal_api_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [SecretStr(k.strip()) for k in value.split(",") if k.strip()]
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production":
            # Fault injection must never be reachable in production, whatever the env file says.
            self.fault_injection_enabled = False
            if self.llm_provider == "stub":
                raise ValueError("LLM_PROVIDER=stub is for demos and tests; configure a real provider or 'none'")
            if not self.internal_api_keys:
                raise ValueError("INTERNAL_API_KEYS must be set in production")
            if any(len(k.get_secret_value()) < 24 for k in self.internal_api_keys):
                raise ValueError("INTERNAL_API_KEYS must be at least 24 characters in production")
            if not self.link_signing_secret or len(self.link_signing_secret.get_secret_value()) < 32:
                raise ValueError("LINK_SIGNING_SECRET must be set (at least 32 characters) in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def n8n_webhook_key(self) -> str | None:
        """Key sent to n8n webhooks. Defaults to the primary internal key (shared service credential)."""
        if self.n8n_api_key and self.n8n_api_key.get_secret_value():
            return self.n8n_api_key.get_secret_value()
        return self.internal_api_keys[0].get_secret_value() if self.internal_api_keys else None

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_secret_key.get_secret_value()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
