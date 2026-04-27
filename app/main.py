"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.geocode import router as geocode_router
from app.api.routes import router as routes_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Pedestrian Routing Prototype",
    version="0.1.0",
    description="Prototype backend for address geocoding and pedestrian route generation.",
)

app.include_router(geocode_router)
app.include_router(routes_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal health check response."""

    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return FastAPI validation errors as HTTP 400 instead of 422."""

    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "type": "validation_error",
                "message": "Invalid request.",
                "details": [_format_validation_error(error) for error in exc.errors()],
            }
        },
    )


@app.exception_handler(AppError)
async def app_error_exception_handler(request: Request, exc: AppError) -> JSONResponse:
    """Return shared application errors as clean JSON payloads."""

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": exc.error_type,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe error response for unexpected exceptions."""

    logger.exception("Unhandled application error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "internal_server_error",
                "message": "An unexpected error occurred.",
            }
        },
    )


def _format_validation_error(error: dict[str, Any]) -> dict[str, str]:
    """Flatten FastAPI validation details into a simpler JSON shape."""

    location = ".".join(str(item) for item in error.get("loc", []))
    message = str(error.get("msg", "Invalid value"))
    return {"field": location, "message": message}
