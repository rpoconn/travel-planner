from fastapi import APIRouter

from features.health.schemas import HealthResponse

router = APIRouter(tags=["health"])

API_VERSION = "0.1.0"


@router.get("/health", operation_id="getHealth")
async def get_health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="ok", version=API_VERSION)
