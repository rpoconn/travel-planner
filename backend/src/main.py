"""FastAPI application factory.

Run locally with: `uv run uvicorn main:app --app-dir src --reload`
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from features.auth.router import router as auth_router
from features.health.router import API_VERSION
from features.health.router import router as health_router
from features.plans.router import router as plans_router
from shared.config import Settings, get_settings
from shared.db import create_engine, create_sessionmaker, init_db
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

    # Creating the engine doesn't connect; the first connection happens in the lifespan.
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await init_db(engine)
        yield
        await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=API_VERSION,
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    # Read by the `SettingsDep` / `SessionDep` dependencies, so tests can inject their own.
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(plans_router)

    logger.info(
        "App created (environment=%s, model=%s)", settings.environment, settings.ollama_model
    )
    return app


app = create_app()
