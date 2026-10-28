"""FastAPI application factory.

Run locally with: `uv run uvicorn main:app --app-dir src --reload`
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from features.health.router import API_VERSION
from features.health.router import router as health_router
from shared.config import Settings, get_settings
from shared.logging import configure_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAPI helpers
# ---------------------------------------------------------------------------


def _operation_id(route: APIRoute) -> str:
    # Routes set explicit camelCase operation_ids; fall back to the function name so the
    # @hey-api generated SDK gets stable, readable method names either way.
    return route.name


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=API_VERSION,
        generate_unique_id_function=_operation_id,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)

    logger.info(
        "App created (environment=%s, model=%s)", settings.environment, settings.ollama_model
    )
    return app


app = create_app()
