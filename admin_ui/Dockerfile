# Stage 1: Build Frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + Runtime
FROM python:3.10-slim
WORKDIR /app

# Install system dependencies (unzip for model extraction, docker CLI for container management)
RUN apt-get update && apt-get install -y curl gcc unzip gnupg lsb-release \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built frontend assets to backend's static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose default port (can be overridden by UVICORN_PORT)
EXPOSE 3003

# Run the application (port configurable via UVICORN_PORT env var)
CMD uvicorn main:app --host 0.0.0.0 --port ${UVICORN_PORT:-3003}
