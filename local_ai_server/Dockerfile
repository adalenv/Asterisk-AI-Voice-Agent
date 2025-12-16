# --- Stage 1: Builder (Dependencies) ---
FROM python:3.11 as builder

# ─────────────────────────────────────────────────────────────────────────────
# Build Args - Conditionally include backends (AAVA-112)
# Default: all backends enabled for backward compatibility
# Usage: docker build --build-arg INCLUDE_VOSK=false ...
# ─────────────────────────────────────────────────────────────────────────────
ARG INCLUDE_VOSK=true
ARG INCLUDE_SHERPA=true
ARG INCLUDE_PIPER=true
ARG INCLUDE_KOKORO=true
ARG INCLUDE_LLAMA=true

WORKDIR /usr/src/app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for optimal caching
COPY requirements.txt .
COPY requirements-base.txt .

# Install base dependencies (always required)
RUN pip install --no-cache-dir -r requirements-base.txt

# Conditionally install backends based on build args
RUN if [ "$INCLUDE_VOSK" = "true" ]; then \
        echo "📦 Installing Vosk STT..." && \
        pip install --no-cache-dir vosk==0.3.45; \
    fi

RUN if [ "$INCLUDE_SHERPA" = "true" ]; then \
        echo "📦 Installing Sherpa-ONNX STT..." && \
        pip install --no-cache-dir sherpa-onnx==1.12.19; \
    fi

RUN if [ "$INCLUDE_PIPER" = "true" ]; then \
        echo "📦 Installing Piper TTS..." && \
        pip install --no-cache-dir piper-tts==1.2.0; \
    fi

RUN if [ "$INCLUDE_KOKORO" = "true" ]; then \
        echo "📦 Installing Kokoro TTS..." && \
        pip install --no-cache-dir "kokoro>=0.9.2"; \
    fi

RUN if [ "$INCLUDE_LLAMA" = "true" ]; then \
        echo "📦 Installing llama-cpp-python LLM..." && \
        pip install --no-cache-dir llama-cpp-python==0.3.16; \
    fi

# Pre-install spacy model for Kokoro TTS (only if Kokoro included)
RUN if [ "$INCLUDE_KOKORO" = "true" ]; then \
        python -m spacy download en_core_web_sm || true; \
    fi

# --- Stage 2: Final Runtime Image ---
FROM python:3.11-slim

# Build argument to optionally include Kroko embedded server
ARG INCLUDE_KROKO_EMBEDDED=false

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libsndfile1 \
    libasound2 \
    libportaudio2 \
    libgomp1 \
    libatomic1 \
    sox \
    wget \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Optional: Install Kroko ONNX server for embedded mode
# Only included if INCLUDE_KROKO_EMBEDDED=true during build
# ─────────────────────────────────────────────────────────────────────────────
RUN if [ "$INCLUDE_KROKO_EMBEDDED" = "true" ]; then \
        echo "Installing ONNX Runtime for Kroko..." && \
        wget -q https://github.com/microsoft/onnxruntime/releases/download/v1.17.1/onnxruntime-linux-x64-1.17.1.tgz && \
        tar -xzf onnxruntime-linux-x64-1.17.1.tgz && \
        mv onnxruntime-linux-x64-1.17.1/lib/* /usr/lib/ && \
        rm -rf onnxruntime-linux-x64-1.17.1* && \
        echo "Downloading Kroko ONNX server binary..." && \
        wget -q https://download.kroko.ai/binaries/kroko-onnx-online-websocket-server \
            -O /usr/local/bin/kroko-server && \
        chmod +x /usr/local/bin/kroko-server && \
        echo "Kroko embedded server installed successfully"; \
    else \
        echo "Skipping Kroko embedded server (use KROKO_URL for external/hosted)"; \
    fi

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home appuser
USER appuser

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application source code
COPY --chown=appuser:appuser . .

# Set PATH for virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Run the application
CMD ["python", "main.py"]
