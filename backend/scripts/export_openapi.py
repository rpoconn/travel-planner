"""Write the FastAPI OpenAPI spec to disk for @hey-api/openapi-ts.

Usage: `uv run python scripts/export_openapi.py [output_path]`
Default output: `backend/openapi.json`.
"""

import json
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# `src/` is the import root (see pyproject.toml); make it importable when run as a script.
sys.path.insert(0, str(BACKEND_DIR / "src"))

from main import app  # noqa: E402

logger = logging.getLogger("export_openapi")


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND_DIR / "openapi.json"
    spec = app.openapi()
    output.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote OpenAPI spec to %s", output)


if __name__ == "__main__":
    main()
