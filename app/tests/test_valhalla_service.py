"""Tests for Valhalla request translation and backend error mapping."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.models.routing import ResolvedLocation
from app.services.valhalla_service import ValhallaService
from app.main import app


@pytest.mark.anyio
async def test_valhalla_route_request_uses_json_query_param_and_snapping_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route requests should use GET /route with a json payload query param."""

    captured: dict[str, object] = {}
    encoded = _encode_polyline6(
        [
            [-71.1183248, 42.40852],
            [-71.1172000, 42.40780],
            [-71.1150000, 42.40670],
        ]
    )
    success_payload = {
        "trip": {
            "legs": [
                {
                    "shape": encoded,
                    "summary": {"length": 0.42, "time": 310},
                }
            ],
            "summary": {"length": 0.42, "time": 310},
        }
    }

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            request = httpx.Request("GET", url, headers=headers, params=params)
            return httpx.Response(200, json=success_payload, request=request)

    monkeypatch.setattr("app.services.valhalla_service.httpx.AsyncClient", FakeAsyncClient)

    service = ValhallaService(
        settings=Settings(
            VALHALLA_BASE_URL="http://localhost:8002",
            PROTOTYPE_MODE=True,
        )
    )
    routes = await service.generate_routes(
        origin=ResolvedLocation(lat=42.40852, lon=-71.1183248, source="input_coordinates"),
        destination=ResolvedLocation(lat=42.4067, lon=-71.1150, source="input_coordinates"),
        mode="pedestrian",
        alternatives=3,
    )

    payload = json.loads(captured["params"]["json"])  # type: ignore[index]
    assert captured["url"] == "http://localhost:8002/route"
    assert "alternatives" not in payload
    assert payload["alternates"] == 2
    assert payload["costing"] == "pedestrian"
    assert payload["shape_format"] == "polyline6"
    assert len(payload["locations"]) == 2
    for location in payload["locations"]:
        assert location["radius"] == 50
        assert location["minimum_reachability"] == 1
        assert location["rank_candidates"] is True

    assert routes[0].decoded_shape[0][0] == pytest.approx(-71.1183248, abs=1e-6)
    assert routes[0].decoded_shape[0][1] == pytest.approx(42.40852, abs=1e-6)


def test_backend_maps_no_suitable_edges_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API should convert Valhalla snapping failures into a cleaner 502 error."""

    error_payload = {
        "error_code": 171,
        "error": "No suitable edges near location",
        "status_code": 400,
        "status": "Bad Request",
    }

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> httpx.Response:
            request = httpx.Request("GET", url, headers=headers, params=params)
            return httpx.Response(400, json=error_payload, request=request)

    monkeypatch.setattr("app.services.valhalla_service.httpx.AsyncClient", FakeAsyncClient)

    client = TestClient(app)
    response = client.post(
        "/routes/generate",
        json={
            "origin": {"lat": 42.40852, "lon": -71.1183248},
            "destination": {"lat": 42.4067, "lon": -71.1150},
            "mode": "pedestrian",
            "alternatives": 1,
        },
    )

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["type"] == "upstream_service_error"
    assert (
        payload["error"]["message"]
        == "Valhalla could not snap one or both locations to a pedestrian network edge."
    )
    assert payload["error"]["details"]["upstream_error"] == "No suitable edges near location"
    assert payload["error"]["details"]["payload"]["locations"][0]["radius"] == 50


def _encode_polyline6(coordinates: list[list[float]]) -> str:
    """Encode [lon, lat] coordinates into a polyline6 string for test fixtures."""

    result: list[str] = []
    last_lat = 0
    last_lon = 0

    for lon, lat in coordinates:
        current_lat = int(round(lat * 1_000_000))
        current_lon = int(round(lon * 1_000_000))

        result.append(_encode_value(current_lat - last_lat))
        result.append(_encode_value(current_lon - last_lon))

        last_lat = current_lat
        last_lon = current_lon

    return "".join(result)


def _encode_value(value: int) -> str:
    """Encode a signed polyline delta for tests."""

    transformed = ~(value << 1) if value < 0 else (value << 1)
    chunks: list[str] = []

    while transformed >= 0x20:
        chunks.append(chr((0x20 | (transformed & 0x1F)) + 63))
        transformed >>= 5

    chunks.append(chr(transformed + 63))
    return "".join(chunks)
