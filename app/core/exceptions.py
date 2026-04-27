"""Shared application exceptions for clean API error handling."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base application exception with HTTP status metadata."""

    status_code = 500
    error_type = "application_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if error_type is not None:
            self.error_type = error_type


class BadRequestError(AppError):
    """Raised when a request is structurally valid JSON but semantically invalid."""

    status_code = 400
    error_type = "bad_request"


class NotFoundError(AppError):
    """Raised when a requested resource cannot be resolved."""

    status_code = 404
    error_type = "not_found"


class UpstreamServiceError(AppError):
    """Raised when an upstream integration fails or returns invalid data."""

    status_code = 502
    error_type = "upstream_service_error"
