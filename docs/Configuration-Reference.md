# Configuration Reference

This document explains every major option in `config/ai-agent.yaml`, the precedence model for greeting/persona, and the impact of fine‑tuning parameters across AudioSocket/ExternalMedia, VAD, Barge‑In, Streaming, and Providers.

## Canonical persona and greeting

- llm.initial_greeting: Text the agent speaks first (if provider supports explicit greeting or the engine plays via TTS).
- llm.prompt: The agent persona/instructions used by LLMs.
- Precedence at runtime:
  1) Provider/pipeline overrides (if explicitly set, e.g., `providers.openai_realtime.instructions`, `providers.deepgram.greeting`)
  2) `llm.prompt` and `llm.initial_greeting` in YAML
  3) Env defaults `AI_ROLE`, `GREETING`

## Transports

- audio_transport: `audiosocket` | `externalmedia`
  - audiosocket: Full‑duplex PCM over TCP; recommended for GA. Downstream can be streamed with pacing.
  - externalmedia: RTP capture path; best for explicit RTP analysis or legacy setups.
- downstream_mode: `stream` | `file`
  - stream: Sends 20 ms frames immediately; best UX, slightly more sensitive to jitter.
  - file: Plays μ‑law files via the bridge; more tolerant but higher latency.

## AudioSocket

- audiosocket.host: Bind address for AudioSocket listener.
- audiosocket.port: TCP port.
- audiosocket.format: `ulaw` | `slin16` (8 kHz). Use `ulaw` to match telephony trunks directly.

## ExternalMedia

- external_media.rtp_host: Bind address for RTP server.
- external_media.rtp_port: Port for inbound RTP.
- external_media.port_range: Optional range (`start:end`) for dynamic per-call RTP allocation; defaults to `rtp_port`.
- external_media.codec: `ulaw` | `slin16` (8 kHz).
- external_media.direction: `both` | `sendonly` | `recvonly`.
- external_media.jitter_buffer_ms: Target frame size for RTP playout pacing.

## Barge‑In

Controls interruption of TTS playback when the caller speaks.

- barge_in.enabled: true/false
- barge_in.initial_protection_ms: 200–600 ms. Drop inbound immediately after TTS starts to avoid self‑echo.
- barge_in.min_ms: 250–600 ms. Minimum sustained speech before a barge‑in is acknowledged (de‑bounce).
- barge_in.energy_threshold: 1000–3000. RMS energy threshold; raise on noisy lines.
- barge_in.cooldown_ms: 500–1500 ms. Ignore new barge‑ins after one triggers.
- barge_in.post_tts_end_protection_ms: 250–500 ms. Short guard to avoid clipping the start of the next caller utterance.

Tuning guidance:

- Noisy lines: raise `energy_threshold` and `min_ms`.
- Fast, chatty interactions: lower `min_ms` and `post_tts_end_protection_ms` cautiously.

## Streaming (downstream_mode=stream)

Controls the pacing and robustness of streamed agent audio.

- streaming.sample_rate: Output sample rate (typically 8000 for telephony).
- streaming.jitter_buffer_ms: 80–150 ms. Higher = more robust to jitter, slightly higher latency.
- streaming.keepalive_interval_ms: TCP keepalive interval for streaming connections.
- streaming.connection_timeout_ms: Time to consider a streaming connection dead.
- streaming.fallback_timeout_ms: No audio for this long triggers fallback to file playback.
- streaming.chunk_size_ms: 20 ms recommended for telephony cadence.
- streaming.min_start_ms: 250–400 ms. Warm‑up buffer before first frame; too low risks underruns.
- streaming.low_watermark_ms: Brief pause/guard band; increase if underruns occur.
- streaming.provider_grace_ms: Absorb late provider chunks to avoid tail-chop artifacts.
- streaming.logging_level: Verbosity for the streaming manager.
- streaming.egress_force_mulaw: When true, converts outbound streaming audio to μ-law 8 kHz regardless of provider encoding.

## VAD (Voice Activity Detection)

Defines how inbound speech is segmented into utterances for STT.

- vad.webrtc_aggressiveness: 0–3. 0=least aggressive (best for 8 kHz telephony), 3=most aggressive (may clip speech).
- vad.webrtc_start_frames: Consecutive frames above threshold to start recording.
- vad.webrtc_end_silence_frames: Silence frames to finalize an utterance (e.g., 50 → ~1000 ms at 20 ms frames).
- vad.min_utterance_duration_ms: Lower bound on utterance length. Raise if STT returns empty.
- vad.max_utterance_duration_ms: Hard cap to prevent runaway capture.
- vad.utterance_padding_ms: Padding around detected speech.
- vad.fallback_enabled: When true, sends audio at a fixed interval if VAD fails to detect speech.
- vad.fallback_interval_ms: Interval between fallback sends.
- vad.fallback_buffer_size: Bytes to accumulate at fallback thresholds.

Common pitfalls:

- Too-short utterances (e.g., 20 ms) cause empty STT transcripts → raise `min_utterance_duration_ms` and ensure `webrtc_end_silence_frames` is not too low.
- Overly aggressive VAD (aggressiveness=2/3) may clip 8 kHz speech; prefer 0–1 for telephony.

## LLM block

- llm.initial_greeting: First message spoken by the agent (if provider supports explicit greeting or engine plays via TTS).
- llm.prompt: Persona/system instruction used by LLMs.
- llm.model: Baseline LLM name (used by some monolithic providers and Deepgram agent think stage).
- llm.api_key: Optional API key for LLMs that require it.

## Providers

### OpenAI Realtime (monolithic agent)

- providers.openai_realtime.api_key: Bearer auth.
- providers.openai_realtime.model, voice, base_url: Model and voice.
- providers.openai_realtime.instructions: Persona override. Leave empty to inherit `llm.prompt`.
- providers.openai_realtime.greeting: Explicit greeting. Leave empty to inherit `llm.initial_greeting`.
- providers.openai_realtime.response_modalities: `audio`, `text`.
- providers.openai_realtime.input_encoding/input_sample_rate_hz: Inbound format; use `ulaw` at 8 kHz when AudioSocket() is invoked with `,ulaw` (engine converts to PCM before sending to OpenAI).
- providers.openai_realtime.output_encoding/output_sample_rate_hz: Provider output; engine resamples to target.
- providers.openai_realtime.target_encoding/target_sample_rate_hz: Downstream transport expectations (e.g., μ‑law at 8 kHz).
- providers.openai_realtime.turn_detection: Server‑side VAD (type, silence_duration_ms, threshold, prefix_padding_ms); improves turn handling.
- Metrics: `ai_agent_openai_assumed_output_sample_rate_hz`, `ai_agent_openai_provider_output_sample_rate_hz`, and `ai_agent_openai_measured_output_sample_rate_hz` expose handshake vs. measured output rates per call.

### Deepgram Voice Agent

- providers.deepgram.api_key, model, tts_model.
- providers.deepgram.greeting: Agent greeting. Leave empty to inherit `llm.initial_greeting`.
- providers.deepgram.instructions: Persona override for the “think” stage; leave empty to inherit `llm.prompt`.
- providers.deepgram.input_encoding/input_sample_rate_hz: Keep `input_encoding=ulaw` at 8 kHz when AudioSocket runs μ-law transport.
- providers.deepgram.continuous_input: true to stream audio continuously.
- Metrics: `ai_agent_deepgram_input_sample_rate_hz` and `ai_agent_deepgram_output_sample_rate_hz` confirm negotiated codec settings per call.

### Google (pipelines)

- google_llm.system_instruction/system_prompt: Persona; if missing, adapter falls back to `llm.prompt`.
- google_tts/tts fields: voice, language, audio encoding/sample rate, target format.
- google_stt/stt fields: encoding, language, model, sampleRateHertz.

### Local provider (pipelines)

- Local STT/LLM/TTS parameters live under pipeline `options`. The engine plays `llm.initial_greeting` first if configured.

## Precedence summary

- Provider/pipeline explicit overrides (instructions/greeting) take priority.
- Otherwise providers/pipelines inherit `llm.prompt` / `llm.initial_greeting`.
- Env `AI_ROLE`/`GREETING` act as defaults when YAML does not specify values.

## Tips

- For noisy trunks, start with:
  - `barge_in.energy_threshold=2200`, `barge_in.min_ms=450`, `vad.webrtc_aggressiveness=1`.
- For lowest latency, start with:
  - `streaming.min_start_ms=250`, `streaming.jitter_buffer_ms=80`, `barge_in.min_ms=300` (expect more sensitivity to jitter).
