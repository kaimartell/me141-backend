"""Utilities for working with Valhalla polyline geometry."""

from __future__ import annotations

from typing import Any


def decode_polyline6(encoded_polyline: str) -> list[list[float]]:
    """Decode a Valhalla polyline6 string into [lon, lat] coordinate pairs."""

    if not encoded_polyline:
        return []

    coordinates: list[list[float]] = []
    index = 0
    latitude = 0
    longitude = 0
    factor = 1_000_000.0

    while index < len(encoded_polyline):
        lat_delta, index = _decode_value(encoded_polyline, index)
        lon_delta, index = _decode_value(encoded_polyline, index)

        latitude += lat_delta
        longitude += lon_delta
        coordinates.append([longitude / factor, latitude / factor])

    return coordinates


def _decode_value(encoded_polyline: str, index: int) -> tuple[int, int]:
    """Decode a single signed coordinate delta from an encoded polyline."""

    result = 0
    shift = 0

    while True:
        if index >= len(encoded_polyline):
            raise ValueError("Invalid encoded polyline string")

        byte = ord(encoded_polyline[index]) - 63
        index += 1
        result |= (byte & 0x1F) << shift
        shift += 5

        if byte < 0x20:
            break

    delta = ~(result >> 1) if result & 1 else (result >> 1)
    return delta, index


def to_geojson_linestring(coordinates: list[list[float]]) -> dict[str, Any]:
    """Convert [lon, lat] coordinates to a GeoJSON LineString."""

    return {
        "type": "LineString",
        "coordinates": _copy_coordinates(coordinates),
    }


def to_arcgis_polyline_payload(coordinates: list[list[float]]) -> dict[str, Any]:
    """Convert [lon, lat] coordinates to an ArcGIS-style polyline payload."""

    return {
        "paths": [_copy_coordinates(coordinates)],
        "spatialReference": {"wkid": 4326},
    }


def _copy_coordinates(coordinates: list[list[float]]) -> list[list[float]]:
    """Copy coordinate arrays to avoid mutating shared references."""

    return [[lon, lat] for lon, lat in coordinates]
