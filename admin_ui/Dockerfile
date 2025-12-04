# Stage 1: Build Frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --omit=dev || npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + Runtime
FROM python:3.10-slim
WORKDIR /app

# Optimization env vars
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Note: docker-ce-cli removed - using docker Python SDK instead
# This saves ~100MB+ in image size

# Install Python dependencies (bcrypt has manylinux wheels, no gcc needed)
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
