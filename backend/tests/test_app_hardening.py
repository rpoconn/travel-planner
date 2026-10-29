"""App-wide hardening: CORS policy, request body cap, production settings checks."""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from shared.config import Settings

ALLOWED_ORIGIN = "http://localhost:5173"


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


async def test_preflight_from_allowed_origin(client: AsyncClient) -> None:
    response = await client.options(
        "/plans",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert set(response.headers["access-control-allow-methods"].split(", ")) == {
        "GET",
        "POST",
        "PATCH",
        "DELETE",
    }


async def test_preflight_rejects_unknown_origins_methods_and_headers(
    client: AsyncClient,
) -> None:
    for origin, method, header in [
        ("https://evil.example", "POST", "content-type"),
        (ALLOWED_ORIGIN, "PUT", "content-type"),
        (ALLOWED_ORIGIN, "POST", "x-custom"),
    ]:
        response = await client.options(
            "/plans",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": header,
            },
        )
        assert response.status_code == 400, (origin, method, header)


async def test_simple_request_from_unknown_origin_gets_no_cors_header(
    client: AsyncClient,
) -> None:
    response = await client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Body size
# ---------------------------------------------------------------------------


async def test_declared_oversized_body_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login",
        content=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json", "Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]
    # Still readable by the browser.
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


async def test_streamed_oversized_body_is_rejected(client: AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"x" * 4096

    # A generator body has no Content-Length, so the limit is enforced while reading.
    response = await client.post(
        "/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


async def test_normal_bodies_pass(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Production settings
# ---------------------------------------------------------------------------


def test_production_requires_a_real_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(environment="production")
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(environment="production", jwt_secret="only-twenty-chars-xx")


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(environment="production", jwt_secret="s" * 32, cors_origins=["*"])


def test_production_accepts_safe_settings() -> None:
    settings = Settings(
        environment="production", jwt_secret="s" * 32, cors_origins=["https://trips.example"]
    )

    assert settings.environment == "production"
