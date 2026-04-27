"""Tests for Valhalla route normalization."""

from __future__ import annotations

from app.services.valhalla_service import normalize_valhalla_route_response


def test_normalize_valhalla_route_response_builds_geometry_first_route() -> None:
    """Normalized routes should include decoded shape, GeoJSON, and GIS payload."""

    coordinates = [
        [-71.100000, 42.400000],
        [-71.099500, 42.400500],
        [-71.098750, 42.401250],
    ]
    encoded = _encode_polyline6(coordinates)
    payload = {
        "trip": {
            "legs": [
                {
                    "shape": encoded,
                    "summary": {"length": 0.85, "time": 620},
                }
            ],
            "summary": {"length": 0.85, "time": 620},
        }
    }

    routes = normalize_valhalla_route_response(payload)

    assert len(routes) == 1
    route = routes[0]
    assert route.route_id == "route-1"
    assert route.distance_m == 850.0
    assert route.duration_s == 620.0
    assert route.encoded_polyline == encoded
    assert route.decoded_shape == coordinates
    assert route.geojson.type == "LineString"
    assert route.geojson.coordinates == coordinates
    assert route.polyline_payload.paths == [coordinates]
    assert route.polyline_payload.spatialReference.wkid == 4326
    assert route.summary == {"length": 0.85, "time": 620}


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
