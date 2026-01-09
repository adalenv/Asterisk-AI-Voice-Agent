# Local Profiles (No Models Bundled)

Local mode is a first-class path in this project, but **models are not shipped inside Docker images**. You must download and mount models into `./models` (default) or provide equivalent paths via environment variables.

## Goals

- Predictable “local stack” that boots reliably
- Clear expectations for CPU/RAM/GPU
- Explicit build profiles so contributors don’t accidentally ship multi-GB images

## Recommended Profiles

### Profile: `local-core` (recommended default)

Use when you want “fully local” call handling with a predictable, smaller stack:

- STT: Vosk
- LLM: llama.cpp (GGUF) (e.g., Phi-3-mini)
- TTS: Piper
- No Sherpa, no Kokoro, no embedded Kroko

Run:

- `docker compose -f docker-compose.yml -f docker-compose.local-core.yml build local_ai_server`
- `docker compose up -d`

### Profile: `local-full` (power users)

Enable additional backends (Sherpa, Kokoro, embedded Kroko). This is heavier, increases build times, and may exceed CI runner disk limits.

Run:

- `docker compose build local_ai_server` (default settings)

## Embedded Kroko (Default: Off)

`INCLUDE_KROKO_EMBEDDED` is **off by default** (lighter image). Enable it only if you specifically want the embedded Kroko ONNX websocket server inside `local_ai_server`.

- Enable for a build: `INCLUDE_KROKO_EMBEDDED=true docker compose build local_ai_server`

## Hardware Expectations (Rule of Thumb)

- **CPU-only “core”:** expect multi-second LLM turns on small CPUs; prioritize fewer concurrent local calls.
- **GPU (if used):** large variance by GPU + model; tune `LOCAL_LLM_GPU_LAYERS` and thread counts.
- **RAM:** ensure the model(s) you choose fit comfortably; leave headroom for Docker + Asterisk.

## Typical Disk/RAM Footprints (Local-Core)

These are **approximate** and vary by model variant/quantization and OS memory behavior. Verify with `du -sh` on your actual model files.

| Component | Example default | Disk (approx) | RAM (approx) | Notes |
|---|---|---:|---:|---|
| STT | Vosk `vosk-model-en-us-0.22` | ~1.5–2.0 GB | ~0.5–1.0 GB | Larger models improve accuracy; CPU-only |
| LLM | Phi-3-mini GGUF `Q4_K_M` | ~2.0–2.6 GB | ~3–5 GB | CPU-only can be slow; GPU layers reduce CPU load |
| TTS | Piper `en_US-lessac-medium.onnx` | ~50–150 MB | ~0.2–0.6 GB | Voice/model dependent |
| **Total (core)** | Vosk + Phi-3-mini + Piper | **~3.6–4.8 GB** | **~4–7 GB** | Add headroom for Docker + Asterisk |

Practical guidance:

- **CPU-only host:** 8 GB RAM minimum; 16 GB recommended for smoother turns and fewer OOM surprises.
- **GPU host:** VRAM needs depend on how many layers you offload; start with `LOCAL_LLM_GPU_LAYERS=0` then increase gradually.

## Model Paths (defaults)

Mounted by `docker-compose.yml`:

- Host: `./models`
- Container: `/app/models`

Defaults used by `local_ai_server`:

- STT: `LOCAL_STT_MODEL_PATH=/app/models/stt/...`
- LLM: `LOCAL_LLM_MODEL_PATH=/app/models/llm/...`
- TTS: `LOCAL_TTS_MODEL_PATH=/app/models/tts/...`

Adjust these in `.env` to match your downloaded model locations.
