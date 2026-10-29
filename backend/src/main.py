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
from features.chat.dependencies import create_llm
from features.chat.router import router as chat_router
from features.health.router import API_VERSION
from features.health.router import router as health_router
from features.itinerary.router import router as itinerary_router
from features.plans.router import router as plans_router
from shared.config import Settings, get_settings
from shared.db import create_engine, create_sessionmaker
from shared.logging import configure_logging
from shared.middleware import BodySizeLimitMiddleware
from shared.migrations import run_migrations
from shared.rate_limit import AUTH_BUCKET, CHAT_BUCKET, create_rate_limiters
from shared.search.provider import create_search_provider

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
    llm = create_llm(settings)
    search = create_search_provider(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await run_migrations(engine)
        yield
        await _app.state.llm.aclose()
        await _app.state.search.aclose()
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
    # Read by the `LLMDep` dependency; tests replace it with a scripted FakeLLM.
    app.state.llm = llm
    # Read by the `SearchDep` dependency; tests replace it with a `FakeSearchProvider`.
    app.state.search = search

    # Read by `enforce_rate_limit`; buckets with a limit of 0 are left out (unlimited).
    app.state.rate_limiters = create_rate_limiters(
        {
            CHAT_BUCKET: settings.chat_rate_limit_per_minute,
            AUTH_BUCKET: settings.auth_rate_limit_per_minute,
        }
    )

    # The last middleware added runs first: CORS wraps everything so even a 413 carries
    # CORS headers and the browser can show its message.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Auth is a bearer header, not cookies, so credentialed requests aren't needed.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        # Last-Event-ID: sent by the SDK's SSE client if it ever resumes a stream.
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        max_age=600,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(plans_router)
    app.include_router(itinerary_router)
    app.include_router(chat_router)

    logger.info(
        "App created (environment=%s, llm=%s, model=%s, search=%s)",
        settings.environment,
        settings.llm_provider,
        settings.ollama_model,
        type(search).__name__,
    )
    return app


app = create_app()
