# Milestone 6 — OpenAI Realtime Voice Agent Integration

## Objective

Add first-class support for OpenAI’s Realtime voice agents so users can swap between Deepgram and OpenAI using configuration only. Reuse the AudioSocket architecture and streaming transport from Milestone 5.

## Success Criteria

- `config/ai-agent.yaml` can set `default_provider: openai_realtime` and a regression call completes with clear two-way conversation.
- Provider exposes codec/sample-rate metadata so the streaming manager automatically resamples to the configured `audiosocket.format`.
- Streaming pipeline successfully converts OpenAI PCM16 output (default 24 kHz) into the configured AudioSocket format without underruns.
- Regression documentation includes an OpenAI voice call walkthrough with logs, metrics, and tuning guidance.

## Dependencies

- Milestone 5 complete (streaming transport production-ready).
- OpenAI API credentials available in `.env` / environment variables.

## Work Breakdown

### 6.1 Provider Implementation

- Create `src/providers/openai_realtime.py` implementing `AIProviderInterface`; the landed module now manages the OpenAI Realtime session lifecycle, PCM16 base64 streaming, output resampling, periodic keepalives, and Deepgram-aligned event emission via `src/audio/resampler.py` helpers.
- Establish the server-side Realtime WebSocket session (no WebRTC dependency) suitable for telephony workloads. Document this choice in the provider docstring.
- Map inbound audio frames to OpenAI’s streaming input API (PCM16, 16 kHz) and base64-wrap chunks per protocol; handle partial transcripts and final responses.
- Decode provider output events (base64 PCM16, default 24 kHz), resample to the configured AudioSocket format, and prepare streaming frames for playback.
- Emit `ProviderEvent` objects consistent with Deepgram (`AgentAudio`, `AgentAudioDone`, transcripts).

### 6.2 Configuration & Secrets

- Extend provider section in `config/ai-agent.yaml` with an `openai_realtime` block (API key env reference, voice preset, model name, sample rate, codec expectations); the block now ships in `config/ai-agent.yaml` while Deepgram and local defaults remain untouched.
- Update `src/config.py` with Pydantic models and validation, including the new `OpenAIRealtimeProviderConfig` structure.
- Document required env vars in `README.md` / `docs/Configuration-Reference.md` (e.g., `OPENAI_API_KEY`) and confirm architecture notes in `docs/contributing/architecture-deep-dive.md`.

### 6.3 Codec & Transport Alignment

- Ensure provider returns explicit metadata (encoding = PCM16 LE, input sample rate = 16 kHz, output default = 24 kHz unless overridden) to `StreamingPlaybackManager` and VAD.
- Add automated downsampling/up-sampling plus µ-law/slin16 conversion so AudioSocket receives frames at the configured format (typically 8 kHz); reusable helpers in `src/audio/resampler.py` now handle PCM16↔µ-law/slin16 conversion and sample-rate changes for this path.
- Add regression assertions verifying that resampled AudioSocket frames match the expected size for the configured format, covered by `tests/test_audio_resampler.py`.

### 6.4 Regression & Documentation

- Ensure regression evidence is recorded in `docs/resilience.md` (and keep the long-form walkthrough in `archived/regressions/openai-call-framework.md` if needed).
- Update `docs/ROADMAP.md` and `docs/contributing/architecture-deep-dive.md` to reflect OpenAI support.

### 6.5 OpenAI Realtime GA Schema Alignment (Production-Validated Feb 2026)

The GA API schema differs significantly from Beta. Key rules discovered during production validation:

#### GA `session.update` — Verified Working Schema

```json
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "output_modalities": ["audio"],
    "audio": {
      "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": "whisper-1"},
        "turn_detection": {
          "type": "server_vad",
          "threshold": 0.5,
          "prefix_padding_ms": 300,
          "silence_duration_ms": 1000,
          "create_response": true,
          "interrupt_response": true
        }
      },
      "output": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "voice": "alloy"
      }
    },
    "instructions": "...",
    "tools": [...]
  }
}
```

#### GA `response.create` — Minimal Payload

```json
{
  "type": "response.create",
  "response": {
    "instructions": "Please greet the user with the following: Hello!"
  }
}
```

GA `response.create` should NOT include `output_modalities` or `input` — modalities are set via `session.update`.

#### GA vs Beta Schema Differences

| Field | Beta | GA |
|-------|------|----|
| Header | `OpenAI-Beta: realtime=v1` | None (remove header) |
| Session type | Not required | `"type": "realtime"` required |
| Modalities | `session.modalities` | `session.output_modalities` |
| Audio format | `input_audio_format: "pcm16"` | `audio.input.format: {"type": "audio/pcm", "rate": 24000}` |
| Output format | `output_audio_format: "pcm16"` | `audio.output.format: {"type": "audio/pcm", "rate": 24000}` |
| Format strings | `pcm16`, `g711_ulaw`, `g711_alaw` | `audio/pcm`, `audio/pcmu`, `audio/pcma` |
| Voice | `session.voice` | `audio.output.voice` |
| Turn detection | `session.turn_detection` | `audio.input.turn_detection` |
| Transcription | `session.input_audio_transcription` | `audio.input.transcription` |
| Response modalities | `response.modalities` | Not needed in `response.create` |
| Audio delta event | `response.audio.delta` (string) | `response.output_audio.delta` (string in `delta` field) |

#### Critical GA Rules (Discovered During Production)

1. **`audio.input.format`** requires both `type` and `rate`
2. **`audio.output.format`** requires `rate` when format is explicitly set in a partial update, but may be optional in the initial full session.update
3. **Always use `audio/pcm` @ 24kHz for output** — `audio/pcmu` may be silently ignored by some models
4. **`turn_detection` goes under `audio.input`** — rejected at session level in GA
5. **`input_audio_transcription` is rejected** — use `audio.input.transcription` instead
6. **`response.output_audio.delta`** sends audio as a base64 **string** in `event["delta"]`, not a dict — code must check `isinstance(delta, str)` before calling `.get()`
7. **Rate is tied to format**: `audio/pcm` = 24000 Hz, `audio/pcmu` = 8000 Hz, `audio/pcma` = 8000 Hz
8. **Model name**: `gpt-realtime` is GA default but may not be available to all accounts — `gpt-4o-realtime-preview-2024-12-17` works with GA API as fallback

- When VAD is enabled (default), stream with `input_audio_buffer.append` only. Do not send `input_audio_buffer.commit`.
- Handle server events: `response.output_audio.delta`/`done`, `response.done`, transcript variants `response.output_audio_transcript.*`.

### 6.6 Verification Signals (Expected in Logs)

- `OpenAI send type=session.update` (nested audio schema)
- `OpenAI send type=response.create` (no `response.audio`)
- `response.created` → `response.output_item.added` → `response.output_audio.delta` → first audio chunk logged
- Playback lifecycle: `AgentAudio` → `AgentAudioDone` → `PlaybackFinished`
- No `unknown_parameter` errors; no `input_audio_buffer_commit_empty` when VAD is on

## Deliverables

- New provider module, config schemas, and tests now wired into `Engine._load_providers`, complete with readiness checks and metrics label `openai_realtime`, validated alongside `python3 -m pytest tests/test_audio_resampler.py`.
- Updated documentation (roadmap, architecture, regression guide, README env vars).
- Regression log capturing a successful OpenAI call (call ID, duration, audio quality notes).

## Verification Checklist

- Switching `default_provider` between `deepgram` and `openai_realtime` works without restarting containers beyond the standard reload, exercising the new readiness gates.
- Logs show `OpenAI Realtime session started` and streaming metrics identical to Deepgram baseline.
- `/metrics` includes provider label `openai_realtime` for turn/latency gauges emitted by the integrated provider.

## Handover Notes

- Coordinate with Milestone 7 (pipeline configurability). Ensure provider metadata is compatible with the new pipeline abstraction.
- Flag any API limitations (e.g., token quotas) in the regression doc for future optimization.
