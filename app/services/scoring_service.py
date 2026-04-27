"""Prototype route scoring built around route geometry."""

from __future__ import annotations

import math
from typing import Any

from app.models.routing import (
    PolylinePayload,
    RestStopSourceStatus,
    RouteCandidate,
    RouteCategoryScores,
    RouteScoreMetrics,
    RouteScoreRawArcGIS,
    RouteScoreRequest,
    RouteScoreResponse,
)
from app.services.arcgis_service import ArcGISService

CATEGORY_WEIGHTS = {
    "obstacles": 0.45,
    "rest_support": 0.30,
    "crossings": 0.10,
    "surface": 0.10,
    "efficiency": 0.05,
}

OBSTACLE_SEVERITY_POINTS = {
    "LOW": 1.0,
    "MEDIUM": 3.0,
    "MODERATE": 3.0,
    "HIGH": 6.0,
    "SEVERE": 6.0,
    "CRITICAL": 10.0,
    "NOT ACCESSIBLE": 10.0,
    "SAFETY HAZARD": 10.0,
    "UNKNOWN": 3.0,
}

CROSSING_KEYWORDS = (
    "crosswalk",
    "crossing",
    "pedestrian signal",
    "ped signal",
    "unsafe crossing",
)

COGNITIVE_KEYWORDS = (
    "cognitive",
    "sensory",
    "wayfinding",
    "neuro",
)

REST_QUALITY_FIELDS = (
    "quality",
    "rest_quality",
    "stop_quality",
    "rating",
    "score",
)

REST_TYPE_WEIGHTS = {
    "Bench": 1.0,
    "Chair": 0.9,
    "Low wall": 0.7,
    "Ledge": 0.6,
    "Step": 0.5,
}


class ScoringService:
    """Compute a small heuristic score from ArcGIS route corridor queries."""

    def __init__(self, arcgis_service: ArcGISService | None = None) -> None:
        self.arcgis_service = arcgis_service or ArcGISService()

    async def score_request(self, request: RouteScoreRequest) -> RouteScoreResponse:
        """Score a route request from either a normalized route or raw geometry."""

        route = request.route
        polyline_payload = route.polyline_payload if route else request.polyline_payload
        if polyline_payload is None:
            raise ValueError("polyline_payload must be present after request validation")

        route_points = _extract_route_points(route, polyline_payload)
        route_id = route.route_id if route else (request.route_id or "scored-route")
        distance_m = route.distance_m if route else request.distance_m
        duration_s = route.duration_s if route else request.duration_s

        if distance_m is None:
            distance_m = _polyline_distance_m(route_points)
        if duration_s is None:
            duration_s = round(distance_m / 1.3, 3)

        arcgis_result = await self.arcgis_service.query_route(polyline_payload)
        metrics = self._build_metrics(
            route_points=route_points,
            distance_m=distance_m,
            duration_s=duration_s,
            arcgis_result=arcgis_result,
        )
        category_scores = self._score_categories(metrics)
        overall_score = self._overall_score(category_scores)
        explanation = self._build_explanation(metrics, category_scores)

        return RouteScoreResponse(
            route_id=route_id,
            metrics=metrics,
            category_scores=category_scores,
            overall_score=overall_score,
            explanation=explanation,
            raw_arcgis=RouteScoreRawArcGIS(
                pois=arcgis_result["pois"],
                surface_summary=arcgis_result["surface_summary"],
                rest_stops=arcgis_result["rest_stops"],
            ),
            rest_stop_source_status=RestStopSourceStatus.model_validate(
                arcgis_result["rest_stop_source_status"]
            ),
        )

    def _build_metrics(
        self,
        *,
        route_points: list[list[float]],
        distance_m: float,
        duration_s: float,
        arcgis_result: dict[str, Any],
    ) -> RouteScoreMetrics:
        """Aggregate raw ArcGIS outputs into a small set of scoring metrics."""

        pois = arcgis_result["pois"]
        surface_summary = arcgis_result["surface_summary"]
        rest_stops = arcgis_result["rest_stops"]
        rest_stop_data_available = arcgis_result["rest_stop_source_status"]["available"]

        severity_counts: dict[str, int] = {}
        weighted_penalty = 0.0
        cognitive_obstacle_count = 0
        crossing_issue_count = 0

        for feature in pois:
            attributes = _attributes(feature)
            obstacle_text = _searchable_text(
                _string_value(attributes, "obstacle_category", "category"),
                _string_value(attributes, "obstacle_type", "type", "issue_type"),
                _string_value(attributes, "affected_users", "affected_user"),
                _string_value(attributes, "severity", "severity_rating", "priority"),
            )
            severity = _obstacle_severity_label(attributes, obstacle_text)
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            weighted_penalty += OBSTACLE_SEVERITY_POINTS.get(severity.upper(), 3.0)

            if any(keyword in obstacle_text for keyword in COGNITIVE_KEYWORDS):
                cognitive_obstacle_count += 1

            if any(keyword in obstacle_text for keyword in CROSSING_KEYWORDS):
                crossing_issue_count += 1

        rest_quality_values = [
            value
            for value in (_rest_quality_score(feature) for feature in rest_stops)
            if value is not None
        ]
        avg_rest_quality = (
            round(sum(rest_quality_values) / len(rest_quality_values), 2)
            if rest_quality_values
            else None
        )
        rest_stop_types = sorted(
            {
                rest_type
                for rest_type in (_rest_type(feature) for feature in rest_stops)
                if rest_type is not None
            }
        )

        return RouteScoreMetrics(
            distance_m=round(distance_m, 3),
            duration_s=round(duration_s, 3),
            route_point_count=len(route_points),
            obstacle_count=len(pois),
            obstacle_severity_counts=dict(sorted(severity_counts.items())),
            cognitive_obstacle_count=cognitive_obstacle_count,
            crossing_issue_count=crossing_issue_count,
            weighted_obstacle_penalty=round(weighted_penalty, 2),
            route_surface_types=surface_summary["route_surface_types"],
            has_gravel=surface_summary["has_gravel"],
            has_sidewalk=surface_summary["has_sidewalk"],
            has_path=surface_summary["has_path"],
            matched_segment_count=surface_summary["matched_segment_count"],
            matched_surface_feature_counts=surface_summary["matched_feature_counts"],
            surface_presence=surface_summary["surface_presence"],
            rest_stop_count=len(rest_stops),
            avg_rest_quality=avg_rest_quality,
            rest_stop_types=rest_stop_types,
            rest_stop_data_available=rest_stop_data_available,
        )

    def _score_categories(self, metrics: RouteScoreMetrics) -> RouteCategoryScores:
        """Score route categories using a simple prototype heuristic."""

        obstacle_score = _clamp(
            100.0
            - (metrics.weighted_obstacle_penalty * 5.0)
            - (metrics.obstacle_count * 3.0)
            - (metrics.cognitive_obstacle_count * 2.0),
            0.0,
            100.0,
        )
        crossing_score = _clamp(100.0 - (metrics.crossing_issue_count * 18.0), 0.0, 100.0)

        surface_score = 55.0
        if metrics.has_sidewalk:
            surface_score += 20.0
        if metrics.has_path:
            surface_score += 15.0
        if metrics.has_gravel:
            surface_score -= 25.0
        if metrics.matched_segment_count == 0:
            surface_score -= 10.0
        surface_score = _clamp(surface_score, 0.0, 100.0)

        ideal_duration_s = max(metrics.distance_m / 1.3, 1.0)
        duration_ratio = metrics.duration_s / ideal_duration_s
        efficiency_score = _clamp(
            100.0
            - max(duration_ratio - 1.0, 0.0) * 60.0
            - min(metrics.distance_m / 150.0, 25.0),
            0.0,
            100.0,
        )

        if not metrics.rest_stop_data_available:
            rest_support_score = 50.0
        else:
            quality_bonus = (
                0.0
                if metrics.avg_rest_quality is None
                else min(metrics.avg_rest_quality * 9.0, 36.0)
            )
            type_bonus = _rest_type_bonus(metrics.rest_stop_types)
            rest_support_score = _clamp(
                10.0
                + min(metrics.rest_stop_count * 18.0, 54.0)
                + quality_bonus
                + type_bonus,
                0.0,
                100.0,
            )

        return RouteCategoryScores(
            obstacles=round(obstacle_score, 1),
            crossings=round(crossing_score, 1),
            surface=round(surface_score, 1),
            efficiency=round(efficiency_score, 1),
            rest_support=round(rest_support_score, 1),
        )

    def _overall_score(self, category_scores: RouteCategoryScores) -> float:
        """Combine category scores into a single weighted prototype score."""

        overall = (
            category_scores.obstacles * CATEGORY_WEIGHTS["obstacles"]
            + category_scores.rest_support * CATEGORY_WEIGHTS["rest_support"]
            + category_scores.crossings * CATEGORY_WEIGHTS["crossings"]
            + category_scores.surface * CATEGORY_WEIGHTS["surface"]
            + category_scores.efficiency * CATEGORY_WEIGHTS["efficiency"]
        )
        return round(overall, 1)

    def _build_explanation(
        self,
        metrics: RouteScoreMetrics,
        category_scores: RouteCategoryScores,
    ) -> str:
        """Build a short explanation from the metrics that drove the score."""

        if not metrics.rest_stop_data_available:
            rest_summary = "rest-stop data is unavailable, so rest support remains neutral"
        elif metrics.rest_stop_count >= 2 and (metrics.avg_rest_quality or 0.0) >= 2.5:
            rest_summary = "it has nearby rest opportunities"
        elif metrics.rest_stop_count == 0:
            rest_summary = "it has limited rest support"
        else:
            rest_summary = "it has some rest opportunities"

        severe_obstacle_count = (
            metrics.obstacle_severity_counts.get("Not Accessible", 0)
            + metrics.obstacle_severity_counts.get("Safety Hazard", 0)
            + metrics.obstacle_severity_counts.get("High", 0)
        )
        if metrics.obstacle_count == 0 or (
            metrics.obstacle_count <= 1 and metrics.weighted_obstacle_penalty <= 2.0
        ):
            obstacle_clause = "low obstacle burden"
        elif severe_obstacle_count > 0 or metrics.weighted_obstacle_penalty >= 10.0:
            obstacle_clause = "obstacle severity lowers its score"
        elif metrics.obstacle_count >= 3:
            obstacle_clause = "repeated obstacles lower its score"
        else:
            obstacle_clause = "some obstacle burden remains"

        if metrics.rest_stop_data_available and metrics.rest_stop_count >= 2 and metrics.obstacle_count <= 1:
            message = (
                f"This route scores well because {rest_summary} and {obstacle_clause}."
            )
        elif "lowers its score" in obstacle_clause and category_scores.efficiency >= 70.0:
            message = (
                f"This route has {rest_summary}, but {obstacle_clause} despite reasonable efficiency."
            )
        elif not metrics.rest_stop_data_available:
            message = (
                f"This route is driven mostly by obstacle burden because {rest_summary}."
            )
        elif metrics.rest_stop_data_available and metrics.rest_stop_count == 0:
            message = f"This route has limited rest support, which lowers its score, and {obstacle_clause}."
        else:
            message = f"This route has {rest_summary} and {obstacle_clause}."

        if metrics.has_gravel:
            surface_note = " Gravel exposure is also present."
        elif not (metrics.has_sidewalk or metrics.has_path):
            surface_note = " Surface coverage data is limited."
        elif metrics.crossing_issue_count >= 2:
            surface_note = " Crossing-related issues are also present."
        elif metrics.obstacle_count == 0:
            surface_note = " Surface conditions are not a major concern in this score."
        else:
            surface_note = ""

        return f"{message}{surface_note}"


def _extract_route_points(
    route: RouteCandidate | None,
    polyline_payload: PolylinePayload,
) -> list[list[float]]:
    """Resolve the [lon, lat] route point list from the available geometry input."""

    if route is not None and route.decoded_shape:
        return [[lon, lat] for lon, lat in route.decoded_shape]

    if polyline_payload.paths and polyline_payload.paths[0]:
        return [[lon, lat] for lon, lat in polyline_payload.paths[0]]

    return []


def _polyline_distance_m(route_points: list[list[float]]) -> float:
    """Approximate line length in meters using haversine segments."""

    if len(route_points) < 2:
        return 0.0

    distance_m = 0.0
    for start, end in zip(route_points, route_points[1:]):
        distance_m += _haversine_m(start[1], start[0], end[1], end[0])
    return distance_m


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


def _attributes(feature: dict[str, Any]) -> dict[str, Any]:
    """Return feature attributes or an empty mapping."""

    attributes = feature.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _string_value(attributes: dict[str, Any], *fields: str) -> str | None:
    """Extract the first non-empty string-like attribute value."""

    for field in fields:
        for key, value in attributes.items():
            if key.lower() == field.lower() and value is not None:
                text = str(value).strip()
                if text:
                    return text
    return None


def _numeric_value(attributes: dict[str, Any], *fields: str) -> float | None:
    """Extract the first numeric attribute value."""

    value = _string_value(attributes, *fields)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _canonical_severity(raw_value: str | None) -> str:
    """Normalize an obstacle severity label."""

    if not raw_value:
        return "Unknown"

    normalized = raw_value.strip().upper()
    if normalized in {
        "LOW",
        "MEDIUM",
        "MODERATE",
        "HIGH",
        "SEVERE",
        "CRITICAL",
        "NOT ACCESSIBLE",
        "SAFETY HAZARD",
    }:
        if normalized == "MODERATE":
            return "Medium"
        if normalized == "NOT ACCESSIBLE":
            return "Not Accessible"
        if normalized == "SAFETY HAZARD":
            return "Safety Hazard"
        return normalized.title()
    return raw_value.strip().title()


def _searchable_text(*values: str | None) -> str:
    """Collapse relevant attributes into a lowercased string for keyword matching."""

    return " ".join(value.lower() for value in values if value).strip()


def _rest_quality_score(rest_stop: dict[str, Any]) -> float | None:
    """Return the numeric rest-quality score from normalized or legacy records."""

    direct_value = rest_stop.get("rest_quality_score")
    if isinstance(direct_value, (int, float)):
        return float(direct_value)

    return _numeric_value(_attributes(rest_stop), *REST_QUALITY_FIELDS)


def _rest_type(rest_stop: dict[str, Any]) -> str | None:
    """Return the rest-stop type from normalized or legacy records."""

    direct_value = rest_stop.get("rest_type")
    if isinstance(direct_value, str) and direct_value.strip():
        return direct_value.strip()

    return _string_value(
        _attributes(rest_stop),
        "what_kind_of_rest_stop_is_this",
        "rest_type",
        "type",
    )


def _rest_type_bonus(rest_stop_types: list[str]) -> float:
    """Apply a mild rest-type bonus so benches and chairs score slightly better."""

    if not rest_stop_types:
        return 0.0

    weights = [REST_TYPE_WEIGHTS.get(rest_type, 0.7) for rest_type in rest_stop_types]
    return round((sum(weights) / len(weights)) * 6.0, 2)


def _obstacle_severity_label(attributes: dict[str, Any], obstacle_text: str) -> str:
    """Return the effective severity label used for obstacle scoring."""

    if "not accessible" in obstacle_text:
        return "Not Accessible"
    if "safety hazard" in obstacle_text:
        return "Safety Hazard"
    return _canonical_severity(
        _string_value(attributes, "severity", "severity_rating", "priority")
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp a numeric value into a bounded range."""

    return max(minimum, min(maximum, value))
