"""Application settings, loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# mistral: Mistral AI (La Plateforme). none: AI switched off (rules only, every case the AI would have read goes to a
# person). stub: deterministic offline model for automated tests and CI only; refused in production.
LLMProvider = Literal["mistral", "none", "stub"]


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
    staff_login_link_minutes: int = Field(default=15, ge=5, le=60)
    staff_session_hours: int = Field(default=8, ge=1, le=24)
    # Development/demo only: every active staff account can also sign in with this shared password.
    # Empty = disabled (the default). Refused when APP_ENV=production.
    staff_demo_password: SecretStr | None = None

    # n8n webhooks the backend calls (dispatcher kick, error replay). Empty disables the calls.
    n8n_base_url: str = "http://n8n:5678"
    n8n_api_key: SecretStr | None = None

    llm_provider: LLMProvider = "mistral"
    # ministral-14b-latest: the strongest model a free (Experiment) Mistral key may call (30 requests/min), with
    # native JSON-schema output. With a paid key use mistral-medium-latest. Pin a dated version (e.g.
    # ministral-14b-2512) when you freeze a release, so evaluation results stay comparable.
    llm_model: str = "ministral-14b-latest"
    mistral_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=60, gt=0, le=600)
    llm_max_tokens: int = Field(default=2000, ge=256, le=32000)
    llm_temperature: float = Field(default=0.0, ge=0, le=1)
    llm_structured_method: Literal["json_schema", "function_calling"] = "json_schema"
    # Requests per second per backend worker process (the image runs 2). 0.25 x 2 = 30 requests/min, the free-tier
    # limit of ministral-14b; a 429 is still handled (retryable, the dispatcher backs off).
    llm_requests_per_second: float = Field(default=0.25, gt=0, le=50)

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
                raise ValueError("LLM_PROVIDER=stub is for automated tests; use 'mistral' (or 'none' to switch AI off)")
            if not self.internal_api_keys:
                raise ValueError("INTERNAL_API_KEYS must be set in production")
            if any(len(k.get_secret_value()) < 24 for k in self.internal_api_keys):
                raise ValueError("INTERNAL_API_KEYS must be at least 24 characters in production")
            if self.staff_demo_password and self.staff_demo_password.get_secret_value():
                raise ValueError(
                    "STAFF_DEMO_PASSWORD is for demos only; unset it in production (staff use email sign-in)"
                )
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
    def demo_password(self) -> str | None:
        value = self.staff_demo_password.get_secret_value() if self.staff_demo_password else ""
        return value or None

    @property
    def n8n_webhook_key(self) -> str | None:
        """Key sent to n8n webhooks. Defaults to the primary internal key (shared service credential)."""
        if self.n8n_api_key and self.n8n_api_key.get_secret_value():
            return self.n8n_api_key.get_secret_value()
        return self.internal_api_keys[0].get_secret_value() if self.internal_api_keys else None

    @property
    def llm_api_key(self) -> str | None:
        value = self.mistral_api_key.get_secret_value().strip() if self.mistral_api_key else ""
        return value or None

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_secret_key.get_secret_value()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
