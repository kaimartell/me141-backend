"""Routing API routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends

from app.api.geocode import get_nominatim_service
from app.models.routing import (
    LocationInput,
    ResolvedLocation,
    RouteGenerationScoredResponse,
    RouteGenerationRequest,
    RouteGenerationResponse,
    RouteLocateDebugResponse,
    RouteRestStopsDebugResponse,
    RouteScoreRequest,
    RouteScoreResponse,
    ScoredRouteCandidate,
)
from app.services.arcgis_service import ArcGISService
from app.services.nominatim_service import NominatimService
from app.services.scoring_service import ScoringService
from app.services.valhalla_service import ValhallaService

router = APIRouter(prefix="/routes", tags=["routes"])
logger = logging.getLogger(__name__)


def get_valhalla_service() -> ValhallaService:
    """Return the routing service dependency."""

    return ValhallaService()


def get_arcgis_service() -> ArcGISService:
    """Return the ArcGIS query service dependency."""

    return ArcGISService()


def get_scoring_service(
    arcgis_service: ArcGISService = Depends(get_arcgis_service),
) -> ScoringService:
    """Return the route scoring service dependency."""

    return ScoringService(arcgis_service=arcgis_service)


@router.post("/generate", response_model=RouteGenerationResponse)
@router.post("/geocode-and-generate", response_model=RouteGenerationResponse)
async def generate_routes(
    request: RouteGenerationRequest,
    nominatim_service: NominatimService = Depends(get_nominatim_service),
    valhalla_service: ValhallaService = Depends(get_valhalla_service),
) -> RouteGenerationResponse:
    """Resolve locations and generate geometry-first walking routes."""

    origin = await _resolve_location(request.origin, nominatim_service)
    destination = await _resolve_location(request.destination, nominatim_service)
    generation = await valhalla_service.generate_route_candidates(
        origin=origin,
        destination=destination,
        mode=request.mode,
        alternatives=request.alternatives,
    )

    return RouteGenerationResponse(
        origin=origin,
        destination=destination,
        mode=request.mode,
        requested_alternatives=request.alternatives,
        routes=generation.routes,
    )


@router.post("/debug-locate", response_model=RouteLocateDebugResponse)
async def debug_locate(
    request: RouteGenerationRequest,
    nominatim_service: NominatimService = Depends(get_nominatim_service),
    valhalla_service: ValhallaService = Depends(get_valhalla_service),
) -> RouteLocateDebugResponse:
    """Resolve locations and return raw Valhalla locate correlation output."""

    origin = await _resolve_location(request.origin, nominatim_service)
    destination = await _resolve_location(request.destination, nominatim_service)
    locate_result = await valhalla_service.locate(
        locations=[origin, destination],
        mode=request.mode,
    )

    return RouteLocateDebugResponse(
        origin=origin,
        destination=destination,
        locate_result=locate_result,
    )


@router.post("/score", response_model=RouteScoreResponse)
async def score_route(
    request: RouteScoreRequest,
    scoring_service: ScoringService = Depends(get_scoring_service),
) -> RouteScoreResponse:
    """Score a route object or ArcGIS-ready polyline payload."""

    return await scoring_service.score_request(request)


@router.post("/debug-rest-stops", response_model=RouteRestStopsDebugResponse)
async def debug_rest_stops(
    request: RouteScoreRequest,
    arcgis_service: ArcGISService = Depends(get_arcgis_service),
) -> RouteRestStopsDebugResponse:
    """Query only the rest-stop layer for a provided route geometry."""

    polyline_payload = _extract_polyline_payload(request)
    rest_stop_result = await arcgis_service.query_rest_stops(polyline_payload)

    return RouteRestStopsDebugResponse(
        route_id=request.route.route_id if request.route else (request.route_id or "scored-route"),
        rest_stops=rest_stop_result["rest_stops"],
        raw_feature_count=rest_stop_result["raw_feature_count"],
        rest_stop_source_status=rest_stop_result["source_status"],
    )


@router.post("/generate-and-score", response_model=RouteGenerationScoredResponse)
async def generate_and_score_routes(
    request: RouteGenerationRequest,
    nominatim_service: NominatimService = Depends(get_nominatim_service),
    valhalla_service: ValhallaService = Depends(get_valhalla_service),
    scoring_service: ScoringService = Depends(get_scoring_service),
) -> RouteGenerationScoredResponse:
    """Generate walking routes and attach prototype scoring to each candidate."""

    origin = await _resolve_location(request.origin, nominatim_service)
    destination = await _resolve_location(request.destination, nominatim_service)
    generation = await valhalla_service.generate_route_candidates(
        origin=origin,
        destination=destination,
        mode=request.mode,
        alternatives=request.alternatives,
    )
    scores = await asyncio.gather(
        *[
            scoring_service.score_request(RouteScoreRequest(route=route))
            for route in generation.distinct_candidates
        ]
    )

    scored_routes = [
        ScoredRouteCandidate(**route.model_dump(), score=score)
        for route, score in zip(generation.distinct_candidates, scores)
    ]
    scored_routes.sort(key=lambda route: route.score.overall_score, reverse=True)
    scored_routes = scored_routes[: generation.diagnostics.returned_route_count]
    logger.info(
        "Route generate-and-score completed. requested_alternatives=%s "
        "candidate_count_before_dedupe=%s candidate_count_after_dedupe=%s "
        "returned_route_count=%s",
        generation.diagnostics.requested_alternatives,
        generation.diagnostics.raw_candidate_count,
        generation.diagnostics.distinct_candidate_count,
        len(scored_routes),
    )

    return RouteGenerationScoredResponse(
        origin=origin,
        destination=destination,
        mode=request.mode,
        requested_alternatives=request.alternatives,
        routes=scored_routes,
    )


async def _resolve_location(
    location: LocationInput,
    nominatim_service: NominatimService,
) -> ResolvedLocation:
    """Resolve an address to coordinates or pass through direct coordinates."""

    if location.address is not None:
        matches = await nominatim_service.geocode(location.address, limit=1)
        match = matches[0]
        return ResolvedLocation(
            lat=match.lat,
            lon=match.lon,
            source=match.source,
            address=location.address,
            display_name=match.display_name,
        )

    return ResolvedLocation(
        lat=location.lat,
        lon=location.lon,
        source="input_coordinates",
    )


def _extract_polyline_payload(request: RouteScoreRequest):
    """Return the ArcGIS-ready polyline payload from a scoring request."""

    if request.route is not None:
        return request.route.polyline_payload
    return request.polyline_payload
