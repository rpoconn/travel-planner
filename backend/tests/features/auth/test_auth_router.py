from fastapi import FastAPI
from httpx import AsyncClient

from shared.rate_limit import AUTH_BUCKET, RateLimiter
from tests.conftest import TEST_PASSWORD, RegisterUser

# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_returns_token_and_user(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "Ada@Example.com", "password": TEST_PASSWORD, "display_name": " Ada "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["display_name"] == "Ada"
    assert "password_hash" not in body["user"]


async def test_register_rejects_duplicate_email_case_insensitively(
    client: AsyncClient, register_user: RegisterUser
) -> None:
    await register_user("ada@example.com")

    response = await client.post(
        "/auth/register", json={"email": "ADA@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}


async def test_register_validates_input(client: AsyncClient) -> None:
    short = await client.post("/auth/register", json={"email": "a@b.co", "password": "short"})
    bad_email = await client.post(
        "/auth/register", json={"email": "not-an-email", "password": TEST_PASSWORD}
    )
    # 30 x 3-byte characters is under 72 characters but over bcrypt's 72-byte limit.
    too_many_bytes = await client.post(
        "/auth/register", json={"email": "a@b.co", "password": "€" * 30}
    )

    assert short.status_code == 422
    assert bad_email.status_code == 422
    assert too_many_bytes.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_with_correct_password(
    client: AsyncClient, register_user: RegisterUser
) -> None:
    await register_user("ada@example.com")

    response = await client.post(
        "/auth/login", json={"email": "Ada@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "ada@example.com"


async def test_login_with_wrong_password_or_unknown_email(
    client: AsyncClient, register_user: RegisterUser
) -> None:
    await register_user("ada@example.com")

    wrong_password = await client.post(
        "/auth/login", json={"email": "ada@example.com", "password": "nope-nope-nope"}
    )
    unknown_email = await client.post(
        "/auth/login", json={"email": "bob@example.com", "password": TEST_PASSWORD}
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    # Same message for both so the endpoint doesn't reveal which emails exist.
    assert wrong_password.json() == unknown_email.json()


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


async def test_me_returns_current_user(client: AsyncClient, register_user: RegisterUser) -> None:
    headers = await register_user("ada@example.com")

    response = await client.get("/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


async def test_me_requires_a_valid_token(client: AsyncClient) -> None:
    missing = await client.get("/auth/me")
    garbage = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert garbage.status_code == 401


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


async def test_login_and_register_share_a_per_ip_rate_limit(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.rate_limiters[AUTH_BUCKET] = RateLimiter(2)
    login = {"email": "nobody@example.com", "password": "wrong password"}

    assert (await client.post("/auth/login", json=login)).status_code == 401
    assert (await client.post("/auth/login", json=login)).status_code == 401
    limited = await client.post(
        "/auth/register", json={"email": "ada@example.com", "password": TEST_PASSWORD}
    )

    assert limited.status_code == 429
    assert limited.json()["detail"].startswith("Too many sign-in attempts.")
    assert int(limited.headers["retry-after"]) > 0
