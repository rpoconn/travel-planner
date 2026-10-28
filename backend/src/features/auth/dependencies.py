"""`CurrentUser` dependency: other features import this to require a logged-in user."""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from features.auth import service
from features.auth.models import User
from shared.db import SessionDep
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
