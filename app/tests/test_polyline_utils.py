"""Tests for geometry utility helpers."""

from __future__ import annotations

from app.services.polyline_utils import (
    decode_polyline6,
    to_arcgis_polyline_payload,
    to_geojson_linestring,
)


def test_decode_polyline6_round_trip() -> None:
    """Polyline6 decoding should preserve [lon, lat] ordering."""

    coordinates = [
        [-71.100000, 42.400000],
        [-71.099500, 42.400500],
        [-71.098750, 42.401250],
    ]
    encoded = _encode_polyline6(coordinates)

    decoded = decode_polyline6(encoded)

    assert decoded == coordinates


def test_geometry_helpers_share_consistent_lon_lat_ordering() -> None:
    """GeoJSON and ArcGIS payloads should use identical coordinate ordering."""

    coordinates = [
        [-71.100000, 42.400000],
        [-71.099500, 42.400500],
    ]

    geojson = to_geojson_linestring(coordinates)
    polyline_payload = to_arcgis_polyline_payload(coordinates)

    assert geojson["coordinates"] == coordinates
    assert polyline_payload["paths"][0] == coordinates
    assert polyline_payload["spatialReference"]["wkid"] == 4326


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
