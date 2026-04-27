#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CUSTOM_DIR="${ROOT_DIR}/custom_files"
BACKUP_ROOT="${CUSTOM_DIR}/_backup"
CONTAINER_NAME="${VALHALLA_CONTAINER_NAME:-valhalla}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"

mkdir -p "${CUSTOM_DIR}"

if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "Stopping and removing container: ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

mkdir -p "${BACKUP_DIR}"

shopt -s nullglob
artifacts=(
  "${CUSTOM_DIR}/valhalla_tiles"
  "${CUSTOM_DIR}/valhalla_tiles.tar"
  "${CUSTOM_DIR}/valhalla.json"
  "${CUSTOM_DIR}/file_hashes.txt"
  "${CUSTOM_DIR}/duplicateways.txt"
  "${CUSTOM_DIR}/admin_data"
  "${CUSTOM_DIR}/timezone_data"
  "${CUSTOM_DIR}/elevation_data"
  "${CUSTOM_DIR}/transit_tiles"
  "${CUSTOM_DIR}/traffic.tar"
)
shopt -u nullglob

moved_any=0
for artifact in "${artifacts[@]}"; do
  if [[ -e "${artifact}" ]]; then
    echo "Backing up $(basename "${artifact}") to ${BACKUP_DIR}"
    mv "${artifact}" "${BACKUP_DIR}/"
    moved_any=1
  fi
done

if [[ "${moved_any}" -eq 0 ]]; then
  echo "No stale Valhalla build artifacts found."
  rmdir "${BACKUP_DIR}" 2>/dev/null || true
else
  echo "Backed up stale artifacts to ${BACKUP_DIR}"
fi

echo "Preserved PBF files:"
find "${CUSTOM_DIR}" -maxdepth 1 -type f -name '*.osm.pbf' -print | sort
