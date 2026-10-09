"""Strukturiertes Logging (JSON auf stdout) mit Secret-Maskierung."""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Discord-Tokens: base64(user_id) . zeitstempel . hmac
_TOKEN_RE = re.compile(r"[\w-]{23,28}\.[\w-]{6,7}\.[\w-]{27,}")
_URL_PASSWORD_RE = re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)")
_MASK = "***"

_registered_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Meldet einen Wert an, der in keinem Log-Eintrag vorkommen darf."""
    if len(value) >= 6:
        _registered_secrets.add(value)


def mask(text: str) -> str:
    for secret in _registered_secrets:
        text = text.replace(secret, _MASK)
    text = _TOKEN_RE.sub(_MASK, text)
    return _URL_PASSWORD_RE.sub(rf"\1{_MASK}\2", text)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_value(v) for v in value]
    return value


def _redact(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    # Läuft nach dem Exception-Rendering, damit auch Stacktraces maskiert werden.
    return {k: _mask_value(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Bibliotheken (discord.py, sqlalchemy, uvicorn) laufen über stdlib-logging;
    # auch deren Ausgaben gehen durch dieselbe Maskierung.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_MaskingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class _MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
