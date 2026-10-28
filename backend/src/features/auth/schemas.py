from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from shared.security import BCRYPT_MAX_PASSWORD_BYTES

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


def _check_password_bytes(value: str) -> str:
    # max_length counts characters; bcrypt's limit is in UTF-8 bytes.
    if len(value.encode()) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes")
    return value


Password = Annotated[
    str,
    Field(min_length=8, max_length=BCRYPT_MAX_PASSWORD_BYTES),
    AfterValidator(_check_password_bytes),
]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class LoginRequest(BaseModel):
    email: EmailStr
    # Only length-capped (not min_length) so old/short passwords get 401, not 422.
    password: Annotated[
        str,
        Field(min_length=1, max_length=BCRYPT_MAX_PASSWORD_BYTES),
        AfterValidator(_check_password_bytes),
    ]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int  # seconds
    user: UserRead
