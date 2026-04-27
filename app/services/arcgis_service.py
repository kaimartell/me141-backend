"""ArcGIS helpers for geometry-first corridor queries."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import UpstreamServiceError
from app.models.routing import PolylinePayload
from app.services.polyline_utils import to_arcgis_polyline_payload

logger = logging.getLogger(__name__)

REST_STOP_OUT_FIELDS = ",".join(
    [
        "objectid",
        "globalid",
        "what_kind_of_rest_stop_is_this",
        "rest_quality",
        "CreationDate",
        "EditDate",
    ]
)


class ArcGISService:
    """Small ArcGIS query client for route corridor inspection."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def generate_polyline(self, route_points: list[list[float]]) -> PolylinePayload:
        """Convert [lon, lat] route points into an ArcGIS polyline payload."""

        return PolylinePayload.model_validate(to_arcgis_polyline_payload(route_points))

    def arcgis_intersects_params(
        self,
        route_points: list[list[float]] | None = None,
        *,
        polyline_payload: PolylinePayload | None = None,
        out_fields: str = "*",
        where: str = "1=1",
        return_geometry: bool = True,
        distance_m: float | None = None,
    ) -> dict[str, str]:
        """Build standard ArcGIS corridor-intersects query params."""

        polyline = polyline_payload or self.generate_polyline(route_points or [])
        payload = polyline.model_dump()
        corridor_distance = (
            self.settings.arcgis_corridor_distance_m
            if distance_m is None
            else distance_m
        )

        return {
            "where": where,
            "geometry": json.dumps(payload, separators=(",", ":")),
            "geometryType": "esriGeometryPolyline",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": str(corridor_distance),
            "units": "esriSRUnit_Meter",
            "inSR": "4326",
            "outSR": "4326",
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "f": "json",
        }

    async def query_pois(self, polyline_payload: PolylinePayload) -> list[dict[str, Any]]:
        """Query obstacle POIs intersecting the route corridor."""

        return await self._query_features(
            self.settings.arcgis_poi_url,
            params=self.arcgis_intersects_params(polyline_payload=polyline_payload),
        )

    async def query_rest_stops(
        self,
        polyline_payload: PolylinePayload,
    ) -> dict[str, Any]:
        """Query and normalize live rest-stop records near the route corridor."""

        if not self.settings.arcgis_rest_stop_url:
            return {
                "rest_stops": [],
                "raw_feature_count": 0,
                "source_status": _build_rest_stop_source_status(
                    configured=False,
                    queried=False,
                    authenticated=False,
                    available=False,
                    reason="Rest-stop URL not configured",
                ),
            }

        token_configured = bool(self.settings.arcgis_rest_stop_token)
        try:
            params = self.arcgis_intersects_params(
                polyline_payload=polyline_payload,
                out_fields=REST_STOP_OUT_FIELDS,
                return_geometry=True,
            )
            if token_configured:
                params["token"] = self.settings.arcgis_rest_stop_token

            features = await self._query_features(
                self.settings.arcgis_rest_stop_url,
                params=params,
                normalizer=self._normalize_rest_stop_feature,
            )
        except UpstreamServiceError as exc:
            reason = _rest_stop_failure_reason(exc)
            status_code = _upstream_status_code(exc)
            response_text = _upstream_response_text(exc)
            logger.warning(
                "ArcGIS rest-stop query unavailable. url=%s token_configured=%s status_code=%s reason=%s response=%s",
                self.settings.arcgis_rest_stop_url,
                token_configured,
                status_code,
                reason,
                response_text,
            )
            return {
                "rest_stops": [],
                "raw_feature_count": 0,
                "source_status": _build_rest_stop_source_status(
                    configured=True,
                    queried=True,
                    authenticated=token_configured,
                    available=False,
                    reason=reason,
                ),
            }

        if features:
            reason = "Success"
        else:
            reason = "No nearby rest stops found"

        return {
            "rest_stops": features,
            "raw_feature_count": len(features),
            "source_status": _build_rest_stop_source_status(
                configured=True,
                queried=True,
                authenticated=token_configured,
                available=True,
                reason=reason,
            ),
        }

    async def query_basemap_layer(
        self,
        polyline_payload: PolylinePayload,
        *,
        layer_id: int,
        out_fields: str = "*",
    ) -> list[dict[str, Any]]:
        """Query a specific Tufts basemap layer by numeric layer id."""

        url = f"{self.settings.arcgis_basemap_service_url.rstrip('/')}/{layer_id}/query"
        return await self._query_features(
            url,
            params=self.arcgis_intersects_params(
                polyline_payload=polyline_payload,
                out_fields=out_fields,
            ),
        )

    async def classify_route_surface(
        self,
        polyline_payload: PolylinePayload,
    ) -> dict[str, Any]:
        """Approximate route surface exposure from intersecting basemap features."""

        gravel_features, sidewalk_features, path_features = await asyncio.gather(
            self.query_basemap_layer(
                polyline_payload,
                layer_id=self.settings.arcgis_gravel_layer_id,
            ),
            self.query_basemap_layer(
                polyline_payload,
                layer_id=self.settings.arcgis_sidewalk_layer_id,
            ),
            self.query_basemap_layer(
                polyline_payload,
                layer_id=self.settings.arcgis_path_layer_id,
            ),
        )

        counts = {
            "gravel": len(gravel_features),
            "sidewalk": len(sidewalk_features),
            "path": len(path_features),
        }
        total = sum(counts.values())
        route_surface_types = sorted(
            {
                *self._surface_labels(gravel_features, fallback="GRAVEL"),
                *self._surface_labels(sidewalk_features, fallback="SIDEWALK"),
                *self._surface_labels(path_features, fallback="PATH"),
            }
        )
        surface_presence = {
            surface: round((count / total) * 100.0, 1)
            for surface, count in counts.items()
            if total > 0 and count > 0
        }

        return {
            "route_surface_types": route_surface_types,
            "has_gravel": counts["gravel"] > 0,
            "has_sidewalk": counts["sidewalk"] > 0,
            "has_path": counts["path"] > 0,
            "matched_segment_count": total,
            "matched_feature_counts": counts,
            "surface_presence": surface_presence,
            "layers": {
                "gravel": gravel_features,
                "sidewalk": sidewalk_features,
                "path": path_features,
            },
        }

    async def query_route(self, polyline_payload: PolylinePayload) -> dict[str, Any]:
        """Query all ArcGIS inputs required by the prototype scoring service."""

        pois_task = self.query_pois(polyline_payload)
        surface_task = self.classify_route_surface(polyline_payload)
        rest_stops_task = self.query_rest_stops(polyline_payload)
        pois, surface_summary, rest_stop_result = await asyncio.gather(
            pois_task,
            surface_task,
            rest_stops_task,
        )

        return {
            "pois": pois,
            "surface_summary": surface_summary,
            "rest_stops": rest_stop_result["rest_stops"],
            "rest_stop_data_available": rest_stop_result["source_status"]["available"],
            "rest_stop_source_status": rest_stop_result["source_status"],
            "rest_stop_raw_feature_count": rest_stop_result["raw_feature_count"],
        }

    async def _query_features(
        self,
        url: str,
        *,
        params: dict[str, str],
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Submit an ArcGIS query and normalize the returned features."""

        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_s) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "ArcGIS query failed. url=%s status_code=%s params=%s response=%s",
                url,
                exc.response.status_code,
                _sanitize_query_params(params),
                exc.response.text,
            )
            details = None
            if self.settings.prototype_mode:
                details = {
                    "url": url,
                    "params": _sanitize_query_params(params),
                    "status_code": exc.response.status_code,
                    "response_text": exc.response.text,
                }
            raise UpstreamServiceError("ArcGIS query failed.", details=details) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "ArcGIS request failed before response. url=%s params=%s error=%s",
                url,
                _sanitize_query_params(params),
                str(exc),
            )
            details = None
            if self.settings.prototype_mode:
                details = {
                    "url": url,
                    "params": _sanitize_query_params(params),
                    "upstream_error": str(exc),
                }
            raise UpstreamServiceError("Failed to reach ArcGIS service.", details=details) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("ArcGIS returned invalid JSON. url=%s response=%s", url, response.text)
            details = None
            if self.settings.prototype_mode:
                details = {
                    "url": url,
                    "params": _sanitize_query_params(params),
                    "response_text": response.text,
                }
            raise UpstreamServiceError("ArcGIS returned invalid JSON.", details=details) from exc

        if not isinstance(payload, dict):
            raise UpstreamServiceError(
                "Unexpected ArcGIS response format.",
                details=payload if self.settings.prototype_mode else None,
            )

        if payload.get("error"):
            logger.error("ArcGIS returned an error payload. url=%s error=%s", url, payload["error"])
            raise UpstreamServiceError(
                "ArcGIS query returned an error response.",
                details=payload if self.settings.prototype_mode else None,
            )

        features = payload.get("features", [])
        if not isinstance(features, list):
            raise UpstreamServiceError(
                "ArcGIS response did not include a valid feature list.",
                details=payload if self.settings.prototype_mode else None,
            )

        normalize = normalizer or self._normalize_feature
        return [normalize(feature) for feature in features if isinstance(feature, dict)]

    def arcgis_feature_to_geojson(self, feature: dict[str, Any]) -> dict[str, Any] | None:
        """Convert a limited subset of ArcGIS geometries into GeoJSON."""

        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            return None

        if "x" in geometry and "y" in geometry:
            return {"type": "Point", "coordinates": [geometry["x"], geometry["y"]]}

        if "paths" in geometry and isinstance(geometry["paths"], list):
            paths = geometry["paths"]
            if len(paths) == 1:
                return {"type": "LineString", "coordinates": paths[0]}
            return {"type": "MultiLineString", "coordinates": paths}

        if "rings" in geometry and isinstance(geometry["rings"], list):
            return {"type": "Polygon", "coordinates": geometry["rings"]}

        return None

    def _normalize_feature(self, feature: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw ArcGIS feature for backend consumption."""

        attributes = feature.get("attributes")
        geometry = feature.get("geometry")
        normalized: dict[str, Any] = {
            "attributes": attributes if isinstance(attributes, dict) else {},
            "geometry": geometry if isinstance(geometry, dict) else None,
        }
        geojson = self.arcgis_feature_to_geojson(feature)
        if geojson is not None:
            normalized["geojson"] = geojson
        return normalized

    def _normalize_rest_stop_feature(self, feature: dict[str, Any]) -> dict[str, Any]:
        """Normalize a live rest-stop survey feature into a scoring-friendly object."""

        attributes = feature.get("attributes")
        geometry = feature.get("geometry")
        if not isinstance(attributes, dict):
            attributes = {}
        if not isinstance(geometry, dict):
            geometry = {}

        lon = geometry.get("x")
        lat = geometry.get("y")

        return {
            "objectid": _get_case_insensitive(attributes, "objectid"),
            "globalid": _get_case_insensitive(attributes, "globalid"),
            "rest_type": _string_or_none(
                _get_case_insensitive(attributes, "what_kind_of_rest_stop_is_this")
            ),
            "rest_quality_raw": _string_or_none(
                _get_case_insensitive(attributes, "rest_quality")
            ),
            "rest_quality_score": parse_rest_quality_score(
                _get_case_insensitive(attributes, "rest_quality")
            ),
            "location": {
                "lat": lat if isinstance(lat, (int, float)) else None,
                "lon": lon if isinstance(lon, (int, float)) else None,
            },
            "creation_date": _get_case_insensitive(attributes, "CreationDate"),
            "edit_date": _get_case_insensitive(attributes, "EditDate"),
        }

    def _surface_labels(
        self,
        features: list[dict[str, Any]],
        *,
        fallback: str,
    ) -> set[str]:
        """Extract human-readable surface labels from layer attributes."""

        labels: set[str] = set()
        for feature in features:
            attributes = feature.get("attributes", {})
            if not isinstance(attributes, dict):
                continue
            for field in ("surface_type", "surface", "type", "material", "classification"):
                value = _get_case_insensitive(attributes, field)
                if isinstance(value, str) and value.strip():
                    labels.add(value.strip().upper())
                    break
        if not labels and features:
            labels.add(fallback)
        return labels


def _get_case_insensitive(attributes: dict[str, Any], field_name: str) -> Any | None:
    """Return an attribute value regardless of input key casing."""

    target = field_name.lower()
    for key, value in attributes.items():
        if key.lower() == target:
            return value
    return None


def parse_rest_quality_score(raw_value: Any) -> int | None:
    """Parse Survey123 rest-quality strings like `3 = good` into numeric scores."""

    if raw_value is None:
        return None

    match = re.match(r"^\s*(\d+)", str(raw_value))
    if match is None:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _string_or_none(value: Any) -> str | None:
    """Return a stripped string value or None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_rest_stop_source_status(
    *,
    configured: bool,
    queried: bool,
    authenticated: bool,
    available: bool,
    reason: str,
) -> dict[str, Any]:
    """Build a compact rest-stop source status object for responses."""

    return {
        "configured": configured,
        "queried": queried,
        "authenticated": authenticated,
        "available": available,
        "reason": reason,
    }


def _sanitize_query_params(params: dict[str, str]) -> dict[str, str]:
    """Redact sensitive query parameters before logging."""

    sanitized = dict(params)
    if "token" in sanitized:
        sanitized["token"] = "***redacted***"
    return sanitized


def _rest_stop_failure_reason(exc: UpstreamServiceError) -> str:
    """Extract a concise reason string from an ArcGIS rest-stop failure."""

    details = exc.details
    if isinstance(details, dict):
        error_payload = details.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

            details_list = error_payload.get("details")
            if isinstance(details_list, list) and details_list:
                first = details_list[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()

        response_text = details.get("response_text")
        if isinstance(response_text, str) and "Token Required" in response_text:
            return "Token Required"

    return exc.message


def _upstream_status_code(exc: UpstreamServiceError) -> int | None:
    """Return an upstream status code when present in exception details."""

    details = exc.details
    if isinstance(details, dict):
        status_code = details.get("status_code")
        if isinstance(status_code, int):
            return status_code
        error_payload = details.get("error")
        if isinstance(error_payload, dict):
            code = error_payload.get("code")
            if isinstance(code, int):
                return code
    return None


def _upstream_response_text(exc: UpstreamServiceError) -> str | None:
    """Return raw upstream response text when present in exception details."""

    details = exc.details
    if isinstance(details, dict):
        response_text = details.get("response_text")
        if isinstance(response_text, str):
            return response_text
    return None
