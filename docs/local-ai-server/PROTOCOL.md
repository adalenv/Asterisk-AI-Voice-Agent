# Local AI Server WebSocket Protocol

This document describes the WebSocket API exposed by the local AI server (default `ws://127.0.0.1:8765`). It supports selective operation modes for STT, LLM, TTS, and a full pipeline.

- Default address: `ws://127.0.0.1:8765`
- Engine/client URL: `LOCAL_WS_URL` (used by `providers.*.base_url/ws_url` in `config/ai-agent.yaml`)
- Server bind: `LOCAL_WS_HOST` + `LOCAL_WS_PORT` (server-side)
- Optional auth: `LOCAL_WS_AUTH_TOKEN` (server-side)
- Modes: `full`, `stt`, `llm`, `tts` (default `full`)
- Binary messages (client → server): raw PCM16 mono frames (assumed 16 kHz unless you set `rate` on JSON `audio`)
- Binary messages (server → client): μ-law 8 kHz audio bytes for TTS playback (used by `full` pipeline)
- JSON messages: control, status, text requests, or base64 audio frames

Source of truth:

- Server: `local_ai_server/main.py`
  - Message handling: `_handle_json_message()`, `_handle_binary_message()`
  - Streaming STT: `_process_stt_stream()`
  - LLM pipeline: `process_llm()`, `_emit_llm_response()`
  - TTS pipeline: `process_tts()`, `_emit_tts_audio()`

---

## Connection and Modes

### Authentication (optional)

If `LOCAL_WS_AUTH_TOKEN` is set on the server, clients must authenticate before any other messages (including binary audio).

Request:

```json
{ "type": "auth", "auth_token": "..." }
```

Response:

```json
{ "type": "auth_response", "status": "ok" }
```

If authentication is required and not completed, the server responds with:

```json
{ "type": "auth_response", "status": "error", "message": "authentication_required" }
```

If the token is wrong:

```json
{ "type": "auth_response", "status": "error", "message": "invalid_auth_token" }
```

### Mode selection

Optionally set a default mode for subsequent binary audio frames.

Request:

```json
{
  "type": "set_mode",
  "mode": "stt",           
  "call_id": "1234-5678"   
}
```

Response:

```json
{
  "type": "mode_ready",
  "mode": "stt",
  "call_id": "1234-5678"
}
```

Notes:

- Supported modes: `full`, `stt`, `llm`, `tts`.
- `call_id` is optional but useful for correlating events.
- If you never call `set_mode`, the default is `full`.

---

## Message Types (JSON)

- `auth` → Authenticate session (if enabled); responds with `auth_response`.
- `set_mode` → Changes session mode; responds with `mode_ready`.
- `audio` → Base64 audio frames for STT/LLM/FULL flows (recommended: PCM16 mono @ 16 kHz).
- `llm_request` → Ask LLM with text; responds with `llm_response`.
- `tts_request` → Synthesize TTS from text; responds with `tts_response` (base64 μ-law).
- `reload_models` → Reload all models; responds with `reload_response`.
- `reload_llm` → Reload only LLM; responds with `reload_response`.
- `switch_model` → Switch backend/model paths at runtime; responds with `switch_response`.
- `status` → Report loaded backends/models; responds with `status_response`.

### Common fields

- `call_id` (string, optional): Correlate the request with your call/session.
- `request_id` (string, optional): Correlate multiple responses to a single request.

---

## Audio Streaming (STT / FULL)

You can stream audio via:

- JSON frames: `{ "type": "audio", "data": "<base64 pcm16>", "rate": 16000, "mode": "full" }`
- Binary frames: send raw PCM16 bytes directly after `set_mode`.

Recommended input: PCM16 mono at 16 kHz. If you send another rate, the server resamples to 16 kHz internally using sox.

### JSON audio example (full pipeline)

Request:

```json
{
  "type": "audio",
  "mode": "full",
  "rate": 16000,
  "call_id": "1234-5678",
  "request_id": "r1",
  "data": "<base64 pcm16 chunk>"
}
```

Expected responses (sequence):

- `stt_result` (zero or more partials)
- `stt_result` (one final)
- `llm_response`
- (optional) `tts_audio` metadata (only if `request_id` is provided)
- one binary WebSocket message containing μ-law 8 kHz audio bytes

Example events:

```json
{ "type": "stt_result", "text": "hello", "call_id": "1234-5678", "mode": "full", "is_final": false, "is_partial": true, "request_id": "r1" }
{ "type": "stt_result", "text": "hello there", "call_id": "1234-5678", "mode": "full", "is_final": true, "is_partial": false, "request_id": "r1", "confidence": 0.91 }
{ "type": "llm_response", "text": "Hi there, how can I help you?", "call_id": "1234-5678", "mode": "llm", "request_id": "r1" }
{ "type": "tts_audio", "call_id": "1234-5678", "mode": "full", "request_id": "r1", "encoding": "mulaw", "sample_rate_hz": 8000, "byte_length": 16347 }
```

If `request_id` is set, the server emits `tts_audio` metadata before the binary audio. If `request_id` is omitted, you will only receive the binary audio bytes.

### Binary audio example (stt-only)

1) Set mode:

```json
{ "type": "set_mode", "mode": "stt", "call_id": "abc" }
```

2) Send binary PCM16 frames (no JSON wrapper). The server will emit:

```json
{ "type": "stt_result", "text": "...", "call_id": "abc", "mode": "stt", "is_final": false, "is_partial": true }
{ "type": "stt_result", "text": "...", "call_id": "abc", "mode": "stt", "is_final": true,  "is_partial": false }
```

Notes:

- The server uses an idle finalizer (`LOCAL_STT_IDLE_MS`, default 3000 ms) to promote a final transcript if no more audio arrives; duplicate/empty finals are suppressed per `local_ai_server/main.py`.

---

## LLM-only

Request:

```json
{
  "type": "llm_request",
  "text": "What are your business hours?",
  "call_id": "1234-5678",
  "request_id": "q1"
}
```

Response:

```json
{
  "type": "llm_response",
  "text": "We're open from 9am to 5pm, Monday through Friday.",
  "call_id": "1234-5678",
  "mode": "llm",
  "request_id": "q1"
}
```

---

## TTS-only

Request:

```json
{
  "type": "tts_request",
  "text": "Hello, how can I help you?",
  "call_id": "1234-5678",
  "request_id": "t1"
}
```

Response:

```json
{
  "type": "tts_response",
  "text": "Hello, how can I help you?",
  "call_id": "1234-5678",
  "request_id": "t1",
  "audio_data": "<base64 mulaw bytes>",
  "encoding": "mulaw",
  "sample_rate_hz": 8000,
  "byte_length": 12446
}
```

---

## Hot Reload

- Reload all models:

```json
{ "type": "reload_models" }
```

Response:

```json
{ "type": "reload_response", "status": "success", "message": "All models reloaded successfully" }
```

- Reload LLM only:

```json
{ "type": "reload_llm" }
```

Response:

```json
{ "type": "reload_response", "status": "success", "message": "LLM model reloaded with optimizations (ctx=..., batch=..., temp=..., max_tokens=...)" }
```

---

## Status

Request:

```json
{ "type": "status" }
```

Response:

```json
{
  "type": "status_response",
  "status": "ok",
  "stt_backend": "vosk|kroko|sherpa",
  "tts_backend": "piper|kokoro",
  "models": {
    "stt": { "loaded": true, "path": "/app/models/stt/...", "display": "vosk-model-en-us-0.22" },
    "llm": { "loaded": true, "path": "/app/models/llm/...", "display": "phi-3-mini-4k-instruct.Q4_K_M.gguf" },
    "tts": { "loaded": true, "path": "/app/models/tts/...", "display": "en_US-lessac-medium.onnx" }
  },
  "kroko": { "embedded": false, "port": 6006, "language": "en-US", "url": "wss://...", "model_path": "/app/models/kroko/..." },
  "kokoro": { "mode": "local|api|hf", "voice": "af_heart", "model_path": "/app/models/tts/kokoro", "api_base_url": "https://.../api/v1" },
  "config": { "log_level": "INFO", "debug_audio": false }
}
```

---

## Model Switching

`switch_model` updates server-side model/backend selections and reloads models without restarting the container.

Request (examples):

```json
{ "type": "switch_model", "stt_backend": "kroko" }
```

```json
{ "type": "switch_model", "stt_backend": "sherpa", "sherpa_model_path": "/app/models/stt/sherpa-onnx-streaming-zipformer-en-2023-06-26" }
```

```json
{ "type": "switch_model", "stt_backend": "kroko", "kroko_embedded": true, "kroko_port": 6006, "kroko_model_path": "/app/models/kroko/kroko-en-v1.0.onnx" }
```

```json
{ "type": "switch_model", "tts_backend": "kokoro", "kokoro_voice": "af_heart" }
```

```json
{ "type": "switch_model", "tts_backend": "kokoro", "kokoro_mode": "api", "kokoro_api_base_url": "https://voice-generator.pages.dev/api/v1" }
```

```json
{ "type": "switch_model", "llm_model_path": "/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf" }
```

Response:

```json
{ "type": "switch_response", "status": "success", "message": "...", "changed": ["stt_backend=kroko"] }
```

---

## Capabilities

Query installed backends without loading models. Useful for Admin UI to show available options.

Request:

```json
{ "type": "capabilities" }
```

Response:

```json
{
  "type": "capabilities_response",
  "capabilities": {
    "vosk": true,
    "sherpa": true,
    "kroko_embedded": true,
    "piper": true,
    "kokoro": true,
    "llama": true
  }
}
```

Notes:

- `kroko_embedded`: `true` only if `/usr/local/bin/kroko-server` exists (requires `INCLUDE_KROKO_EMBEDDED=true` at build time)
- `kokoro`: `true` if Kokoro package is installed, or `KOKORO_API_BASE_URL` is set, or model files exist on disk
- `vosk`, `piper`, `llama`: Reported as `true` in default/full Docker images (assumes standard dependencies are installed)
- Used by Admin UI `/api/local-ai/capabilities` endpoint to filter available options

---

## Client Examples

Additional example code (including an espeak-ng based lightweight TTS demo) lives under `docs/local-ai-server/examples/`.

### Python: TTS request and save μ-law file

```python
import asyncio, websockets, json

async def tts():
    async with websockets.connect("ws://127.0.0.1:8765", max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "tts_request",
            "text": "Hello, how can I help you?",
            "call_id": "demo",
            "request_id": "t1",
        }))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "tts_response"
        import base64
        audio_bytes = base64.b64decode(resp["audio_data"])
        with open("out.ulaw", "wb") as f:
            f.write(audio_bytes)

asyncio.run(tts())
```

### Python: STT-only with binary frames

```python
import asyncio, websockets, json

async def stt(pcm_bytes):
    async with websockets.connect("ws://127.0.0.1:8765", max_size=None) as ws:
        await ws.send(json.dumps({"type": "set_mode", "mode": "stt", "call_id": "demo"}))
        await ws.recv()  # mode_ready
        await ws.send(pcm_bytes)  # raw PCM16 mono @ 16kHz
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            evt = json.loads(msg)
            if evt.get("type") == "stt_result" and evt.get("is_final"):
                print("Final:", evt["text"])
                break

# pcm_bytes = ... load/generate 16kHz PCM16 mono
# asyncio.run(stt(pcm_bytes))
```

---

## Environment Variables and Tuning

Server-side (see `local_ai_server/main.py`):

- Models: `LOCAL_STT_MODEL_PATH`, `LOCAL_LLM_MODEL_PATH`, `LOCAL_TTS_MODEL_PATH`
- WebSocket bind: `LOCAL_WS_HOST`, `LOCAL_WS_PORT`
- Optional auth: `LOCAL_WS_AUTH_TOKEN`
- LLM performance: `LOCAL_LLM_THREADS`, `LOCAL_LLM_CONTEXT`, `LOCAL_LLM_BATCH`, `LOCAL_LLM_MAX_TOKENS`, `LOCAL_LLM_TEMPERATURE`, `LOCAL_LLM_TOP_P`, `LOCAL_LLM_REPEAT_PENALTY`, `LOCAL_LLM_SYSTEM_PROMPT`, `LOCAL_LLM_STOP_TOKENS`
- STT idle promote: `LOCAL_STT_IDLE_MS` (default 3000 ms)
- LLM timeout: `LOCAL_LLM_INFER_TIMEOUT_SEC` (default 20.0)
- Logging: `LOCAL_LOG_LEVEL` (default INFO)

Engine-side (see `config/ai-agent.*.yaml` and `.env.example`):

- `providers.local.base_url` / `providers.local*.ws_url` (default `${LOCAL_WS_URL:-ws://127.0.0.1:8765}`)
- `providers.local*.auth_token` (default `${LOCAL_WS_AUTH_TOKEN:-}`)
- Timeouts: `${LOCAL_WS_CONNECT_TIMEOUT}`, `${LOCAL_WS_RESPONSE_TIMEOUT}`
- Chunk size (ms): `${LOCAL_WS_CHUNK_MS}`

Dependencies:

- sox (used for resampling and μ-law conversion). The container image includes it; if running outside Docker ensure `sox` is installed.

---

## Expected Event Order (Full Pipeline)

For a single request_id and continuous audio segment in `full` mode:

1. `stt_result` (0..N partial)
2. `stt_result` (1 final)
3. `llm_response`
4. (optional) `tts_audio` metadata (only if `request_id` was provided on the input)
5. Binary μ-law audio bytes (8 kHz)

Duplicate/empty finals are suppressed; see `_handle_final_transcript()` for details.

---

## Error Responses

When the server encounters an error processing a request, it responds with an error message:

```json
{
  "type": "error",
  "error": "Error description",
  "call_id": "1234-5678",
  "request_id": "r1",
  "details": {
    "error_type": "timeout" | "invalid_request" | "processing_error",
    "component": "stt" | "llm" | "tts",
    "message": "Detailed error message"
  }
}
```

Common error types:

- **timeout**: Component took too long (e.g., LLM inference timeout)
- **invalid_request**: Malformed JSON or missing required fields
- **processing_error**: Internal error during STT/LLM/TTS processing

Example:

```json
{
  "type": "error",
  "error": "LLM inference timeout after 20.0 seconds",
  "call_id": "abc-123",
  "request_id": "llm-1",
  "details": {
    "error_type": "timeout",
    "component": "llm",
    "message": "Increase LOCAL_LLM_INFER_TIMEOUT_SEC or reduce max_tokens"
  }
}
```

---

## Common Issues and Resolutions

- STT returns empty often
  - Cause: utterances too short. Increase chunk size or allow idle finalizer (`LOCAL_STT_IDLE_MS`), ensure PCM16 @ 16kHz input.
- No TTS audio received
  - For `tts_request`, the response is JSON `tts_response` containing `audio_data` (base64 μ-law @ 8 kHz).
  - For `full` mode, the server sends a binary WebSocket frame containing μ-law bytes (and may also send `tts_audio` metadata if `request_id` was provided).
- LLM timeout (slow responses)
  - Increase `LOCAL_LLM_INFER_TIMEOUT_SEC`; reduce `LOCAL_LLM_MAX_TOKENS`; use faster model or fewer threads context.
- Model load failures
  - Check paths: `LOCAL_*_MODEL_PATH`; run `make model-setup`; verify models exist inside the container.
- Resample or μ-law conversion errors
  - Ensure `sox` is installed in the environment. Logs will show conversion failures.
- Mode mismatch warnings
  - Sending audio with `mode=tts` is ignored. Use `tts_request` (text in) for TTS.
- High memory usage
  - Lower `LOCAL_LLM_CONTEXT`, `LOCAL_LLM_BATCH`; tune threads; consider a smaller model.

---

## Performance Characteristics

### Models (Default Installation)

- **STT**: Vosk `vosk-model-en-us-0.22` (16kHz native)
  - Size: ~40MB
  - Latency: 100-300ms (streaming with partials)
  - Accuracy: Good for conversational speech

- **LLM**: Phi-3-mini-4k `phi-3-mini-4k-instruct.Q4_K_M.gguf`
  - Size: 2.3GB
  - Warmup: ~110 seconds (first load)
  - Inference: 2-5 seconds per response
  - Context: 4096 tokens
  - Quality: Good for conversational AI (better than TinyLlama, less than GPT-4)

- **TTS**: Piper `en_US-lessac-medium.onnx` (22kHz native)
  - Size: ~60MB
  - Latency: 500-1000ms
  - Output: μ-law @ 8kHz
  - Quality: Natural, clear voice

### Typical Latencies (End-to-End)

- **STT only**: 100-300ms
- **LLM inference**: 2-5 seconds (depends on response length)
- **TTS synthesis**: 500-1000ms
- **Full pipeline turn**: 3-7 seconds total

### Concurrency

- **Single server**: ~10-20 concurrent calls (CPU-bound)
- **Bottleneck**: LLM inference (most CPU intensive)
- **Scaling**: Deploy multiple containers with load balancer

---

## Versioning and Compatibility

- Protocol is stable for v4.0 GA track. Message types and fields correspond to the implementation in `local_ai_server/main.py`.
- The engine's local provider uses the same contract to support pipelines defined in `config/ai-agent.*.yaml`.
