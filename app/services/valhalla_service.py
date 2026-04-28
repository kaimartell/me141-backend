"""HTTP client wrapper and normalization logic for Valhalla routing."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
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
MAX_INTERNAL_ROUTE_CANDIDATES = 8
MAX_RETURNED_ROUTE_CANDIDATES = 3
ROUTE_SIMILARITY_SAMPLE_COUNT = 12
ROUTE_SIMILARITY_AVG_DISTANCE_M = 12.0
ROUTE_SIMILARITY_MAX_DISTANCE_M = 35.0
ROUTE_SIMILARITY_DISTANCE_RATIO = 0.04
ROUTE_SIMILARITY_DURATION_RATIO = 0.08


@dataclass(frozen=True)
class RouteGenerationDiagnostics:
    """Internal route generation diagnostics for logs and tests."""

    requested_alternatives: int
    internal_candidate_target: int
    raw_candidate_count: int
    distinct_candidate_count: int
    returned_route_count: int


@dataclass(frozen=True)
class RouteGenerationResult:
    """Generated route candidates plus internal diversity diagnostics."""

    routes: list[RouteCandidate]
    distinct_candidates: list[RouteCandidate]
    diagnostics: RouteGenerationDiagnostics


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

        result = await self.generate_route_candidates(
            origin=origin,
            destination=destination,
            mode=mode,
            alternatives=alternatives,
        )
        return result.routes

    async def generate_route_candidates(
        self,
        *,
        origin: ResolvedLocation,
        destination: ResolvedLocation,
        mode: str = "pedestrian",
        alternatives: int = 1,
    ) -> RouteGenerationResult:
        """Generate a larger internal candidate pool and collapse near duplicates."""

        internal_candidate_target = self._internal_candidate_target(alternatives)
        request_payload = self._build_route_request(
            origin,
            destination,
            mode=mode,
            alternatives=internal_candidate_target,
        )
        response_payload = await self._request_json_endpoint(
            self.settings.valhalla_route_url,
            payload=request_payload,
            endpoint_name="route",
        )

        routes = normalize_valhalla_route_response(response_payload)
        if not routes:
            raise UpstreamServiceError("Valhalla did not return any route candidates.")

        distinct_routes = dedupe_route_candidates(routes)
        returned_route_count = min(
            len(distinct_routes),
            public_route_return_count(alternatives),
        )
        diagnostics = RouteGenerationDiagnostics(
            requested_alternatives=alternatives,
            internal_candidate_target=internal_candidate_target,
            raw_candidate_count=len(routes),
            distinct_candidate_count=len(distinct_routes),
            returned_route_count=returned_route_count,
        )
        logger.info(
            "Valhalla route candidates generated. requested_alternatives=%s "
            "internal_candidate_target=%s raw_candidate_count=%s "
            "distinct_candidate_count=%s returned_route_count=%s",
            diagnostics.requested_alternatives,
            diagnostics.internal_candidate_target,
            diagnostics.raw_candidate_count,
            diagnostics.distinct_candidate_count,
            diagnostics.returned_route_count,
        )

        return RouteGenerationResult(
            routes=distinct_routes[:returned_route_count],
            distinct_candidates=distinct_routes,
            diagnostics=diagnostics,
        )

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

    def _internal_candidate_target(self, requested_alternatives: int) -> int:
        """Return the Valhalla candidate count to request internally."""

        configured_count = max(self.settings.valhalla_internal_candidate_count, 1)
        requested_count = max(requested_alternatives, 1)
        return min(
            max(configured_count, requested_count),
            MAX_INTERNAL_ROUTE_CANDIDATES,
        )

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


def public_route_return_count(requested_alternatives: int) -> int:
    """Clamp public route output to a small useful set."""

    return min(max(requested_alternatives, 1), MAX_RETURNED_ROUTE_CANDIDATES)


def dedupe_route_candidates(routes: list[RouteCandidate]) -> list[RouteCandidate]:
    """Collapse route candidates that are effectively the same geometry."""

    distinct_routes: list[RouteCandidate] = []
    for route in routes:
        if any(_routes_are_similar(route, kept) for kept in distinct_routes):
            logger.info(
                "Dropping near-duplicate Valhalla route. route_id=%s kept_count=%s",
                route.route_id,
                len(distinct_routes),
            )
            continue
        distinct_routes.append(route)
    return distinct_routes


def _routes_are_similar(route_a: RouteCandidate, route_b: RouteCandidate) -> bool:
    """Return true when two routes are too similar to keep separately."""

    if route_a.encoded_polyline == route_b.encoded_polyline:
        return True

    if not route_a.decoded_shape or not route_b.decoded_shape:
        return False

    if not _relative_values_are_close(
        route_a.distance_m,
        route_b.distance_m,
        ROUTE_SIMILARITY_DISTANCE_RATIO,
    ):
        return False

    if not _relative_values_are_close(
        route_a.duration_s,
        route_b.duration_s,
        ROUTE_SIMILARITY_DURATION_RATIO,
    ):
        return False

    bbox_distance_m = _bbox_center_distance_m(route_a.decoded_shape, route_b.decoded_shape)
    if bbox_distance_m > ROUTE_SIMILARITY_MAX_DISTANCE_M:
        return False

    sampled_a = _sample_shape(route_a.decoded_shape, ROUTE_SIMILARITY_SAMPLE_COUNT)
    sampled_b = _sample_shape(route_b.decoded_shape, ROUTE_SIMILARITY_SAMPLE_COUNT)
    distances = [
        _haversine_m(point_a[1], point_a[0], point_b[1], point_b[0])
        for point_a, point_b in zip(sampled_a, sampled_b)
    ]
    if not distances:
        return False

    avg_distance_m = sum(distances) / len(distances)
    max_distance_m = max(distances)
    return (
        avg_distance_m <= ROUTE_SIMILARITY_AVG_DISTANCE_M
        and max_distance_m <= ROUTE_SIMILARITY_MAX_DISTANCE_M
    )


def _relative_values_are_close(value_a: float, value_b: float, ratio: float) -> bool:
    """Compare values with a relative tolerance and zero-safe fallback."""

    largest = max(abs(value_a), abs(value_b), 1.0)
    return abs(value_a - value_b) / largest <= ratio


def _sample_shape(
    coordinates: list[list[float]],
    sample_count: int,
) -> list[list[float]]:
    """Sample route coordinates evenly by index while preserving endpoints."""

    if len(coordinates) <= sample_count:
        return [list(point) for point in coordinates]

    last_index = len(coordinates) - 1
    step = last_index / (sample_count - 1)
    indexes = [round(index * step) for index in range(sample_count)]
    indexes[0] = 0
    indexes[-1] = last_index
    return [list(coordinates[index]) for index in indexes]


def _bbox_center_distance_m(
    coordinates_a: list[list[float]],
    coordinates_b: list[list[float]],
) -> float:
    """Return distance between route bounding-box centers."""

    center_a = _bbox_center(coordinates_a)
    center_b = _bbox_center(coordinates_b)
    return _haversine_m(center_a[1], center_a[0], center_b[1], center_b[0])


def _bbox_center(coordinates: list[list[float]]) -> list[float]:
    """Return [lon, lat] center of a coordinate bounding box."""

    lons = [point[0] for point in coordinates]
    lats = [point[1] for point in coordinates]
    return [
        (min(lons) + max(lons)) / 2.0,
        (min(lats) + max(lats)) / 2.0,
    ]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return haversine distance in meters."""

    radius_m = 6_371_000.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_m * c


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
