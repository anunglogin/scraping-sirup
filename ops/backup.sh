#!/bin/sh
# Daily pg_dump loop. Runs in the postgres:16 image (see docker-compose.yml).
# Every 10 minutes: if it is past BACKUP_HOUR (WIB) and today's dump does not
# exist yet, create it (write to .tmp, then rename so a half-written file is
# never mistaken for a good backup). Old dumps are pruned after
# BACKUP_KEEP_DAYS days. A failed dump is logged and retried on the next tick;
# the loop itself never exits, so the container does not crash-loop.
set -u

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_HOUR="${BACKUP_HOUR:-4}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-600}"

mkdir -p "$BACKUP_DIR"
echo "backup loop started: hour>=${BACKUP_HOUR} keep=${BACKUP_KEEP_DAYS}d dir=${BACKUP_DIR}"

while true; do
  today="$(date +%F)"
  hour="$(date +%H)"
  hour="${hour#0}"
  file="${BACKUP_DIR}/sirup-${today}.dump"

  if [ "$hour" -ge "$BACKUP_HOUR" ] && [ ! -f "$file" ]; then
    echo "$(date -Iseconds) creating ${file}"
    if pg_dump --format=custom --no-owner --file="${file}.tmp"; then
      mv "${file}.tmp" "$file"
      echo "$(date -Iseconds) backup ok: $(du -h "$file" | cut -f1)"
      find "$BACKUP_DIR" -maxdepth 1 -name 'sirup-*.dump' -type f \
        -mtime +"$((BACKUP_KEEP_DAYS - 1))" -print -delete
    else
      rm -f "${file}.tmp"
      echo "$(date -Iseconds) BACKUP FAILED, will retry in ${INTERVAL_SECONDS}s" >&2
    fi
  fi
  sleep "$INTERVAL_SECONDS"
done
