"""API tests for route scoring endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes import get_arcgis_service, get_scoring_service, get_valhalla_service
from app.main import app
from app.models.routing import (
    GeoJSONLineString,
    PolylinePayload,
    RestStopSourceStatus,
    RouteCategoryScores,
    RouteCandidate,
    RouteScoreMetrics,
    RouteScoreRawArcGIS,
    RouteScoreResponse,
    SpatialReference,
)
from app.services.valhalla_service import RouteGenerationDiagnostics, RouteGenerationResult


client = TestClient(app)


class FakeScoringService:
    """Deterministic scoring stub for API tests."""

    async def score_request(self, request: object) -> RouteScoreResponse:
        return RouteScoreResponse(
            route_id="route-1",
            metrics=RouteScoreMetrics(
                distance_m=1141.0,
                duration_s=809.812,
                route_point_count=36,
                obstacle_count=3,
                obstacle_severity_counts={"Low": 1, "Medium": 1, "High": 1},
                cognitive_obstacle_count=1,
                crossing_issue_count=1,
                weighted_obstacle_penalty=12.5,
                route_surface_types=["SIDEWALK", "PATH"],
                has_gravel=False,
                has_sidewalk=True,
                has_path=True,
                matched_segment_count=5,
                matched_surface_feature_counts={"gravel": 0, "sidewalk": 3, "path": 2},
                surface_presence={"sidewalk": 60.0, "path": 40.0},
                rest_stop_count=2,
                avg_rest_quality=2.5,
                rest_stop_types=["Bench", "Chair"],
                rest_stop_data_available=True,
            ),
            category_scores=RouteCategoryScores(
                obstacles=58.0,
                crossings=82.0,
                surface=81.0,
                rest_support=88.0,
                efficiency=84.0,
            ),
            overall_score=74.3,
            explanation=(
                "This route scores well because it has nearby rest opportunities "
                "and low obstacle burden."
            ),
            raw_arcgis=RouteScoreRawArcGIS(
                pois=[{"attributes": {"severity": "High"}}],
                surface_summary={"has_sidewalk": True, "has_path": True},
                rest_stops=[{"attributes": {"quality": 3}}],
            ),
            rest_stop_source_status=RestStopSourceStatus(
                configured=True,
                queried=True,
                authenticated=False,
                available=True,
                reason="Success",
            ),
        )


class FakeArcGISService:
    """Deterministic ArcGIS stub for rest-stop debug endpoint tests."""

    async def query_rest_stops(self, polyline_payload: object) -> dict[str, object]:
        return {
            "rest_stops": [
                {
                    "objectid": 11,
                    "globalid": "{abc-123}",
                    "rest_type": "Bench",
                    "rest_quality_raw": "3 = good",
                    "rest_quality_score": 3,
                    "location": {"lat": 42.409, "lon": -71.118},
                    "creation_date": 1710000000000,
                    "edit_date": 1710003600000,
                }
            ],
            "raw_feature_count": 1,
            "source_status": {
                "configured": True,
                "queried": True,
                "authenticated": False,
                "available": True,
                "reason": "Success",
            },
        }


class FakeDiverseValhallaService:
    """Route generation stub that exposes more candidates than it returns."""

    def __init__(self) -> None:
        self.low_route = _route_candidate("route-low", distance_m=400.0)
        self.high_route = _route_candidate("route-high", distance_m=440.0)

    async def generate_route_candidates(self, **kwargs: object) -> RouteGenerationResult:
        return RouteGenerationResult(
            routes=[self.low_route],
            distinct_candidates=[self.low_route, self.high_route],
            diagnostics=RouteGenerationDiagnostics(
                requested_alternatives=1,
                internal_candidate_target=6,
                raw_candidate_count=2,
                distinct_candidate_count=2,
                returned_route_count=1,
            ),
        )


class FakeRankedScoringService:
    """Scoring stub that proves generate-and-score scores every distinct candidate."""

    def __init__(self) -> None:
        self.scored_route_ids: list[str] = []

    async def score_request(self, request: object) -> RouteScoreResponse:
        route = request.route  # type: ignore[attr-defined]
        self.scored_route_ids.append(route.route_id)
        overall_score = 91.0 if route.route_id == "route-high" else 62.0
        return _score_response(route.route_id, overall_score=overall_score)


def test_score_endpoint_rejects_missing_route_and_polyline_payload() -> None:
    """Scoring input must include either a route object or a polyline payload."""

    response = client.post("/routes/score", json={})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"


def test_score_endpoint_returns_expected_response_shape() -> None:
    """The score endpoint should return the normalized scoring response."""

    app.dependency_overrides[get_scoring_service] = lambda: FakeScoringService()
    try:
        response = client.post(
            "/routes/score",
            json={
                "route_id": "route-1",
                "polyline_payload": {
                    "paths": [
                        [
                            [-71.1183248, 42.40852],
                            [-71.1150, 42.4067],
                        ]
                    ],
                    "spatialReference": {"wkid": 4326},
                },
                "distance_m": 1141.0,
                "duration_s": 809.812,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_id"] == "route-1"
    assert payload["metrics"]["obstacle_count"] == 3
    assert payload["metrics"]["rest_stop_data_available"] is True
    assert payload["metrics"]["rest_stop_types"] == ["Bench", "Chair"]
    assert payload["category_scores"]["rest_support"] == 88.0
    assert payload["overall_score"] == 74.3
    assert payload["rest_stop_source_status"]["reason"] == "Success"
    assert "explanation" in payload
    assert "raw_arcgis" in payload


def test_debug_rest_stops_endpoint_returns_expected_response_shape() -> None:
    """The rest-stop debug endpoint should expose normalized records and source status."""

    app.dependency_overrides[get_arcgis_service] = lambda: FakeArcGISService()
    try:
        response = client.post(
            "/routes/debug-rest-stops",
            json={
                "route_id": "route-1",
                "polyline_payload": {
                    "paths": [
                        [
                            [-71.1183248, 42.40852],
                            [-71.1150, 42.4067],
                        ]
                    ],
                    "spatialReference": {"wkid": 4326},
                },
                "distance_m": 1141.0,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_id"] == "route-1"
    assert payload["raw_feature_count"] == 1
    assert payload["rest_stop_source_status"]["available"] is True
    assert payload["rest_stops"][0]["rest_type"] == "Bench"


def test_generate_and_score_scores_distinct_candidates_and_returns_best_requested_count() -> None:
    """The combined endpoint should rank all distinct internal candidates before returning."""

    scoring_service = FakeRankedScoringService()
    app.dependency_overrides[get_valhalla_service] = lambda: FakeDiverseValhallaService()
    app.dependency_overrides[get_scoring_service] = lambda: scoring_service
    try:
        response = client.post(
            "/routes/generate-and-score",
            json={
                "origin": {"lat": 42.40852, "lon": -71.1183248},
                "destination": {"lat": 42.4067, "lon": -71.1150},
                "mode": "pedestrian",
                "alternatives": 1,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert scoring_service.scored_route_ids == ["route-low", "route-high"]
    assert len(payload["routes"]) == 1
    assert payload["routes"][0]["route_id"] == "route-high"
    assert payload["routes"][0]["score"]["overall_score"] == 91.0


def _route_candidate(route_id: str, *, distance_m: float) -> RouteCandidate:
    """Build a compact route candidate for API tests."""

    coordinates = [
        [-71.1183248, 42.40852],
        [-71.1172, 42.4078],
        [-71.115, 42.4067],
    ]
    return RouteCandidate(
        route_id=route_id,
        distance_m=distance_m,
        duration_s=distance_m / 1.3,
        encoded_polyline=route_id,
        decoded_shape=coordinates,
        geojson=GeoJSONLineString(coordinates=coordinates),
        polyline_payload=PolylinePayload(
            paths=[coordinates],
            spatialReference=SpatialReference(),
        ),
        summary={"length": distance_m / 1000.0, "time": distance_m / 1.3},
    )


def _score_response(route_id: str, *, overall_score: float) -> RouteScoreResponse:
    """Build a compact scoring response for API tests."""

    return RouteScoreResponse(
        route_id=route_id,
        metrics=RouteScoreMetrics(
            distance_m=1141.0,
            duration_s=809.812,
            route_point_count=36,
            obstacle_count=0,
            obstacle_severity_counts={},
            cognitive_obstacle_count=0,
            crossing_issue_count=0,
            weighted_obstacle_penalty=0.0,
            route_surface_types=["SIDEWALK"],
            has_gravel=False,
            has_sidewalk=True,
            has_path=False,
            matched_segment_count=1,
            matched_surface_feature_counts={"gravel": 0, "sidewalk": 1, "path": 0},
            surface_presence={"sidewalk": 100.0},
            rest_stop_count=0,
            rest_stop_types=[],
            rest_stop_data_available=True,
        ),
        category_scores=RouteCategoryScores(
            obstacles=100.0,
            crossings=100.0,
            surface=75.0,
            rest_support=10.0,
            efficiency=80.0,
        ),
        overall_score=overall_score,
        explanation="Test score.",
        raw_arcgis=RouteScoreRawArcGIS(),
        rest_stop_source_status=RestStopSourceStatus(
            configured=True,
            queried=True,
            authenticated=False,
            available=True,
            reason="Success",
        ),
    )
