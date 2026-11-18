#!/bin/sh
# Entrypoint: refreshes ClamAV definitions, then starts the FastAPI app.
# Runs as the yeet user (uid 1000); clamd inherits the same user.
set -e

mkdir -p /data/clamav-db

echo "[yeet] Updating ClamAV virus definitions (may take ~2 min on first run)..."
freshclam --quiet 2>/dev/null || {
    echo "[yeet] WARNING: freshclam update skipped — will use cached definitions if present."
}

# clamd is started and stopped by the app (app/services/virus_scan.py).

echo "[yeet] Starting FastAPI app..."
# Only the reverse proxy may set X-Forwarded-*. With '*' uvicorn rewrites
# request.client to the LEFT-MOST X-Forwarded-For entry — which the caller
# writes — so any client could pick its own identity. The container publishes
# on 127.0.0.1 only, so the peer is always the docker gateway.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips="$FORWARDED_ALLOW_IPS" \
    --access-log \
    --log-level info
