"""Shared logger. Plain formatting is fine for v1; swap for structlog/JSON
in production if you add a log aggregator."""

import logging

from app.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(settings.log_level.upper())
    return logger
