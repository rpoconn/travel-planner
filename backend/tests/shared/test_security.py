from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shared.config import Settings
from shared.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


async def test_password_hash_round_trip() -> None:
    hashed = await hash_password("s3cret-password", rounds=4)

    assert hashed != "s3cret-password"
    assert await verify_password("s3cret-password", hashed)
    assert not await verify_password("wrong-password", hashed)


def test_token_round_trip(settings: Settings) -> None:
    user_id = uuid4()

    token = create_access_token(user_id, settings)

    assert decode_access_token(token, settings) == user_id


def test_expired_token_is_rejected(settings: Settings) -> None:
    long_ago = datetime.now(UTC) - timedelta(minutes=settings.jwt_expire_minutes + 1)
    token = create_access_token(uuid4(), settings, now=long_ago)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_token_signed_with_other_secret_is_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"jwt_secret": "a-completely-different-secret-value"})
    token = create_access_token(uuid4(), other)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_garbage_token_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-jwt", settings)
