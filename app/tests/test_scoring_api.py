"""API tests for route scoring endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes import get_arcgis_service, get_scoring_service
from app.main import app
from app.models.routing import (
    RestStopSourceStatus,
    RouteCategoryScores,
    RouteScoreMetrics,
    RouteScoreRawArcGIS,
    RouteScoreResponse,
)


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
