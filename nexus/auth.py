"""
Azure AD / Entra ID JWT authentication for NEXUS.

All API routes (except /health) require a valid Bearer token issued by the
configured Azure AD tenant for the NEXUS app registration.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.utils import base64url_decode
import json, base64

from nexus.config import settings

log = structlog.get_logger(__name__)

bearer_scheme = HTTPBearer()

# Simple in-process JWKS cache — refreshed every hour
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600  # seconds


async def _get_jwks() -> dict[str, Any]:
    global _jwks_cache, _jwks_fetched_at
    if time.time() - _jwks_fetched_at > _JWKS_TTL:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.jwks_uri, timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = time.time()
            log.info("jwks_refreshed")
    return _jwks_cache


def _get_signing_key(jwks: dict[str, Any], kid: str) -> str:
    """Return the PEM public key for the given key ID."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            # python-jose can use the raw JWK dict directly
            return key
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found")


class CurrentUser:
    def __init__(self, oid: str, name: str, email: str | None, roles: list[str]):
        self.oid = oid          # Azure AD object ID — stable identifier
        self.name = name
        self.email = email
        self.roles = roles

    @property
    def is_facilitator(self) -> bool:
        return "Nexus.Facilitator" in self.roles or "Nexus.Admin" in self.roles

    @property
    def is_admin(self) -> bool:
        return "Nexus.Admin" in self.roles


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Decode header to get kid without verifying signature yet
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise credentials_exception

        jwks = await _get_jwks()
        signing_key = _get_signing_key(jwks, kid)

        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.azure_client_id,
            issuer=settings.token_issuer,
            options={"verify_at_hash": False},
        )

        oid: str = payload.get("oid") or payload.get("sub")
        if not oid:
            raise credentials_exception

        name: str = payload.get("name", "Unknown")
        email: str | None = payload.get("preferred_username") or payload.get("email")
        # App roles are in the `roles` claim when assigned via Azure AD app roles
        roles: list[str] = payload.get("roles", [])

        return CurrentUser(oid=oid, name=name, email=email, roles=roles)

    except JWTError as exc:
        log.warning("jwt_validation_failed", error=str(exc))
        raise credentials_exception from exc


def require_facilitator(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_facilitator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Facilitator role required")
    return user
