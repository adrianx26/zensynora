"""
Web Authentication — API key and admin access control.

Provides FastAPI dependencies for protecting sensitive endpoints.
"""

from fastapi import Header, HTTPException, status
from myclaw.config import load_config


async def require_admin_api_key(x_api_key: str = Header(..., description="Admin API key")):
    """Validate the admin API key sent in the X-API-Key header.

    Raises:
        HTTPException: 403 if the key is missing or invalid.
    """
    config = load_config()
    security = getattr(config, "security", None)
    expected = security.admin_api_key.get_secret_value() if security else ""

    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key not configured. Set security.admin_api_key in config.",
        )

    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return x_api_key
