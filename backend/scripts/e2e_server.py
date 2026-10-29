"""Run the API for the Playwright E2E suite: offline LLM + search, a fresh database each run.

Usage: `uv run python scripts/e2e_server.py [--port 8765] [--cors-origin http://localhost:5174]`
(started by `frontend/playwright.config.ts`; there's normally no need to run it by hand).
"""

import argparse
import logging
import os
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Gitignored (`backend/*.db`); deleted on every start so runs don't see each other's data.
E2E_DB = BACKEND_DIR / "e2e.db"

logger = logging.getLogger("e2e_server")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cors-origin", default="http://localhost:5174")
    args = parser.parse_args()

    for path in (E2E_DB, E2E_DB.with_name(f"{E2E_DB.name}-journal")):
        path.unlink(missing_ok=True)

    # Environment variables beat `.env`, so a developer's local settings can't leak in.
    os.environ.update(
        {
            "ENVIRONMENT": "test",
            "DATABASE_URL": f"sqlite+aiosqlite:///{E2E_DB.as_posix()}",
            "LLM_PROVIDER": "fake",
            "SEARCH_PROVIDER": "fake",
            "CORS_ORIGINS": f'["{args.cors_origin}"]',
            "BCRYPT_ROUNDS": "4",
            "JWT_SECRET": "e2e-only-secret-that-is-at-least-32-bytes",
            "LOG_LEVEL": "WARNING",
            # Every E2E test registers a user from 127.0.0.1; keep the per-IP limit out of it.
            "AUTH_RATE_LIMIT_PER_MINUTE": "0",
        }
    )
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    logger.info("Starting E2E API on :%d (database %s)", args.port, E2E_DB)
    uvicorn.run("main:app", app_dir=str(BACKEND_DIR / "src"), port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
