#!/bin/bash
# Brain graphs backup — tars the brain /graphs volume (graph.json per repo + memory).
#
# Follows the ds6c backup mold (see backups/scripts/backup-grafana.sh):
#   * deliberately does NOT use `set -euo pipefail` — the ds6c convention is to
#     log failures and continue so one bad step never silently aborts the run.
#   * logs to /opt/backups/logs/brain-graphs.log
#   * stages in /opt/backups/tmp/brain-graphs/, tars, rclones to gdrive, cleans up.
#
# Cron entry (in ds6c/backups/etc-cron-d-ds6c-backups):
#   30 4 * * * root /opt/backups/scripts/backup-brain-graphs.sh
#
# The brain /graphs volume is the Coolify-managed named volume mounted at
# /graphs inside the brain container. It holds <repo>/graphify-out/graph.json
# per repo plus the /graphs/memory tree. This is the brain's durable state —
# repos themselves are re-clonable, but rebuilt graphs + memory are not.
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)
BACKUP_DIR="/opt/backups/tmp/brain-graphs"
BACKUP_FILE="brain-graphs-${TIMESTAMP}.tar.gz"
LOG_FILE="/opt/backups/logs/brain-graphs.log"

# Resolve the brain graphs volume path on the host.
# Coolify mounts named volumes under /var/lib/docker/volumes/. The exact volume
# name is Coolify-generated; we glob for the brain app's graphs volume. If the
# layout differs on this host, set GRAPHS_VOLUME_DIR below explicitly.
GRAPHS_VOLUME_DIR="${BRAIN_GRAPHS_VOLUME_DIR:-$(docker volume ls --format '{{.Mountpoint}}' --filter name=brain 2>/dev/null | grep -E 'graphs$' | head -1)}"

mkdir -p "${BACKUP_DIR}"

echo "[$(date)] Starting brain graphs backup (volume=${GRAPHS_VOLUME_DIR:-<unresolved>})..." >> "$LOG_FILE"

if [ -z "${GRAPHS_VOLUME_DIR}" ] || [ ! -d "${GRAPHS_VOLUME_DIR}" ]; then
    echo "[$(date)] WARNING: brain graphs volume not found via 'docker volume ls'. Falling back to in-container copy." >> "$LOG_FILE"
    # Fallback: docker cp the /graphs tree out of a running brain container.
    BRAIN_CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'brain' | head -1)
    if [ -n "${BRAIN_CONTAINER}" ]; then
        docker cp "${BRAIN_CONTAINER}:/graphs/." "${BACKUP_DIR}/graphs/" 2>> "$LOG_FILE"
    else
        echo "[$(date)] ERROR: no brain container running and no graphs volume resolved — backup aborted" >> "$LOG_FILE"
        exit 1
    fi
else
    # Copy the volume (cp -a preserves perms + symlinks) into the staging dir.
    cp -a "${GRAPHS_VOLUME_DIR}/." "${BACKUP_DIR}/graphs/" 2>> "$LOG_FILE"
fi

# Tar the staged graphs tree.
cd "${BACKUP_DIR}" && tar -czf "${BACKUP_FILE}" graphs/ 2>> "$LOG_FILE"

if [ $? -eq 0 ]; then
    SIZE=$(du -sh "${BACKUP_DIR}/${BACKUP_FILE}" | cut -f1)
    echo "[$(date)] Brain graphs backup OK: ${BACKUP_FILE} (${SIZE})" >> "$LOG_FILE"

    rclone copy "${BACKUP_DIR}/${BACKUP_FILE}" gdrive:ds6c-backups/infrastructure/brain-graphs/ --log-file="$LOG_FILE" --log-level INFO
    rm -rf "${BACKUP_DIR}/${BACKUP_FILE}" "${BACKUP_DIR}/graphs"
    echo "[$(date)] Brain graphs backup uploaded and cleaned up" >> "$LOG_FILE"
else
    echo "[$(date)] ERROR: brain graphs tar failed" >> "$LOG_FILE"
    rm -rf "${BACKUP_DIR}/graphs" 2>/dev/null
    exit 1
fi
