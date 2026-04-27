"""Pydantic models for route generation requests and responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LocationInput(BaseModel):
    """Address or coordinate input for route endpoints."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("address")
    @classmethod
    def strip_address(cls, value: str | None) -> str | None:
        """Normalize blank addresses to validation errors."""

        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("address must not be blank")
        return cleaned

    @model_validator(mode="after")
    def validate_address_or_coordinates(self) -> "LocationInput":
        """Require exactly one addressing strategy per location."""

        has_address = self.address is not None
        has_lat = self.lat is not None
        has_lon = self.lon is not None

        if has_lat != has_lon:
            raise ValueError("lat and lon must be provided together")

        has_coordinates = has_lat and has_lon

        if has_address and has_coordinates:
            raise ValueError("provide either address or coordinates, not both")

        if not has_address and not has_coordinates:
            raise ValueError("provide either address or coordinates")

        return self


class RouteGenerationRequest(BaseModel):
    """Request payload for walking route generation."""

    model_config = ConfigDict(extra="forbid")

    origin: LocationInput
    destination: LocationInput
    mode: Literal["pedestrian"] = "pedestrian"
    alternatives: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Requested number of route candidates.",
    )


class ResolvedLocation(BaseModel):
    """Normalized resolved location used in route generation."""

    lat: float
    lon: float
    source: str
    address: str | None = None
    display_name: str | None = None


class GeoJSONLineString(BaseModel):
    """GeoJSON LineString using [lon, lat] coordinate ordering."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class SpatialReference(BaseModel):
    """ArcGIS spatial reference definition."""

    wkid: int = 4326


class PolylinePayload(BaseModel):
    """ArcGIS-style polyline payload using [lon, lat] coordinate ordering."""

    paths: list[list[list[float]]]
    spatialReference: SpatialReference = Field(default_factory=SpatialReference)


class RouteCandidate(BaseModel):
    """Normalized route candidate returned to the frontend."""

    route_id: str
    distance_m: float
    duration_s: float
    encoded_polyline: str
    decoded_shape: list[list[float]]
    geojson: GeoJSONLineString
    polyline_payload: PolylinePayload
    summary: dict[str, Any] = Field(default_factory=dict)


class RouteGenerationResponse(BaseModel):
    """Response payload for route generation."""

    origin: ResolvedLocation
    destination: ResolvedLocation
    mode: Literal["pedestrian"]
    requested_alternatives: int
    routes: list[RouteCandidate]


class RouteLocateDebugResponse(BaseModel):
    """Response payload for the Valhalla locate debug endpoint."""

    origin: ResolvedLocation
    destination: ResolvedLocation
    locate_result: Any


class RouteScoreRequest(BaseModel):
    """Request payload for route scoring from either a route or raw geometry."""

    model_config = ConfigDict(extra="forbid")

    route: RouteCandidate | None = None
    polyline_payload: PolylinePayload | None = None
    route_id: str | None = None
    distance_m: float | None = Field(default=None, ge=0)
    duration_s: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_score_source(self) -> "RouteScoreRequest":
        """Require exactly one scoring input source."""

        has_route = self.route is not None
        has_polyline = self.polyline_payload is not None

        if has_route and has_polyline:
            raise ValueError("provide either route or polyline_payload, not both")

        if not has_route and not has_polyline:
            raise ValueError("provide either route or polyline_payload")

        return self


class RouteScoreMetrics(BaseModel):
    """Raw route metrics used by the prototype scoring heuristic."""

    distance_m: float
    duration_s: float
    route_point_count: int
    obstacle_count: int
    obstacle_severity_counts: dict[str, int] = Field(default_factory=dict)
    cognitive_obstacle_count: int
    crossing_issue_count: int
    weighted_obstacle_penalty: float
    route_surface_types: list[str] = Field(default_factory=list)
    has_gravel: bool
    has_sidewalk: bool
    has_path: bool
    matched_segment_count: int
    matched_surface_feature_counts: dict[str, int] = Field(default_factory=dict)
    surface_presence: dict[str, float] = Field(default_factory=dict)
    rest_stop_count: int
    avg_rest_quality: float | None = None
    rest_stop_types: list[str] = Field(default_factory=list)
    rest_stop_data_available: bool


class RestStopSourceStatus(BaseModel):
    """Status of the live rest-stop data source for a route corridor query."""

    configured: bool
    queried: bool
    authenticated: bool
    available: bool
    reason: str


class RouteCategoryScores(BaseModel):
    """Prototype category-level scores normalized to 0-100."""

    obstacles: float
    crossings: float
    surface: float
    efficiency: float
    rest_support: float


class RouteScoreRawArcGIS(BaseModel):
    """Raw ArcGIS query output included for prototype debugging."""

    pois: list[dict[str, Any]] = Field(default_factory=list)
    surface_summary: dict[str, Any] = Field(default_factory=dict)
    rest_stops: list[dict[str, Any]] = Field(default_factory=list)


class RouteScoreResponse(BaseModel):
    """Response payload for route scoring."""

    route_id: str
    metrics: RouteScoreMetrics
    category_scores: RouteCategoryScores
    overall_score: float
    explanation: str
    raw_arcgis: RouteScoreRawArcGIS
    rest_stop_source_status: RestStopSourceStatus


class RouteRestStopsDebugResponse(BaseModel):
    """Response payload for targeted rest-stop debugging."""

    route_id: str
    rest_stops: list[dict[str, Any]] = Field(default_factory=list)
    raw_feature_count: int
    rest_stop_source_status: RestStopSourceStatus


class ScoredRouteCandidate(RouteCandidate):
    """Route candidate enriched with scoring output."""

    score: RouteScoreResponse


class RouteGenerationScoredResponse(RouteGenerationResponse):
    """Route generation response with score data attached to each route."""

    routes: list[ScoredRouteCandidate]
