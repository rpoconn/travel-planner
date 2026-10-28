from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


async def test_openapi_spec_exposes_health_operation(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    operation = response.json()["paths"]["/health"]["get"]
    assert operation["operationId"] == "getHealth"
