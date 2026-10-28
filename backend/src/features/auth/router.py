from fastapi import APIRouter, HTTPException, status

from features.auth import service
from features.auth.dependencies import CurrentUser
from features.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserRead
from shared.config import SettingsDep
from shared.db import SessionDep
from shared.errors import error_responses

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    operation_id="register",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(status.HTTP_409_CONFLICT),
)
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Create an account and return an access token (the user is logged in immediately)."""
    try:
        user = await service.register_user(session, body, settings.bcrypt_rounds)
    except service.EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        ) from exc
    return service.issue_token(user, settings)


@router.post(
    "/login",
    operation_id="login",
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    """Exchange email + password for an access token."""
    user = await service.authenticate_user(session, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    return service.issue_token(user, settings)


@router.get(
    "/me",
    operation_id="getMe",
    responses=error_responses(status.HTTP_401_UNAUTHORIZED),
)
async def get_me(user: CurrentUser) -> UserRead:
    """The currently authenticated user."""
    return UserRead.model_validate(user)
