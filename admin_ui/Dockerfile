# Stage 1: Build Frontend
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
# Install all deps including devDependencies for build (vite, typescript, etc.)
RUN npm ci || npm install
ARG CACHEBUST=1
COPY frontend/ .
RUN npm run build
# Note: devDependencies are only in builder stage, not in final image

# Stage 2: Backend + Runtime
FROM python:3.11-slim-bookworm
WORKDIR /app

# Optimization env vars
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install Docker CLI and Compose plugin for container management
# This allows admin-ui to start/stop/restart ai-engine and local-ai-server
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    build-essential \
    python3-dev \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (bcrypt has manylinux wheels, no gcc needed)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built frontend assets to backend's static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose default port (can be overridden by UVICORN_PORT)
EXPOSE 3003

# Run the application
# NOTE: Binds to 0.0.0.0 by default for remote accessibility (common for Asterisk servers).
# SECURITY: Change admin password on first login and restrict port 3003 via firewall/VPN/reverse proxy.
# Port configurable via UVICORN_PORT env var.
# IMPORTANT: Admin UI writes SQLite WAL/SHM files into the shared `./data` mount.
# Set umask so files created by root inside this container are group-writable for `ai_engine` (runs as appuser).
CMD sh -c 'umask 0002 && uvicorn main:app --host ${UVICORN_HOST:-0.0.0.0} --port ${UVICORN_PORT:-3003}'
