"""Request validation tests for the FastAPI API layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_route_request_rejects_missing_location_values() -> None:
    """Origin and destination must each provide address or coordinates."""

    response = client.post(
        "/routes/generate",
        json={
            "origin": {},
            "destination": {"address": "419 Boston Ave, Medford, MA"},
            "mode": "pedestrian",
            "alternatives": 1,
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"


def test_route_request_rejects_address_and_coordinates_together() -> None:
    """A single location cannot mix address and direct coordinates."""

    response = client.post(
        "/routes/generate",
        json={
            "origin": {
                "address": "419 Boston Ave, Medford, MA",
                "lat": 42.4,
                "lon": -71.1,
            },
            "destination": {"lat": 42.41, "lon": -71.09},
            "mode": "pedestrian",
            "alternatives": 1,
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"


def test_geocode_request_rejects_blank_query() -> None:
    """Blank geocoding queries should be rejected before upstream calls."""

    response = client.post("/geocode", json={"query": "   "})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"


def test_cors_allows_capacitor_ios_origin() -> None:
    """Packaged iOS Capacitor WebViews should be allowed to call the API."""

    response = client.options(
        "/routes/generate-and-score",
        headers={
            "Origin": "capacitor://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "capacitor://localhost"


def test_cors_allows_ionic_ios_origin() -> None:
    """Older Ionic WebViews should also be allowed for demo installs."""

    response = client.options(
        "/routes/generate-and-score",
        headers={
            "Origin": "ionic://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "ionic://localhost"


def test_cors_allows_capacitor_android_origin() -> None:
    """Packaged Android Capacitor WebViews should be allowed to call the API."""

    response = client.options(
        "/routes/generate-and-score",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"
