# Pedestrian Routing Prototype Backend

This project is a small FastAPI backend that sits between a frontend and upstream mapping services. It supports:

- geocoding address input with Nominatim
- generating pedestrian routes with Valhalla
- normalizing every returned route around route geometry for downstream GIS scoring
- debugging Valhalla correlation with a lightweight locate endpoint

The routing response is geometry-first. After decoding the Valhalla shape, the service consistently uses `[lon, lat]` ordering for:

- `decoded_shape`
- GeoJSON `LineString.coordinates`
- ArcGIS-style `polyline_payload.paths`

That ordering is preserved everywhere in the backend so downstream GIS scoring can treat the route geometry as the primary artifact.

## Project Structure

```text
app/
  main.py
  api/
    geocode.py
    routes.py
  services/
    arcgis_service.py
    nominatim_service.py
    polyline_utils.py
    scoring_service.py
    valhalla_service.py
  models/
    geocode.py
    routing.py
  core/
    config.py
    exceptions.py
    logging.py
  tests/
scripts/
  reset_valhalla.sh
  start_valhalla.sh
requirements.txt
.env.example
README.md
```

## Features

- `GET /health` for a simple health check
- `POST /geocode` for address lookup through Nominatim
- `POST /routes/generate` for pedestrian route generation from address or direct coordinate input
- `POST /routes/geocode-and-generate` as an alias of `/routes/generate`
- `POST /routes/debug-locate` for raw Valhalla locate/correlation output during prototype debugging
- `POST /routes/debug-rest-stops` for isolated ArcGIS rest-stop query debugging
- `POST /routes/score` for prototype ArcGIS-backed route scoring
- `POST /routes/generate-and-score` to generate routes and score each candidate in one call
- clean JSON errors with:
  - `400` for validation problems
  - `404` for geocoding misses
  - `502` for upstream integration failures

## Requirements

- Python 3.11+
- Docker if you want to run Valhalla locally
- A reachable Valhalla instance with usable map data

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Environment Variables

`.env` only tells the backend where upstream services live. It does not start Valhalla.

Example `.env`:

```env
NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
VALHALLA_BASE_URL=http://localhost:8002
ARCGIS_POI_URL=https://services3.arcgis.com/iuNbZYJOrAYBrPyC/arcgis/rest/services/survey123_7932a20fc6b14b7d9e48cbdb5e383a9c_results/FeatureServer/0/query
ARCGIS_BASEMAP_SERVICE_URL=https://services7.arcgis.com/UlEfxLrnpFcC1i8z/ArcGIS/rest/services/Tufts_University_Basemap/FeatureServer
ARCGIS_GRAVEL_LAYER_ID=20
ARCGIS_SIDEWALK_LAYER_ID=15
ARCGIS_PATH_LAYER_ID=14
ARCGIS_POI_CORRIDOR_DISTANCE_M=12
ARCGIS_REST_STOP_CORRIDOR_DISTANCE_M=18
ARCGIS_SURFACE_CORRIDOR_DISTANCE_M=8
ARCGIS_REST_STOP_URL=https://services3.arcgis.com/iuNbZYJOrAYBrPyC/arcgis/rest/services/survey123_e4187ac026344439a0cbbe2af967c1a7_results/FeatureServer/0/query
ARCGIS_REST_STOP_TOKEN=
VALHALLA_INTERNAL_CANDIDATE_COUNT=6
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
PROTOTYPE_MODE=true
```

Supported settings:

- `NOMINATIM_BASE_URL` default: `https://nominatim.openstreetmap.org`
- `VALHALLA_BASE_URL` default: `http://localhost:8002`
- `APP_HOST` default: `0.0.0.0`
- `APP_PORT` default: `8000`
- `LOG_LEVEL` default: `INFO`
- `PROTOTYPE_MODE` default: `true`
- `NOMINATIM_USER_AGENT`
- `NOMINATIM_LIMIT`
- `ARCGIS_POI_URL`
- `ARCGIS_BASEMAP_SERVICE_URL`
- `ARCGIS_GRAVEL_LAYER_ID`
- `ARCGIS_SIDEWALK_LAYER_ID`
- `ARCGIS_PATH_LAYER_ID`
- `ARCGIS_POI_CORRIDOR_DISTANCE_M` default: `12`
- `ARCGIS_REST_STOP_CORRIDOR_DISTANCE_M` default: `18`
- `ARCGIS_SURFACE_CORRIDOR_DISTANCE_M` default: `8`
- `ARCGIS_CORRIDOR_DISTANCE_M` legacy generic corridor used only by direct helper calls
- `ARCGIS_REST_STOP_URL`
- `ARCGIS_REST_STOP_TOKEN`
- `VALHALLA_INTERNAL_CANDIDATE_COUNT` default: `6`, capped internally at `8`
- `HTTP_TIMEOUT_S`

## Valhalla Local Setup

The backend expects a running Valhalla server. `.env` only points the backend at Valhalla. It does not start the container, download map data, or rebuild tiles.

Important:

- A running container can still be broken if it is serving stale or corrupt tiles.
- `curl /status` succeeding does not prove the local graph is valid.
- The documented default in this repo is a local Massachusetts `.osm.pbf` at `custom_files/massachusetts-latest.osm.pbf`.
- The image used consistently in scripts and docs is `ghcr.io/nilsnolde/docker-valhalla/valhalla:latest`.
- The first startup takes time because Valhalla must build fresh tiles.

### Why Clean Rebuilds Matter

If you reuse `custom_files/valhalla_tiles` or `custom_files/valhalla_tiles.tar`, Valhalla may start successfully while still serving a stale or corrupt graph. In the broken setup we observed:

- `use_tiles_ignore_pbf=True` caused the container to ignore the Massachusetts `.osm.pbf`
- `locate` returned `No data found for location`
- `/route` returned `No suitable edges near location`
- container logs showed `Invalid tile data size = 0. Tile file might be corrupted`

### Default Data Strategy

This repo now uses one explicit default path:

1. Keep `custom_files/massachusetts-latest.osm.pbf` locally.
2. Back up and remove stale build artifacts before startup.
3. Start `ghcr.io/nilsnolde/docker-valhalla/valhalla:latest` with forced rebuild settings.

### Clean Rebuild Sequence

Download the Massachusetts extract if it is missing:

```bash
mkdir -p custom_files
curl -L \
  -o custom_files/massachusetts-latest.osm.pbf \
  https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf
```

Reset stale build outputs while preserving the `.osm.pbf`:

```bash
./scripts/reset_valhalla.sh
```

Start Valhalla with a forced rebuild:

```bash
./scripts/start_valhalla.sh
```

The reset script:

- stops and removes the current `valhalla` container if it exists
- backs up stale outputs into `custom_files/_backup/<timestamp>/`
- preserves local `.osm.pbf` files

The start script:

- uses `custom_files/massachusetts-latest.osm.pbf` by default
- downloads that PBF if it is missing
- forces a clean tile rebuild instead of silently reusing old tiles
- starts `ghcr.io/nilsnolde/docker-valhalla/valhalla:latest`

### Watch Logs

```bash
docker logs -f valhalla
```

Wait until the build finishes and the service is accepting requests before testing routes.

### Check Valhalla Health

```bash
curl http://localhost:8002/status
```

### Test Valhalla Directly

This backend now uses Valhalla's local `GET /route?json=...` form with pedestrian-friendly snapping defaults:

- `radius=50`
- `minimum_reachability=1`
- `rank_candidates=true`

Example direct route request:

```bash
curl -G http://localhost:8002/route \
  --data-urlencode 'json={
    "locations":[
      {
        "lat":42.40852,
        "lon":-71.1183248,
        "radius":50,
        "minimum_reachability":1,
        "rank_candidates":true
      },
      {
        "lat":42.4154169,
        "lon":-71.1270758,
        "radius":50,
        "minimum_reachability":1,
        "rank_candidates":true
      }
    ],
    "costing":"pedestrian",
    "alternates":0,
    "shape_format":"polyline6"
  }'
```

This Tufts-area pair was validated successfully after a clean rebuild:

- origin: `42.40852, -71.1183248`
- destination: `42.4154169, -71.1270758`

Debug correlation directly with locate:

```bash
curl -G http://localhost:8002/locate \
  --data-urlencode 'json={
    "verbose":true,
    "costing":"pedestrian",
    "locations":[
      {
        "lat":42.40852,
        "lon":-71.1183248,
        "radius":50,
        "minimum_reachability":1,
        "rank_candidates":true
      },
      {
        "lat":42.4154169,
        "lon":-71.1270758,
        "radius":50,
        "minimum_reachability":1,
        "rank_candidates":true
      }
    ]
  }'
```

If `locate` returns edges and nodes, the graph is loaded and correlation works.

If `locate` returns `No data found for location`, or if `/route` still fails with `No suitable edges near location`, inspect the logs and rebuild cleanly before changing backend code.

If the container logs show `Invalid tile data size = 0. Tile file might be corrupted`, the local graph is suspect. Remove or back up the stale build outputs and rerun `./scripts/start_valhalla.sh`.

### Validation Checklist

After a clean rebuild, verify in this order:

1. `docker logs -f valhalla`
2. `curl http://localhost:8002/status`
3. `curl -G http://localhost:8002/locate --data-urlencode 'json=...'`
4. `curl -G http://localhost:8002/route --data-urlencode 'json=...'`
5. `curl -X POST http://localhost:8000/routes/generate ...`

Expected behavior:

- `/status` returns `200`
- `/locate` returns real `nodes` and `edges`
- `/route` returns a `trip`
- backend `/routes/generate` returns normalized route geometry

## Run the Backend Locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or use the values in your environment:

```bash
uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}" --reload
```

## API Examples

### Health Check

```bash
curl http://localhost:8000/health
```

### Geocode an Address

```bash
curl -X POST http://localhost:8000/geocode \
  -H "Content-Type: application/json" \
  -d '{
    "query": "419 Boston Ave, Medford, MA"
  }'
```

### Generate Routes From Addresses

```bash
curl -X POST http://localhost:8000/routes/generate \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {
      "address": "419 Boston Ave, Medford, MA"
    },
    "destination": {
      "address": "200 Boston Ave, Medford, MA"
    },
    "mode": "pedestrian",
    "alternatives": 1
  }'
```

### Generate Routes From Coordinates

```bash
curl -X POST http://localhost:8000/routes/generate \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {
      "lat": 42.40852,
      "lon": -71.1183248
    },
    "destination": {
      "lat": 42.4154169,
      "lon": -71.1270758
    },
    "mode": "pedestrian",
    "alternatives": 1
  }'
```

### Debug Valhalla Correlation Through the Backend

```bash
curl -X POST http://localhost:8000/routes/debug-locate \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {
      "lat": 42.40852,
      "lon": -71.1183248
    },
    "destination": {
      "lat": 42.4154169,
      "lon": -71.1270758
    },
    "mode": "pedestrian",
    "alternatives": 1
  }'
```

### Score a Generated Route or Raw Polyline

`POST /routes/score` accepts either:

- a full generated `route` object from `/routes/generate`
- or a `polyline_payload` in the ArcGIS-ready geometry format already used by the backend

Example scoring a direct polyline payload:

```bash
curl -X POST http://localhost:8000/routes/score \
  -H "Content-Type: application/json" \
  -d '{
    "route_id": "route-1",
    "polyline_payload": {
      "paths": [
        [
          [-71.1183248, 42.40852],
          [-71.1150, 42.4067]
        ]
      ],
      "spatialReference": {
        "wkid": 4326
      }
    },
    "distance_m": 1141.0,
    "duration_s": 809.812
  }'
```

### Debug Only the Rest-Stop Layer

```bash
curl -X POST http://localhost:8000/routes/debug-rest-stops \
  -H "Content-Type: application/json" \
  -d '{
    "route_id": "route-1",
    "polyline_payload": {
      "paths": [
        [
          [-71.1183248, 42.40852],
          [-71.1150, 42.4067]
        ]
      ],
      "spatialReference": {
        "wkid": 4326
      }
    }
  }'
```

### Generate and Score in One Call

```bash
curl -X POST http://localhost:8000/routes/generate-and-score \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {
      "address": "419 Boston Ave, Medford, MA"
    },
    "destination": {
      "address": "200 Boston Ave, Medford, MA"
    },
    "mode": "pedestrian",
    "alternatives": 1
  }'
```

## Response Shape

The route generation response stays geometry-first:

- `route_id`
- `distance_m`
- `duration_s`
- `encoded_polyline`
- `decoded_shape`
- `geojson`
- `polyline_payload`
- `summary`

Example route structure:

```json
{
  "route_id": "route-1",
  "distance_m": 850.0,
  "duration_s": 620.0,
  "encoded_polyline": "....",
  "decoded_shape": [
    [-71.1, 42.4],
    [-71.0995, 42.4005]
  ],
  "geojson": {
    "type": "LineString",
    "coordinates": [
      [-71.1, 42.4],
      [-71.0995, 42.4005]
    ]
  },
  "polyline_payload": {
    "paths": [
      [
        [-71.1, 42.4],
        [-71.0995, 42.4005]
      ]
    ],
    "spatialReference": {
      "wkid": 4326
    }
  },
  "summary": {
    "length": 0.85,
    "time": 620
  }
}
```

The scoring response is intentionally minimal and heuristic-based. It returns:

- raw route metrics such as `distance_m`, `duration_s`, `route_point_count`
- obstacle metrics from the ArcGIS POI layer
- approximate surface exposure from intersecting Tufts basemap polygons
- live rest-stop metrics from the configured Survey123 ArcGIS layer
- category scores, an overall score, and a short explanation string

Example scoring structure:

```json
{
  "route_id": "route-1",
  "metrics": {
    "distance_m": 1141.0,
    "duration_s": 809.812,
    "route_point_count": 36,
    "obstacle_count": 3,
    "crossing_issue_count": 1,
    "route_surface_types": ["SIDEWALK", "PATH"],
    "rest_stop_count": 0,
    "rest_stop_data_available": false
  },
  "category_scores": {
    "obstacles": 72.0,
    "crossings": 78.0,
    "surface": 81.0,
    "rest_support": 50.0,
    "efficiency": 84.0
  },
  "overall_score": 74.6,
  "explanation": "This route is mostly sidewalk/path with some obstacle burden remaining. It remains relatively efficient.",
  "raw_arcgis": {
    "pois": [],
    "surface_summary": {},
    "rest_stops": []
  }
}
```

## Scoring Notes

The current scoring layer is a prototype heuristic, not a validated accessibility model.

- The default prototype weighting currently emphasizes obstacle burden and rest support for early testing.
- Surface scoring is currently an approximation based on intersecting polygon features, not clipped segment lengths.
- Gravel, sidewalk, and path exposure are inferred from matched basemap features within a route corridor.
- ArcGIS scoring uses feature-specific corridor defaults: `12 m` for obstacle POIs, `18 m` for rest stops, and `8 m` for sidewalk/path/gravel surface layers.
- Scoring diagnostics under `raw_arcgis.diagnostics` include corridor distances, raw feature counts, and original versus scoring geometry point counts.
- Obstacle scoring now prioritizes severe barriers, especially `Not accessible` and `Safety hazard` issues.
- Rest-stop data is wired to the configured ArcGIS Survey123 layer using `what_kind_of_rest_stop_is_this` and `rest_quality`.
- If that layer requires ArcGIS authentication, set `ARCGIS_REST_STOP_TOKEN` so the backend can query it.
- Survey123 rest quality strings such as `3 = good` are parsed into numeric scores for scoring.
- Future versions can add additional scoring profiles without changing the geometry-first route flow.
- All scoring remains geometry-first so later GIS enrichment can build on the same `polyline_payload`.

## Rest Stop Layer Access

The rest-stop layer may be either public-read or token-protected. The backend supports both modes.

- If `ARCGIS_REST_STOP_TOKEN` is unset, the backend queries the rest-stop layer anonymously.
- If `ARCGIS_REST_STOP_TOKEN` is set, the backend includes it in the ArcGIS query request.
- If ArcGIS returns an auth failure such as `Token Required`, scoring continues with `rest_stop_data_available=false` and the response includes `rest_stop_source_status` so clients can distinguish access failures from valid zero-result queries.
- The backend currently uses `what_kind_of_rest_stop_is_this`, `rest_quality`, `CreationDate`, and `EditDate` from the Survey123 layer, and parses strings like `3 = good` into numeric rest-quality scores.

Troubleshooting:

- Survey123 and hosted feature layer sharing can change after republish.
- If rest-stop data suddenly disappears, verify the layer's anonymous query settings or refresh `ARCGIS_REST_STOP_TOKEN`.

## Notes on Valhalla Alternatives

The public API uses `alternatives` as the requested number of final route candidates. Internally, the backend now asks Valhalla for a larger candidate pool, deduplicates near-identical route geometries, and keeps only a small useful set in the frontend response.

Current behavior:

- if the client asks for one route, the backend still tries to generate several internal candidates
- near-identical candidate shapes are collapsed before scoring or returning routes
- `/routes/generate` returns only the requested count, capped at three routes
- `/routes/generate-and-score` scores all distinct internal candidates, sorts by score, and returns only the best requested count, capped at three routes
- candidate counts before and after deduplication are written to logs for validation

The service layer is structured so it can later expand to:

- multiple candidate generation strategies
- richer downstream GIS scoring integration on normalized geometry output

## Running Tests

```bash
pytest app/tests
```

## Prototype Scope

This prototype intentionally does not include:

- authentication
- caching
- a database
- background jobs

All HTTP calls are isolated in service classes to make later expansion simpler.
