# Deepgram Voice Agent API — Implementation Reference

This document summarizes the parts of Deepgram’s Voice Agent API that are relevant to our telephony integration. It is based on the following Deepgram docs:

- Configure the Voice Agent: <https://developers.deepgram.com/docs/configure-voice-agent>
- Settings (SettingsConfiguration): <https://developers.deepgram.com/docs/voice-agent-settings-configuration>
- Getting Started: <https://developers.deepgram.com/docs/voice-agent>
- Voice Agent Audio & Playback: <https://developers.deepgram.com/docs/voice-agent-audio-playback>
- Authentication: <https://developers.deepgram.com/reference/authentication>

Note: The legacy reference path sometimes linked as “reference/voice-agent/agent” can 404. Prefer the docs above for canonical field names and examples.

---

## WebSocket endpoint and authentication

- Endpoint (via SDK): Use the SDK method (e.g. `deepgram.agent.v1.connect()`).
- Raw WS endpoints observed in docs/ecosystem:
  - `wss://agent.deepgram.com/v1/agent`
  - `wss://agent.deepgram.com/v1/agent/converse`
  - Ambiguity: Current docs we can access do not canonize a single URL string; prefer the SDK.

- Authentication header:
  - `Authorization: Token <YOUR_DEEPGRAM_API_KEY>`
  - Token-based short‑lived access also supported via Token API (see Authentication docs).

---

## Initial Settings message (client → server)

Send immediately after connecting, before streaming any audio. The server replies with `SettingsApplied` when accepted.

Top‑level fields:

- `type`: "Settings"
- `tags`: string[]
- `experimental`: boolean
- `mip_opt_out`: boolean
- `flags.history`: boolean
- `audio`: object
- `agent`: object

Audio configuration:

- `audio.input`
  - `audio.input.encoding`: string (e.g., "linear16", "ulaw")
  - `audio.input.sample_rate`: number (Hz) (e.g., 8000, 16000, 24000)
- `audio.output`
  - `audio.output.encoding`: string (e.g., "linear16", "mp3")
  - `audio.output.sample_rate`: number (Hz) (e.g., 24000)
  - `audio.output.bitrate`: number (bps) (mp3 example: 48000)
  - `audio.output.container`: string (e.g., "none", "wav")

Agent configuration:

- `agent.language`: string (e.g., "en-US"). Notes:
  - With Flux models, `agent.language` is ignored for STT but may be propagated to TTS settings.
- `agent.context`: optional
  - `agent.context.messages`: history seed entries (type/role/content, etc.)
- `agent.listen.provider`
  - `type`: "deepgram"
  - `model`: STT model (e.g., "nova-3", "nova-2-general", "flux-general-en")
  - `keyterms`: string[] (deepgram, nova-3 en-only)
  - `smart_format`: boolean
- `agent.think.provider`
  - `type`: "open_ai" | "anthropic" | "google" | "groq" | custom
  - `model`: string (e.g., "gpt-4o", "gpt-4o-mini")
  - `temperature`: number
  - Optional: `credentials`, `endpoint` { `url`, `headers` }, `functions`, `prompt`, `context_length`
- `agent.speak.provider`
  - `type`: "deepgram" | "open_ai" | "eleven_labs" | "cartesia" | "aws_polly"
  - `model`: string (e.g., "aura-2-thalia-en" for Deepgram)
  - Provider‑specific: `model_id`, `voice` { `mode`, `id"` }, `language`, `language_code`, `engine`, `credentials`, `endpoint`
- `agent.greeting`: string

Minimal example from docs (normalized formatting):

```json
{
  "type": "Settings",
  "tags": ["demo", "voice_agent"],
  "audio": {
    "input": { "encoding": "linear16", "sample_rate": 24000 },
    "output": { "encoding": "linear16", "sample_rate": 24000, "container": "none" }
  },
  "agent": {
    "language": "en",
    "listen": { "provider": { "type": "deepgram", "model": "nova-3", "smart_format": false } },
    "think": { "provider": { "type": "open_ai", "model": "gpt-4o-mini", "temperature": 0.7 } },
    "speak": { "provider": { "type": "deepgram", "model": "aura-2-thalia-en" } },
    "greeting": "Hello! How can I help you today?"
  }
}
```

---

## Server → client messages (canonical names)

- `Welcome`

```json
{ "type": "Welcome", "request_id": "550e8400-e29b-41d4-a716-446655440000" }
```

- `SettingsApplied`

```json
{ "type": "SettingsApplied" }
```

- `ConversationText`

```json
{ "type": "ConversationText", "role": "user", "content": "What's the weather like today?" }
```

- `UserStartedSpeaking`

```json
{ "type": "UserStartedSpeaking" }
```

- `AgentAudio`
  - Carries TTS audio frames matching the negotiated `audio.output` (e.g., linear16@24000). Exact payload envelope is not fully specified in accessible docs; rely on SDK or inspect runtime messages.

- `History`
  - Used within context priming and sometimes sent as part of conversation state; shape varies.

- `Error`

```json
{ "type": "Error", "message": "Error details..." }
```

Ambiguity: Full JSON schemas for `AgentAudio` and some event bodies are not provided in the pages above; use SDK or inspect logs to document actual payloads.

---

## Client audio streaming

- Keep Alive: SDK examples use periodic keep‑alive messages (e.g., `AgentKeepAlive`).
- Audio streaming: Examples show sending audio chunks over the WS. The docs we accessed do not specify a canonical message name/shape or raw binary vs JSON‑wrapped payload for input audio; the SDK abstracts this. For raw WS integration, mirror SDK behavior or capture traffic to align with server expectations.

---

## Supported encodings and sample rates (as seen in docs/examples)

- Input encodings: "linear16" (PCM), "ulaw"
- Output encodings: "linear16", "mp3" (with `bitrate`, `container`)
- Common sample rates:
  - Telephony input: 8000 (μ‑law)
  - STT input (PCM): 16000 or 24000
  - TTS output (PCM): 24000

Playback troubleshooting notes:

- Verify receipt of `SettingsApplied` after sending `Settings`.
- Incorrect `encoding`/`sample_rate` leads to static/garbled audio.

---

## Telephony‑ready Settings (our baseline)

We accept μ‑law @ 8000 from Asterisk and prefer provider output as PCM16 @ 24000. The engine performs DC‑block and μ‑law compand to 8 kHz for AudioSocket.

```json
{
  "type": "Settings",
  "audio": {
    "input": { "encoding": "ulaw", "sample_rate": 8000 },
    "output": { "encoding": "linear16", "sample_rate": 24000, "container": "none" }
  },
  "agent": {
    "language": "en-US",
    "listen": { "provider": { "type": "deepgram", "model": "nova-2-general", "smart_format": false } },
    "think": { "provider": { "type": "open_ai", "model": "gpt-4o", "temperature": 0.6 }, "prompt": "You are a concise voice assistant. Respond clearly and keep answers under 20 words unless more detail is requested." },
    "speak": { "provider": { "type": "deepgram", "model": "aura-2-thalia-en" } },
    "greeting": "Hello, how can I help you today?"
  }
}
```

---

## Best practices for telephony

- **Audio correctness**
  - Wait for `SettingsApplied` before play/stream.
  - Keep wire format explicit and consistent (μ‑law 8 kHz or PCM16 8 kHz).
  - Apply a DC‑blocker (single‑pole high‑pass) in PCM16 before μ‑law compand; use soft limiting to avoid clipping.

- **Pacing/drift**
  - 20 ms frames; warm‑up 300–400 ms; low‑watermark 200–300 ms.
  - Pacer must align to fixed 20 ms ticks and correct accumulated error.

- **Diagnostics**
  - Log ACK `audio.output.*` and first‑chunk inferred encoding/rate.
  - Capture first 200 ms pre‑normalization and wire `out-`/`mix-` for RCA.

---

## Ambiguities / undocumented aspects in accessible docs

- Raw WS URL: `wss://agent.deepgram.com/v1/agent` vs `.../v1/agent/converse` appears across materials; prefer SDK connect.
- `AgentAudio` event schema and raw client input audio message shape are not fully specified in public pages above; the SDK hides these details.
- Supported encoding/rate lists are presented via examples rather than exhaustive enumerations.
