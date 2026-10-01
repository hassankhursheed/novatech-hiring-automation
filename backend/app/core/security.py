"""Service-to-service authentication (n8n -> backend) with rotatable API keys."""

import hmac

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Internal service key")


def require_internal_api_key(
    api_key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.internal_api_keys:
        # Only possible outside production (enforced in Settings): allow local experimentation.
        return
    if not api_key:
        raise UnauthorizedError("missing X-API-Key header", code="API_KEY_MISSING")
    # Constant-time comparison against every active key (supports key rotation).
    if not any(hmac.compare_digest(api_key, k.get_secret_value()) for k in settings.internal_api_keys):
        raise UnauthorizedError("invalid API key", code="API_KEY_INVALID")
