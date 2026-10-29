"""Central logging configuration."""

import logging
import re

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Query parameters that carry a credential (SerpApi only takes its key as `api_key`).
_SECRET_PARAM = re.compile(r"(\bapi_key=)[^&\s\"'>]+")
# httpx logs every request's full URL at INFO; httpcore logs request details at DEBUG.
_HTTP_LOGGERS = ("httpx", "httpcore")


class RedactSecretsFilter(logging.Filter):
    """Masks credentials in the URLs HTTP libraries log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_PARAM.sub(r"\1[redacted]", message)
        if redacted != message:
            # Store the already-formatted message so the handler doesn't format it again.
            record.msg, record.args = redacted, None
        return True


def redact_http_secrets() -> None:
    """Install `RedactSecretsFilter` on the HTTP libraries' loggers (once)."""
    for name in _HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, RedactSecretsFilter) for existing in logger.filters):
            logger.addFilter(RedactSecretsFilter())


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; safe to call repeatedly (e.g. under uvicorn --reload)."""
    logging.basicConfig(level=level.upper(), format=_LOG_FORMAT, force=True)
    redact_http_secrets()
