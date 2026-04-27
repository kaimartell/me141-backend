"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the FastAPI service."""

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    prototype_mode: bool = Field(default=True, alias="PROTOTYPE_MODE")

    nominatim_base_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        alias="NOMINATIM_BASE_URL",
    )
    nominatim_user_agent: str = Field(
        default="pedestrian-routing-prototype/0.1",
        alias="NOMINATIM_USER_AGENT",
    )
    nominatim_limit: int = Field(default=5, alias="NOMINATIM_LIMIT")

    valhalla_base_url: str = Field(
        default="http://localhost:8002",
        alias="VALHALLA_BASE_URL",
    )

    arcgis_poi_url: str = Field(
        default=(
            "https://services3.arcgis.com/iuNbZYJOrAYBrPyC/arcgis/rest/services/"
            "survey123_7932a20fc6b14b7d9e48cbdb5e383a9c_results/FeatureServer/0/query"
        ),
        alias="ARCGIS_POI_URL",
    )
    arcgis_basemap_service_url: str = Field(
        default=(
            "https://services7.arcgis.com/UlEfxLrnpFcC1i8z/ArcGIS/rest/services/"
            "Tufts_University_Basemap/FeatureServer"
        ),
        alias="ARCGIS_BASEMAP_SERVICE_URL",
    )
    arcgis_gravel_layer_id: int = Field(default=20, alias="ARCGIS_GRAVEL_LAYER_ID")
    arcgis_sidewalk_layer_id: int = Field(default=15, alias="ARCGIS_SIDEWALK_LAYER_ID")
    arcgis_path_layer_id: int = Field(default=14, alias="ARCGIS_PATH_LAYER_ID")
    arcgis_corridor_distance_m: float = Field(
        default=10.0,
        alias="ARCGIS_CORRIDOR_DISTANCE_M",
    )
    arcgis_rest_stop_url: str | None = Field(
        default=(
            "https://services3.arcgis.com/iuNbZYJOrAYBrPyC/arcgis/rest/services/"
            "survey123_e4187ac026344439a0cbbe2af967c1a7/FeatureServer/0/query"
        ),
        alias="ARCGIS_REST_STOP_URL",
    )
    arcgis_rest_stop_token: str | None = Field(
        default=None,
        alias="ARCGIS_REST_STOP_TOKEN",
    )

    http_timeout_s: float = Field(default=15.0, alias="HTTP_TIMEOUT_S")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def nominatim_search_url(self) -> str:
        """Return the Nominatim search endpoint."""

        return f"{self.nominatim_base_url.rstrip('/')}/search"

    @property
    def valhalla_route_url(self) -> str:
        """Return the Valhalla route endpoint."""

        return f"{self.valhalla_base_url.rstrip('/')}/route"

    @property
    def valhalla_locate_url(self) -> str:
        """Return the Valhalla locate endpoint."""

        return f"{self.valhalla_base_url.rstrip('/')}/locate"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    return Settings()
