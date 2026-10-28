"""Password hashing, JWT issuing/decoding, and the bearer-token dependency.

Loading the full `User` row lives in `features.auth.dependencies`; this module only knows
about user ids so `shared/` never depends on a feature.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.config import Settings, SettingsDep

logger = logging.getLogger(__name__)

# bcrypt only looks at the first 72 bytes and bcrypt>=5 raises on longer input.
BCRYPT_MAX_PASSWORD_BYTES = 72


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def _hash_password_sync(password: str, rounds: int) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def _verify_password_sync(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def hash_password(password: str, rounds: int) -> str:
    # bcrypt is deliberately slow and CPU-bound; keep it off the event loop.
    return await asyncio.to_thread(_hash_password_sync, password, rounds)


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, password, password_hash)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class InvalidTokenError(Exception):
    """Token is malformed, expired, signed with another key, or has a bad subject."""


def create_access_token(subject: UUID, settings: Settings, now: datetime | None = None) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        return UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

# auto_error=False so a missing header yields our 401 (with WWW-Authenticate), not a bare 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: SettingsDep,
) -> UUID:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        return decode_access_token(credentials.credentials, settings)
    except InvalidTokenError as exc:
        logger.info("Rejected bearer token: %s", exc)
        raise _unauthorized("Invalid or expired token") from exc


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
