"""User registration, authentication, and token issuing."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from features.auth.models import User
from features.auth.schemas import RegisterRequest, TokenResponse, UserRead
from shared.config import Settings
from shared.security import create_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)


class EmailAlreadyRegisteredError(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def register_user(session: AsyncSession, data: RegisterRequest, bcrypt_rounds: int) -> User:
    email = normalize_email(data.email)
    if await get_user_by_email(session, email) is not None:
        raise EmailAlreadyRegisteredError(email)

    user = User(
        email=email,
        display_name=data.display_name,
        password_hash=await hash_password(data.password, bcrypt_rounds),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Lost a race with a concurrent registration for the same email.
        await session.rollback()
        raise EmailAlreadyRegisteredError(email) from exc

    logger.info("Registered user %s", user.id)
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None or not await verify_password(password, user.password_hash):
        return None
    return user


def issue_token(user: User, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, settings),
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )
