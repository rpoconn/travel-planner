"""`CurrentUser` dependency (other features import this to require a logged-in user) and
the rate limit on login/register."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from features.auth import service
from features.auth.models import User
from shared.db import SessionDep
from shared.rate_limit import AUTH_BUCKET, client_ip, enforce_rate_limit
from shared.security import CurrentUserId


async def get_current_user(user_id: CurrentUserId, session: SessionDep) -> User:
    user = await service.get_user(session, user_id)
    if user is None:
        # Token is valid but the account no longer exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def limit_auth_attempts(request: Request) -> None:
    """Per-IP cap on login/register attempts (slows down password guessing)."""
    enforce_rate_limit(request, AUTH_BUCKET, client_ip(request), "Too many sign-in attempts.")
