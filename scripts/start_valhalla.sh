#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CUSTOM_DIR="${ROOT_DIR}/custom_files"
CONTAINER_NAME="${VALHALLA_CONTAINER_NAME:-valhalla}"
VALHALLA_IMAGE="${VALHALLA_IMAGE:-ghcr.io/nilsnolde/docker-valhalla/valhalla:latest}"
PBF_NAME="${VALHALLA_PBF_NAME:-massachusetts-latest.osm.pbf}"
PBF_URL="${VALHALLA_PBF_URL:-https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf}"
PBF_PATH="${CUSTOM_DIR}/${PBF_NAME}"

mkdir -p "${CUSTOM_DIR}"

if [[ ! -f "${PBF_PATH}" ]]; then
  echo "Downloading ${PBF_URL}"
  curl -L --fail --output "${PBF_PATH}" "${PBF_URL}"
else
  echo "Using existing PBF: ${PBF_PATH}"
fi

"${ROOT_DIR}/scripts/reset_valhalla.sh"

echo "Starting ${CONTAINER_NAME} from ${VALHALLA_IMAGE}"
docker run -dt \
  --name "${CONTAINER_NAME}" \
  -p 8002:8002 \
  -v "${CUSTOM_DIR}:/custom_files" \
  -e use_tiles_ignore_pbf=False \
  -e force_rebuild=True \
  -e build_tar=Force \
  -e build_admins=Force \
  -e build_time_zones=Force \
  -e serve_tiles=True \
  -e update_existing_config=True \
  "${VALHALLA_IMAGE}" \
  build_tiles >/dev/null

echo "Container started. Watch progress with:"
echo "  docker logs -f ${CONTAINER_NAME}"
