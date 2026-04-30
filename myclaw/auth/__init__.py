"""Authentication primitives — JWT verification + OAuth callback handling.

These are pure building blocks. Wiring them into the FastAPI app lives in
``myclaw/api_server.py`` and is up to the operator (we don't force one
provider). The module imports cleanly without ``PyJWT`` installed; using
the helpers without the dep raises ``RuntimeError`` rather than crashing
on import.
"""

from .jwt_auth import (
    JWTAuthenticator,
    JWTVerificationError,
    AuthenticatedPrincipal,
)
from .oauth import (
    OAuth2Config,
    OAuth2CallbackHandler,
    OAuthFlowError,
)

__all__ = [
    "JWTAuthenticator",
    "JWTVerificationError",
    "AuthenticatedPrincipal",
    "OAuth2Config",
    "OAuth2CallbackHandler",
    "OAuthFlowError",
]
