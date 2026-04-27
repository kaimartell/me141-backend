"""Pydantic models for geocoding requests and responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GeocodeRequest(BaseModel):
    """Request payload for a geocoding lookup."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, description="Human-readable address query.")

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        """Reject blank geocoding queries after trimming whitespace."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned


class GeocodeMatch(BaseModel):
    """Normalized Nominatim geocode result."""

    display_name: str
    lat: float
    lon: float
    importance: float | None = None
    source: Literal["nominatim"] = "nominatim"


class GeocodeResponse(BaseModel):
    """Response payload for a geocoding lookup."""

    query: str
    matches: list[GeocodeMatch]
