"""Tests for prototype route scoring."""

from __future__ import annotations

import pytest

from app.models.routing import PolylinePayload, RouteScoreRequest, SpatialReference
from app.services.scoring_service import CATEGORY_WEIGHTS, ScoringService


class ScenarioArcGISService:
    """Configurable ArcGIS stub for scoring tests."""

    def __init__(
        self,
        *,
        pois: list[dict[str, object]] | None = None,
        rest_stops: list[dict[str, object]] | None = None,
        rest_stop_data_available: bool = True,
    ) -> None:
        self._pois = pois or []
        self._rest_stops = rest_stops or []
        self._rest_stop_data_available = rest_stop_data_available

    async def query_route(self, polyline_payload: PolylinePayload) -> dict[str, object]:
        return {
            "pois": self._pois,
            "surface_summary": {
                "route_surface_types": ["SIDEWALK", "PATH"],
                "has_gravel": False,
                "has_sidewalk": True,
                "has_path": True,
                "matched_segment_count": 3,
                "matched_feature_counts": {"gravel": 0, "sidewalk": 2, "path": 1},
                "surface_presence": {"sidewalk": 66.7, "path": 33.3},
            },
            "rest_stops": self._rest_stops,
            "rest_stop_data_available": self._rest_stop_data_available,
            "rest_stop_source_status": {
                "configured": True,
                "queried": True,
                "authenticated": False,
                "available": self._rest_stop_data_available,
                "reason": "Success" if self._rest_stop_data_available else "Token Required",
            },
            "rest_stop_raw_feature_count": len(self._rest_stops),
        }


def _score_request() -> RouteScoreRequest:
    """Return a stable scoring request fixture."""

    return RouteScoreRequest(
        route_id="route-1",
        polyline_payload=PolylinePayload(
            paths=[[[-71.1183248, 42.40852], [-71.1150, 42.4067]]],
            spatialReference=SpatialReference(),
        ),
        distance_m=1141.0,
        duration_s=809.812,
    )


def test_default_category_weights_prioritize_obstacles_and_rest_support() -> None:
    """The prototype weighting should emphasize obstacle burden and rest support."""

    assert CATEGORY_WEIGHTS == {
        "obstacles": 0.45,
        "rest_support": 0.30,
        "crossings": 0.10,
        "surface": 0.10,
        "efficiency": 0.05,
    }


@pytest.mark.anyio
async def test_scoring_service_computes_obstacle_and_rest_metrics() -> None:
    """Scoring should surface obstacle and rest metrics clearly in the response."""

    service = ScoringService(
        arcgis_service=ScenarioArcGISService(
            pois=[
                {
                    "attributes": {
                        "obstacle_category": "Crossing",
                        "obstacle_type": "No crosswalk",
                        "severity": "High",
                        "affected_users": "cognitive;mobility",
                    }
                },
                {
                    "attributes": {
                        "obstacle_category": "Surface",
                        "obstacle_type": "Uneven pavement",
                        "severity": "Low",
                    }
                },
            ],
            rest_stops=[
                {
                    "rest_type": "Bench",
                    "rest_quality_raw": "2 = okay",
                    "rest_quality_score": 2,
                    "location": {"lat": 42.409, "lon": -71.118},
                },
                {
                    "rest_type": "Chair",
                    "rest_quality_raw": "3 = good",
                    "rest_quality_score": 3,
                    "location": {"lat": 42.4087, "lon": -71.1175},
                },
            ],
        )
    )
    response = await service.score_request(_score_request())

    assert response.route_id == "route-1"
    assert response.metrics.obstacle_count == 2
    assert response.metrics.obstacle_severity_counts == {"High": 1, "Low": 1}
    assert response.metrics.cognitive_obstacle_count == 1
    assert response.metrics.crossing_issue_count == 1
    assert response.metrics.weighted_obstacle_penalty == 7.0
    assert response.metrics.rest_stop_count == 2
    assert response.metrics.avg_rest_quality == 2.5
    assert response.metrics.rest_stop_types == ["Bench", "Chair"]
    assert response.metrics.rest_stop_data_available is True
    assert response.rest_stop_source_status.reason == "Success"
    assert response.category_scores.obstacles == 57.0
    assert response.category_scores.rest_support == 74.2
    assert 0.0 <= response.overall_score <= 100.0
    assert "rest" in response.explanation.lower()
    assert "obstacle" in response.explanation.lower()


@pytest.mark.anyio
async def test_better_rest_support_scores_higher_all_else_equal() -> None:
    """Routes with stronger rest support should score higher under the default profile."""

    low_rest_service = ScoringService(
        arcgis_service=ScenarioArcGISService(
            rest_stops=[],
            rest_stop_data_available=True,
        )
    )
    high_rest_service = ScoringService(
        arcgis_service=ScenarioArcGISService(
            rest_stops=[
                {"rest_type": "Bench", "rest_quality_score": 3},
                {"rest_type": "Bench", "rest_quality_score": 3},
                {"rest_type": "Chair", "rest_quality_score": 3},
            ],
            rest_stop_data_available=True,
        )
    )

    low_rest = await low_rest_service.score_request(_score_request())
    high_rest = await high_rest_service.score_request(_score_request())

    assert high_rest.category_scores.rest_support > low_rest.category_scores.rest_support
    assert high_rest.overall_score > low_rest.overall_score


@pytest.mark.anyio
async def test_worse_obstacle_burden_scores_lower_all_else_equal() -> None:
    """Routes with stronger obstacle penalties should score lower under the default profile."""

    low_obstacle_service = ScoringService(
        arcgis_service=ScenarioArcGISService(
            pois=[
                {
                    "attributes": {
                        "obstacle_category": "Surface",
                        "obstacle_type": "Minor crack",
                        "severity": "Low",
                    }
                }
            ],
            rest_stops=[
                {"rest_type": "Bench", "rest_quality_score": 3},
                {"rest_type": "Chair", "rest_quality_score": 3},
            ],
        )
    )
    high_obstacle_service = ScoringService(
        arcgis_service=ScenarioArcGISService(
            pois=[
                {
                    "attributes": {
                        "obstacle_category": "Access",
                        "obstacle_type": "Not accessible ramp",
                        "severity": "Not accessible",
                    }
                },
                {
                    "attributes": {
                        "obstacle_category": "Safety",
                        "obstacle_type": "Safety hazard at curb",
                        "severity": "Safety hazard",
                    }
                },
            ],
            rest_stops=[
                {"rest_type": "Bench", "rest_quality_score": 3},
                {"rest_type": "Chair", "rest_quality_score": 3},
            ],
        )
    )

    low_obstacle = await low_obstacle_service.score_request(_score_request())
    high_obstacle = await high_obstacle_service.score_request(_score_request())

    assert low_obstacle.category_scores.obstacles > high_obstacle.category_scores.obstacles
    assert low_obstacle.overall_score > high_obstacle.overall_score
