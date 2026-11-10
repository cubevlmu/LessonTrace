#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
umask 077
domain="${1:-}"
if [[ ! "$domain" =~ ^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$ ]]; then
  echo 'Usage: sudo bash setup.sh your.actual.domain (domain only; no https://)'; exit 1
fi
docker compose version >/dev/null
if [[ "$(uname -m)" != x86_64 ]]; then echo 'This release requires an x86_64 / amd64 server.'; exit 1; fi
if [[ -e lesson_trace_data ]]; then echo 'Data already exists. No import performed; use docker compose up -d for restart.'; exit 1; fi
sha256sum -c data.tar.gz.sha256
tar -xzf data.tar.gz
chown -R 10001:10001 lesson_trace_data
chmod 700 lesson_trace_data
chmod 600 CREDENTIALS.private.json
image="$(cat IMAGE.txt)"
printf 'LESSONTRACE_IMAGE=%s\nINTICLASS_PUBLIC_ORIGIN=https://%s\n' "$image" "$domain" > .env
chmod 600 .env
if [[ -f lessontrace-image-amd64.tar.gz ]]; then
  sha256sum -c lessontrace-image-amd64.tar.gz.sha256
  docker load -i lessontrace-image-amd64.tar.gz
else
  docker compose pull api
fi
docker compose pull proxy
docker compose config --quiet
docker compose up -d --wait --wait-timeout 120
docker compose exec -T proxy nginx -t
docker compose ps
echo 'Local upstream ready: http://127.0.0.1:8788'
echo "Configure your HTTPS website $domain to reverse-proxy this upstream (see README)."
echo 'Judge credentials: CREDENTIALS.private.json. Keep this file outside your web root.'
