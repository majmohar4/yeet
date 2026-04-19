#!/bin/sh
# Entrypoint: starts ClamAV (freshclam + clamd), then the FastAPI app.
# Runs as the yeet user (uid 1000); clamd inherits the same user.
set -e

mkdir -p /data/clamav-db

echo "[yeet] Updating ClamAV virus definitions (may take ~2 min on first run)..."
freshclam --quiet 2>/dev/null || {
    echo "[yeet] WARNING: freshclam update skipped — will use cached definitions if present."
}

echo "[yeet] Starting clamd..."
clamd &

echo "[yeet] Waiting for clamd to accept connections..."
i=0
while [ "$i" -lt 60 ]; do
    if python3 - <<'PYEOF' 2>/dev/null
import socket, sys
try:
    s = socket.socket()
    s.settimeout(1)
    s.connect(('127.0.0.1', 3310))
    s.sendall(b'zPING\0')
    r = s.recv(10)
    s.close()
    sys.exit(0 if r == b'PONG\0' else 1)
except Exception:
    sys.exit(1)
PYEOF
    then
        echo "[yeet] clamd ready."
        break
    fi
    sleep 2
    i=$((i + 1))
done

echo "[yeet] Starting FastAPI app..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    --access-log \
    --log-level info
