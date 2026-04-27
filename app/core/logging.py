"""Logging configuration helpers."""

from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    """Configure application logging once at startup."""

    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
