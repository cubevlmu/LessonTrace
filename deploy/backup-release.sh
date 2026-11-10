#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
umask 077
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose stop api
trap 'docker compose start api' EXIT
tar -czf "backups/lessontrace-$stamp.tar.gz" lesson_trace_data .env CREDENTIALS.private.json
sha256sum "backups/lessontrace-$stamp.tar.gz" > "backups/lessontrace-$stamp.tar.gz.sha256"
echo "Backup saved: backups/lessontrace-$stamp.tar.gz"
