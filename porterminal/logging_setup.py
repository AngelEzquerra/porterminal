"""Logging setup for the Porterminal server."""

from __future__ import annotations

import logging
import os


class CleanFormatter(logging.Formatter):
    """Clean log formatter with minimal output."""

    FORMATS = {
        logging.DEBUG: "\033[90m[DEBUG]\033[0m %(message)s",
        logging.INFO: "\033[36m[INFO]\033[0m %(message)s",
        logging.WARNING: "\033[33m[WARN]\033[0m %(message)s",
        logging.ERROR: "\033[31m[ERROR]\033[0m %(message)s",
        logging.CRITICAL: "\033[31;1m[CRIT]\033[0m %(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        fmt = self.FORMATS.get(record.levelno, "[%(levelname)s] %(message)s")
        formatter = logging.Formatter(fmt)
        return formatter.format(record)


def setup_logging_from_env() -> None:
    """Configure root logging with clean format."""
    level_name = (os.environ.get("PORTERMINAL_LOG_LEVEL") or "info").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Add clean handler
    handler = logging.StreamHandler()
    handler.setFormatter(CleanFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # Set level for app loggers
    logging.getLogger("src").setLevel(level)

    # Suppress noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
