"""HTTP client wrapper and normalization logic for Valhalla routing."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import UpstreamServiceError
from app.models.routing import (
    GeoJSONLineString,
    PolylinePayload,
    ResolvedLocation,
    RouteCandidate,
)
from app.services.polyline_utils import (
    decode_polyline6,
    to_arcgis_polyline_payload,
    to_geojson_linestring,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCATION_RADIUS_M = 50
DEFAULT_MINIMUM_REACHABILITY = 1
DEFAULT_RANK_CANDIDATES = True


class ValhallaService:
    """Small Valhalla client for pedestrian route generation."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def generate_routes(
        self,
        *,
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        mode: str = "pedestrian",
        alternatives: int = 1,
    ) -> list[RouteCandidate]:
        """Generate route candidates and normalize them around geometry output."""

        request_payload = self._build_route_request(
            origin,
            destination,
            mode=mode,
            alternatives=alternatives,
        )
        response_payload = await self._request_json_endpoint(
            self.settings.valhalla_route_url,
            payload=request_payload,
            endpoint_name="route",
        )

        routes = normalize_valhalla_route_response(response_payload)
        if not routes:
            raise UpstreamServiceError("Valhalla did not return any route candidates.")

        return routes[: max(alternatives, 1)]

    async def locate(
        self,
        *,
        locations: list[ResolvedLocation],
        mode: str = "pedestrian",
    ) -> Any:
        """Call Valhalla locate for prototype debugging."""

        request_payload = self._build_locate_request(locations, mode=mode)
        return await self._request_json_endpoint(
            self.settings.valhalla_locate_url,
            payload=request_payload,
            endpoint_name="locate",
            allow_list_response=True,
        )

    def _build_route_request(
        self,
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        *,
        mode: str,
        alternatives: int,
    ) -> dict[str, Any]:
        """Build a Valhalla route request payload for local testing."""

        return {
            "locations": [
                self._build_location(origin),
                self._build_location(destination),
            ],
            "costing": mode,
            "alternates": max(alternatives - 1, 0),
            "shape_format": "polyline6",
        }

    def _build_locate_request(
        self,
        locations: list[ResolvedLocation],
        mode: str,
    ) -> dict[str, Any]:
        """Build a Valhalla locate request payload using route-style snapping defaults."""

        return {
            "verbose": True,
            "locations": [self._build_location(location) for location in locations],
            "costing": mode,
        }

    def _build_location(self, location: ResolvedLocation) -> dict[str, Any]:
        """Build a Valhalla location with pedestrian-friendly snapping settings."""

        return {
            "lat": location.lat,
            "lon": location.lon,
            "radius": DEFAULT_LOCATION_RADIUS_M,
            "minimum_reachability": DEFAULT_MINIMUM_REACHABILITY,
            "rank_candidates": DEFAULT_RANK_CANDIDATES,
        }

    async def _request_json_endpoint(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        endpoint_name: str,
        allow_list_response: bool = False,
    ) -> Any:
        """Submit a GET request with a `json=` query parameter to Valhalla."""

        payload_string = json.dumps(payload, separators=(",", ":"))
        headers = {"Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_s) as client:
                response = await client.get(
                    url,
                    headers=headers,
                    params={"json": payload_string},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._build_http_status_error(
                endpoint_name=endpoint_name,
                payload=payload,
                response=exc.response,
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "Valhalla %s request failed before receiving a response. url=%s payload=%s error=%s",
                endpoint_name,
                url,
                payload_string,
                str(exc),
            )
            details = None
            if self.settings.prototype_mode:
                details = {
                    "endpoint": endpoint_name,
                    "url": url,
                    "payload": payload,
                    "upstream_error": str(exc),
                }
            raise UpstreamServiceError(
                "Failed to reach Valhalla routing service.",
                details=details,
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "Valhalla %s returned invalid JSON. url=%s payload=%s response=%s",
                endpoint_name,
                str(response.request.url),
                payload_string,
                response.text,
            )
            details = None
            if self.settings.prototype_mode:
                details = {
                    "endpoint": endpoint_name,
                    "url": str(response.request.url),
                    "payload": payload,
                    "response_text": response.text,
                }
            raise UpstreamServiceError("Valhalla returned invalid JSON.", details=details) from exc

        if allow_list_response and isinstance(data, list):
            return data

        if not isinstance(data, dict):
            raise UpstreamServiceError(
                "Unexpected response format from Valhalla.",
                details=data if self.settings.prototype_mode else None,
            )

        return data

    def _build_http_status_error(
        self,
        *,
        endpoint_name: str,
        payload: dict[str, Any],
        response: httpx.Response,
    ) -> UpstreamServiceError:
        """Map a Valhalla HTTP error into a cleaner application error."""

        raw_text = response.text
        upstream_error = _extract_valhalla_error_text(raw_text)
        request_url = str(response.request.url)

        logger.error(
            "Valhalla %s request failed. url=%s payload=%s status_code=%s response=%s",
            endpoint_name,
            request_url,
            json.dumps(payload, separators=(",", ":")),
            response.status_code,
            raw_text,
        )

        message = f"Valhalla {endpoint_name} request failed."
        if "No suitable edges near location" in upstream_error:
            message = (
                "Valhalla could not snap one or both locations to a pedestrian network edge."
            )

        details = None
        if self.settings.prototype_mode:
            details = {
                "endpoint": endpoint_name,
                "url": request_url,
                "payload": payload,
                "status_code": response.status_code,
                "response_text": raw_text,
                "upstream_error": upstream_error,
            }

        return UpstreamServiceError(message, details=details)


def _extract_valhalla_error_text(raw_text: str) -> str:
    """Extract the most useful Valhalla error string from a raw error payload."""

    try:
        payload = json.loads(raw_text)
    except ValueError:
        return raw_text.strip()

    if not isinstance(payload, dict):
        return raw_text.strip()

    error_value = payload.get("error")
    if isinstance(error_value, str) and error_value.strip():
        return error_value.strip()

    return raw_text.strip()


def normalize_valhalla_route_response(payload: dict[str, Any]) -> list[RouteCandidate]:
    """Normalize one or more Valhalla trip payloads into route candidates."""

    trip_candidates = _extract_trip_candidates(payload)
    routes: list[RouteCandidate] = []

    for index, trip in enumerate(trip_candidates, start=1):
        route = _build_route_candidate(route_id=f"route-{index}", trip=trip)
        if route is not None:
            routes.append(route)

    return routes


def _extract_trip_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the primary trip plus any future alternate trip structures."""

    trips: list[dict[str, Any]] = []

    primary_trip = payload.get("trip")
    if isinstance(primary_trip, dict):
        trips.append(primary_trip)

    alternates = payload.get("alternates") or payload.get("alternate_trips") or []
    if isinstance(alternates, list):
        for item in alternates:
            if not isinstance(item, dict):
                continue
            nested_trip = item.get("trip")
            if isinstance(nested_trip, dict):
                trips.append(nested_trip)
            else:
                trips.append(item)

    return trips


def _build_route_candidate(route_id: str, trip: dict[str, Any]) -> RouteCandidate | None:
    """Build a geometry-first route candidate from a Valhalla trip object."""

    encoded_polyline = _extract_encoded_polyline(trip)
    if not encoded_polyline:
        return None

    decoded_shape = _decode_trip_shape(trip, fallback_shape=encoded_polyline)
    if not decoded_shape:
        return None

    summary = _extract_summary(trip)
    geojson = GeoJSONLineString(**to_geojson_linestring(decoded_shape))
    polyline_payload = PolylinePayload(**to_arcgis_polyline_payload(decoded_shape))

    return RouteCandidate(
        route_id=route_id,
        distance_m=_summary_distance_m(summary),
        duration_s=_summary_duration_s(summary),
        encoded_polyline=encoded_polyline,
        decoded_shape=decoded_shape,
        geojson=geojson,
        polyline_payload=polyline_payload,
        summary=summary,
    )


def _extract_encoded_polyline(trip: dict[str, Any]) -> str | None:
    """Find the encoded polyline for a trip or its first leg."""

    trip_shape = trip.get("shape")
    if isinstance(trip_shape, str) and trip_shape:
        return trip_shape

    for leg in trip.get("legs", []):
        if isinstance(leg, dict):
            leg_shape = leg.get("shape")
            if isinstance(leg_shape, str) and leg_shape:
                return leg_shape

    return None


def _decode_trip_shape(trip: dict[str, Any], fallback_shape: str) -> list[list[float]]:
    """Decode and merge leg shapes while preserving [lon, lat] ordering."""

    legs = [leg for leg in trip.get("legs", []) if isinstance(leg, dict)]
    if not legs:
        return decode_polyline6(fallback_shape)

    merged_coordinates: list[list[float]] = []
    for leg in legs:
        shape = leg.get("shape")
        if not isinstance(shape, str) or not shape:
            continue
        decoded_leg = decode_polyline6(shape)
        if not merged_coordinates:
            merged_coordinates.extend(decoded_leg)
            continue
        if decoded_leg and merged_coordinates[-1] == decoded_leg[0]:
            merged_coordinates.extend(decoded_leg[1:])
        else:
            merged_coordinates.extend(decoded_leg)

    return merged_coordinates


def _extract_summary(trip: dict[str, Any]) -> dict[str, Any]:
    """Extract a raw summary payload and synthesize one from legs if needed."""

    summary = trip.get("summary")
    if isinstance(summary, dict):
        return summary

    total_length_km = 0.0
    total_time_s = 0.0
    for leg in trip.get("legs", []):
        if not isinstance(leg, dict):
            continue
        leg_summary = leg.get("summary")
        if not isinstance(leg_summary, dict):
            continue
        total_length_km += _coerce_float(leg_summary.get("length"))
        total_time_s += _coerce_float(leg_summary.get("time"))

    return {"length": total_length_km, "time": total_time_s}


def _summary_distance_m(summary: dict[str, Any]) -> float:
    """Convert Valhalla summary length in kilometers to meters."""

    return _coerce_float(summary.get("length")) * 1000.0


def _summary_duration_s(summary: dict[str, Any]) -> float:
    """Return Valhalla summary duration in seconds."""

    return _coerce_float(summary.get("time"))


def _coerce_float(value: Any) -> float:
    """Safely convert values to float for normalized route summaries."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
