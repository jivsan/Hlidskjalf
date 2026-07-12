# syntax=docker/dockerfile:1

# Hlidskjalf — non-Nix deployment image.
# Multi-stage: build the React SPA, install the FastAPI backend, serve the SPA.
# See docs/docker.md for the quickstart.

# --- Stage 1: build the React SPA -------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend

# Install deps against the lockfile first so this layer caches across source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the SPA -> /app/frontend/dist
COPY frontend/ ./
RUN npm run build

# --- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root service account + a state dir it owns (sqlite: bandwidth history,
# rescue boot-order stash). HLIDSKJALF_STATE_DIR points here.
RUN groupadd --system hlidskjalf \
 && useradd --system --gid hlidskjalf --home-dir /var/lib/hlidskjalf \
        --shell /usr/sbin/nologin hlidskjalf \
 && mkdir -p /var/lib/hlidskjalf \
 && chown hlidskjalf:hlidskjalf /var/lib/hlidskjalf

# Install the backend package (console script `hlidskjalf`). manylinux wheels
# cover every dependency (pydantic-core, argon2-cffi/cffi) so no build toolchain
# is needed in the final image.
COPY backend/ /app/backend/
RUN pip install --no-cache-dir /app/backend

# Ship the built SPA and point the backend at it.
COPY --from=frontend /app/frontend/dist /app/static

ENV HLIDSKJALF_HOST=0.0.0.0 \
    HLIDSKJALF_PORT=8787 \
    HLIDSKJALF_STATIC_DIR=/app/static \
    HLIDSKJALF_STATE_DIR=/var/lib/hlidskjalf \
    PYTHONUNBUFFERED=1

EXPOSE 8787
VOLUME ["/var/lib/hlidskjalf"]

USER hlidskjalf

# No curl in the image — a stdlib one-liner exits non-zero unless /api/health 200s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health').read()"]

# CMD (not ENTRYPOINT) so `docker run <img> python -c ...` can override it to run
# one-off helpers (e.g. generating an argon2 password hash — see docs/docker.md).
CMD ["hlidskjalf"]
