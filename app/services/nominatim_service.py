"""HTTP client wrapper for the Nominatim geocoding service."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, UpstreamServiceError
from app.models.geocode import GeocodeMatch


class NominatimService:
    """Small Nominatim client used by API routes."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def geocode(self, query: str, limit: int | None = None) -> list[GeocodeMatch]:
        """Geocode a human-readable address into normalized matches."""

        params = {
            "q": query,
            "format": "jsonv2",
            "limit": limit or self.settings.nominatim_limit,
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": self.settings.nominatim_user_agent,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_s,
                headers=headers,
            ) as client:
                response = await client.get(self.settings.nominatim_search_url, params=params)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpstreamServiceError(
                f"Nominatim geocoding failed with status {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamServiceError("Failed to reach Nominatim geocoding service.") from exc

        payload = self._parse_json(response)

        if not payload:
            raise NotFoundError(f'No geocoding matches found for "{query}".')

        matches = [match for match in (self._normalize_match(item) for item in payload) if match]

        if not matches:
            raise NotFoundError(f'No geocoding matches found for "{query}".')

        return matches

    def _parse_json(self, response: httpx.Response) -> list[dict[str, Any]]:
        """Safely parse a Nominatim JSON payload."""

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamServiceError("Nominatim returned invalid JSON.") from exc

        if not isinstance(payload, list):
            raise UpstreamServiceError("Unexpected geocoding response format from Nominatim.")

        return [item for item in payload if isinstance(item, dict)]

    def _normalize_match(self, item: dict[str, Any]) -> GeocodeMatch | None:
        """Convert a raw Nominatim result into the public response shape."""

        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            return None

        importance_value = item.get("importance")
        importance = None
        if importance_value is not None:
            try:
                importance = float(importance_value)
            except (TypeError, ValueError):
                importance = None

        return GeocodeMatch(
            display_name=str(item.get("display_name", "")).strip() or "Unnamed result",
            lat=lat,
            lon=lon,
            importance=importance,
        )
