# ── Stage 1: dependency builder ──────────────────────────────────────────────
# Use full Python image to compile any C extensions (bcrypt, etc.)
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
# Works on both ARM64 (Apple Silicon) and x86_64 — installs ClamAV via apt,
# so no architecture-specific Docker image is needed.
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="yeet"
LABEL org.opencontainers.image.description="Minimal secure file sharing"
LABEL org.opencontainers.image.source="https://github.com/majmohar4/yeet"

# Install ClamAV (apt packages work on both ARM64 and x86_64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    clamav \
    clamav-daemon \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and data directories
RUN useradd --system --uid 1000 --create-home yeet && \
    mkdir -p /data/uploads /data/archive /data/partial /data/clamav-db && \
    chown -R yeet:yeet /data && \
    # Give yeet ownership of ClamAV runtime dirs so clamd/freshclam can write logs/pid
    chown -R yeet:yeet /var/log/clamav /var/run/clamav 2>/dev/null || true

# Configure clamd: TCP on localhost, custom DB path, no privilege drop, no local socket
RUN sed -i \
        -e 's|^LocalSocket|#LocalSocket|' \
        -e 's|^User |#User |' \
        /etc/clamav/clamd.conf && \
    # clamd keeps the FIRST occurrence of a setting, so delete before appending.
    # Size limits must cover the largest upload (MAX_FILE_SIZE is runtime-tunable):
    # the stock 25M StreamMaxLength made every bigger upload fail with a 503.
    # 4000M is just under clamd's 4 GB ceiling. MaxScanTime is raised from 120s
    # because a multi-GB video can legitimately take minutes.
    sed -i -E '/^(DatabaseDirectory|Foreground|StreamMaxLength|MaxFileSize|MaxScanSize|MaxScanTime|ConcurrentDatabaseReload) /d' \
        /etc/clamav/clamd.conf && \
    printf '\n# yeet overrides\nDatabaseDirectory /data/clamav-db\nTCPSocket 3310\nTCPAddr 127.0.0.1\nForeground yes\nStreamMaxLength 4000M\nMaxFileSize 4000M\nMaxScanSize 4000M\nMaxScanTime 600000\nConcurrentDatabaseReload no\n' \
        >> /etc/clamav/clamd.conf

# Configure freshclam: same DB path, no user-switch
RUN sed -i '/^DatabaseDirectory/d' /etc/clamav/freshclam.conf && \
    { grep -q '^DatabaseOwner' /etc/clamav/freshclam.conf && \
      sed -i 's|^DatabaseOwner .*|DatabaseOwner yeet|' /etc/clamav/freshclam.conf || \
      echo 'DatabaseOwner yeet' >> /etc/clamav/freshclam.conf; } && \
    # The app restarts clamd after updating; a RELOAD would double its memory.
    sed -i '/^NotifyClamd/d' /etc/clamav/freshclam.conf && \
    printf '\nDatabaseDirectory /data/clamav-db\n' >> /etc/clamav/freshclam.conf

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=yeet:yeet app/       ./app/
COPY --chown=yeet:yeet templates/ ./templates/
COPY --chown=yeet:yeet .env.example .

# Admin CLI — `docker exec yeet yeet <command>`
COPY --chmod=755 yeet /usr/local/bin/yeet

# Startup script: runs freshclam, starts clamd, then starts uvicorn
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user — clamd and freshclam run as yeet too
USER yeet

# Data directory — uploads, database, and ClamAV definitions live here
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["/entrypoint.sh"]
