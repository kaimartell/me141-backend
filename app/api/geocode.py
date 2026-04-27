"""Geocoding API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.geocode import GeocodeRequest, GeocodeResponse
from app.services.nominatim_service import NominatimService

router = APIRouter(tags=["geocode"])


def get_nominatim_service() -> NominatimService:
    """Return the geocoding service dependency."""

    return NominatimService()


@router.post("/geocode", response_model=GeocodeResponse)
async def geocode_address(
    request: GeocodeRequest,
    service: NominatimService = Depends(get_nominatim_service),
) -> GeocodeResponse:
    """Geocode a human-readable address query."""

    matches = await service.geocode(request.query)
    return GeocodeResponse(query=request.query, matches=matches)
