"""Tests for ArcGIS route corridor query helpers."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.models.routing import PolylinePayload, SpatialReference
from app.services.arcgis_service import ArcGISService, parse_rest_quality_score


def test_arcgis_intersects_params_builds_expected_polyline_query() -> None:
    """ArcGIS query params should preserve the backend's [lon, lat] geometry."""

    service = ArcGISService(
        settings=Settings(
            ARCGIS_CORRIDOR_DISTANCE_M=15,
            PROTOTYPE_MODE=True,
        )
    )

    params = service.arcgis_intersects_params(
        route_points=[[-71.1183248, 42.40852], [-71.1150, 42.4067]],
        out_fields="obstacle_category,severity",
    )

    geometry = json.loads(params["geometry"])
    assert params["geometryType"] == "esriGeometryPolyline"
    assert params["spatialRel"] == "esriSpatialRelIntersects"
    assert params["distance"] == "15.0"
    assert params["units"] == "esriSRUnit_Meter"
    assert params["outSR"] == "4326"
    assert params["f"] == "json"
    assert geometry["paths"] == [[[-71.1183248, 42.40852], [-71.115, 42.4067]]]


@pytest.mark.anyio
async def test_query_pois_uses_configured_arcgis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """POI queries should hit the configured ArcGIS endpoint with corridor params."""

    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            captured["url"] = url
            captured["params"] = params
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"features": []}, request=request)

    monkeypatch.setattr("app.services.arcgis_service.httpx.AsyncClient", FakeAsyncClient)

    service = ArcGISService(
        settings=Settings(
            ARCGIS_POI_URL="https://example.test/FeatureServer/0/query",
            PROTOTYPE_MODE=True,
        )
    )
    polyline_payload = _polyline_payload()

    await service.query_pois(polyline_payload)

    params = captured["params"]
    assert captured["url"] == "https://example.test/FeatureServer/0/query"
    assert params["geometryType"] == "esriGeometryPolyline"  # type: ignore[index]
    assert params["spatialRel"] == "esriSpatialRelIntersects"  # type: ignore[index]
    assert params["returnGeometry"] == "true"  # type: ignore[index]


def test_parse_rest_quality_score_handles_survey123_strings() -> None:
    """Survey123 rest-quality strings should be converted into numeric scores."""

    assert parse_rest_quality_score("1 = poor") == 1
    assert parse_rest_quality_score("2 = okay") == 2
    assert parse_rest_quality_score("3 = good") == 3
    assert parse_rest_quality_score("unknown") is None


@pytest.mark.anyio
async def test_query_rest_stops_anonymous_success_sets_available_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous rest-stop queries should succeed when the layer is public."""

    captured: dict[str, object] = {}
    response_payload = {
        "features": [
            {
                "attributes": {
                    "objectid": 11,
                    "globalid": "{abc-123}",
                    "what_kind_of_rest_stop_is_this": "Bench",
                    "rest_quality": "3 = good",
                    "CreationDate": 1710000000000,
                    "EditDate": 1710003600000,
                },
                "geometry": {"x": -71.118, "y": 42.409},
            }
        ]
    }

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            captured["url"] = url
            captured["params"] = params
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json=response_payload, request=request)

    monkeypatch.setattr("app.services.arcgis_service.httpx.AsyncClient", FakeAsyncClient)

    service = ArcGISService(
        settings=Settings(
            ARCGIS_REST_STOP_URL="https://example.test/rest/FeatureServer/0/query",
            PROTOTYPE_MODE=True,
        )
    )

    result = await service.query_rest_stops(_polyline_payload())

    params = captured["params"]
    assert "token" not in params  # type: ignore[operator]
    assert result["raw_feature_count"] == 1
    assert result["source_status"] == {
        "configured": True,
        "queried": True,
        "authenticated": False,
        "available": True,
        "reason": "Success",
    }
    assert result["rest_stops"] == [
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
    ]


@pytest.mark.anyio
async def test_query_rest_stops_attaches_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected rest-stop queries should attach the configured token."""

    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            captured["params"] = params
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"features": []}, request=request)

    monkeypatch.setattr("app.services.arcgis_service.httpx.AsyncClient", FakeAsyncClient)

    service = ArcGISService(
        settings=Settings(
            ARCGIS_REST_STOP_URL="https://example.test/rest/FeatureServer/0/query",
            ARCGIS_REST_STOP_TOKEN="secret-token",
            PROTOTYPE_MODE=True,
        )
    )

    result = await service.query_rest_stops(_polyline_payload())

    assert captured["params"]["token"] == "secret-token"  # type: ignore[index]
    assert result["source_status"] == {
        "configured": True,
        "queried": True,
        "authenticated": True,
        "available": True,
        "reason": "No nearby rest stops found",
    }


@pytest.mark.anyio
async def test_query_rest_stops_token_required_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token-required responses should not break the rest-stop path."""

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(
                200,
                json={
                    "error": {
                        "code": 499,
                        "message": "Token Required",
                        "messageCode": "GWM_0003",
                        "details": ["Token Required"],
                    }
                },
                request=request,
            )

    monkeypatch.setattr("app.services.arcgis_service.httpx.AsyncClient", FakeAsyncClient)

    service = ArcGISService(
        settings=Settings(
            ARCGIS_REST_STOP_URL="https://example.test/rest/FeatureServer/0/query",
            PROTOTYPE_MODE=True,
        )
    )

    result = await service.query_rest_stops(_polyline_payload())

    assert result["rest_stops"] == []
    assert result["raw_feature_count"] == 0
    assert result["source_status"] == {
        "configured": True,
        "queried": True,
        "authenticated": False,
        "available": False,
        "reason": "Token Required",
    }


@pytest.mark.anyio
async def test_query_rest_stops_unconfigured_and_empty_success_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfigured sources should be distinguishable from valid empty results."""

    unconfigured_service = ArcGISService(
        settings=Settings(
            ARCGIS_REST_STOP_URL="",
            PROTOTYPE_MODE=True,
        )
    )
    unconfigured = await unconfigured_service.query_rest_stops(_polyline_payload())

    assert unconfigured["source_status"] == {
        "configured": False,
        "queried": False,
        "authenticated": False,
        "available": False,
        "reason": "Rest-stop URL not configured",
    }

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"features": []}, request=request)

    monkeypatch.setattr("app.services.arcgis_service.httpx.AsyncClient", FakeAsyncClient)

    configured_service = ArcGISService(
        settings=Settings(
            ARCGIS_REST_STOP_URL="https://example.test/rest/FeatureServer/0/query",
            PROTOTYPE_MODE=True,
        )
    )
    empty_success = await configured_service.query_rest_stops(_polyline_payload())

    assert empty_success["source_status"] == {
        "configured": True,
        "queried": True,
        "authenticated": False,
        "available": True,
        "reason": "No nearby rest stops found",
    }


def _polyline_payload() -> PolylinePayload:
    """Return a shared polyline payload fixture."""

    return PolylinePayload(
        paths=[[[-71.1183248, 42.40852], [-71.1150, 42.4067]]],
        spatialReference=SpatialReference(),
    )
