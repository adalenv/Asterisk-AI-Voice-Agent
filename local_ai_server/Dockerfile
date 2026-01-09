# --- Stage 1: Builder (Dependencies) ---
# Note: Using trixie (Debian 13) for glibc compatibility with Kroko binary
FROM python:3.11-slim-trixie as builder

# ─────────────────────────────────────────────────────────────────────────────
# Build Args - Conditionally include backends (AAVA-112)
# Default: all backends enabled for backward compatibility
# Usage: docker build --build-arg INCLUDE_VOSK=false ...
# ─────────────────────────────────────────────────────────────────────────────
ARG INCLUDE_VOSK=true
ARG INCLUDE_SHERPA=true
ARG INCLUDE_FASTER_WHISPER=false
ARG INCLUDE_WHISPER_CPP=false
ARG INCLUDE_PIPER=true
ARG INCLUDE_KOKORO=true
ARG INCLUDE_MELOTTS=false
ARG INCLUDE_LLAMA=true

WORKDIR /usr/src/app

# Some optional backends require native builds (e.g., llama-cpp-python) or git installs (MeloTTS).
# Install build tooling only when those backends are enabled to keep CI "ci-min" builds lean.
RUN apt-get -o Acquire::Retries=5 update && \
    if [ "$INCLUDE_LLAMA" = "true" ] || [ "$INCLUDE_MELOTTS" = "true" ] || [ "$INCLUDE_WHISPER_CPP" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            build-essential \
            cmake \
            git \
            ; \
    fi && \
    rm -rf /var/lib/apt/lists/*

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

RUN if [ "$INCLUDE_FASTER_WHISPER" = "true" ]; then \
        echo "📦 Installing Faster-Whisper STT..." && \
        pip install --no-cache-dir faster-whisper==1.0.3; \
    fi

RUN if [ "$INCLUDE_WHISPER_CPP" = "true" ]; then \
        echo "📦 Installing Whisper.cpp (pywhispercpp)..." && \
        pip install --no-cache-dir pywhispercpp==1.2.0; \
    fi

RUN if [ "$INCLUDE_PIPER" = "true" ]; then \
        echo "📦 Installing Piper TTS..." && \
        pip install --no-cache-dir piper-tts==1.2.0; \
    fi

RUN if [ "$INCLUDE_KOKORO" = "true" ]; then \
        echo "📦 Installing Kokoro TTS..." && \
        pip install --no-cache-dir "kokoro>=0.9.2"; \
    fi

RUN if [ "$INCLUDE_MELOTTS" = "true" ]; then \
        echo "📦 Installing MeloTTS..." && \
        pip install --no-cache-dir git+https://github.com/myshell-ai/MeloTTS.git && \
        python -m unidic download && \
        python -c "import nltk; nltk.download('averaged_perceptron_tagger_eng')" && \
        echo "📦 Pre-downloading MeloTTS BERT model..." && \
        python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('bert-base-uncased')" && \
        python -c "from melo.api import TTS; tts = TTS(language='EN', device='cpu'); print('MeloTTS ready')"; \
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
# Using trixie (Debian 13) for Kroko binary compatibility (requires glibc 2.38+)
FROM python:3.11-slim-trixie

# Build argument to optionally include Kroko embedded server
ARG INCLUDE_KROKO_EMBEDDED=false

# Install runtime dependencies
RUN apt-get -o Acquire::Retries=5 update && apt-get install -y \
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
# SECURITY: SHA256 checksums verify binary integrity (AAVA-134)
# Build will FAIL if checksums don't match (fail-closed)
# ─────────────────────────────────────────────────────────────────────────────
# Known checksums - update these when upgrading versions
# ONNX Runtime 1.17.1 Linux x64: https://github.com/microsoft/onnxruntime/releases/tag/v1.17.1
ARG ONNX_RUNTIME_SHA256="89b153af88746665909c758a06797175ae366280cbf25502c41eb5955f9a555e"
# Kroko server: Obtain from official Kroko release notes or contact vendor
# IMPORTANT: Set via --build-arg KROKO_SERVER_SHA256=<hash> or update this default
ARG KROKO_SERVER_SHA256=""

RUN if [ "$INCLUDE_KROKO_EMBEDDED" = "true" ]; then \
        echo "📦 Installing ONNX Runtime for Kroko..." && \
        wget -q https://github.com/microsoft/onnxruntime/releases/download/v1.17.1/onnxruntime-linux-x64-1.17.1.tgz \
            -O /tmp/onnxruntime.tgz && \
        echo "🔐 Verifying ONNX Runtime checksum..." && \
        ACTUAL_ONNX_SHA=$(sha256sum /tmp/onnxruntime.tgz | cut -d' ' -f1) && \
        echo "   Expected: ${ONNX_RUNTIME_SHA256}" && \
        echo "   Actual:   ${ACTUAL_ONNX_SHA}" && \
        if [ -n "$ONNX_RUNTIME_SHA256" ] && [ "$ACTUAL_ONNX_SHA" != "$ONNX_RUNTIME_SHA256" ]; then \
            echo "🚨 SECURITY: ONNX Runtime checksum mismatch! Build aborted." && \
            exit 1; \
        fi && \
        tar -xzf /tmp/onnxruntime.tgz -C /tmp && \
        mv /tmp/onnxruntime-linux-x64-1.17.1/lib/* /usr/lib/ && \
        rm -rf /tmp/onnxruntime* && \
        echo "📦 Downloading Kroko ONNX server binary..." && \
        wget -q https://download.kroko.ai/binaries/kroko-onnx-online-websocket-server \
            -O /usr/local/bin/kroko-server && \
        echo "🔐 Verifying Kroko server checksum..." && \
        ACTUAL_KROKO_SHA=$(sha256sum /usr/local/bin/kroko-server | cut -d' ' -f1) && \
        echo "   Expected: ${KROKO_SERVER_SHA256:-'(not set - verification skipped)'}" && \
        echo "   Actual:   ${ACTUAL_KROKO_SHA}" && \
        if [ -n "$KROKO_SERVER_SHA256" ] && [ "$ACTUAL_KROKO_SHA" != "$KROKO_SERVER_SHA256" ]; then \
            echo "🚨 SECURITY: Kroko server checksum mismatch! Build aborted." && \
            exit 1; \
        fi && \
        if [ -z "$KROKO_SERVER_SHA256" ]; then \
            echo "⚠️  WARNING: KROKO_SERVER_SHA256 not set. For production, set --build-arg KROKO_SERVER_SHA256=${ACTUAL_KROKO_SHA}"; \
        fi && \
        chmod +x /usr/local/bin/kroko-server && \
        echo "✅ Kroko embedded server installed successfully"; \
    else \
        echo "ℹ️  Skipping Kroko embedded server (use KROKO_URL for external/hosted)"; \
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
