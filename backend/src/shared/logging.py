"""Central logging configuration."""

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; safe to call repeatedly (e.g. under uvicorn --reload)."""
    logging.basicConfig(level=level.upper(), format=_LOG_FORMAT, force=True)
