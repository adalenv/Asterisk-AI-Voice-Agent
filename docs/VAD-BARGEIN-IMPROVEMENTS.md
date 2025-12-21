# VAD + Barge-In Improvements (Option 2: Provider-Owned Turn-Taking, Platform Flush)

This document captures the agreed plan to improve barge-in UX across full-agent providers (Google Live, Deepgram, OpenAI Realtime, ElevenLabs) while keeping hybrid/local pipelines stable.

## Goals

- **Immediate audible interruption**: when the caller starts speaking over agent TTS, the caller should hear agent audio stop immediately.
- **No collision with provider turn-taking**: for full-agent providers that already run native VAD/barge-in, the platform should not fight them by cancelling sessions or manipulating provider state.
- **Safe fallback**: if the provider does not emit an explicit “user started speaking” event, the platform may use local VAD **only** when we can prove the inbound audio is caller-isolated.
- **Transport parity**: works for both `audio_transport=externalmedia` and `audio_transport=audiosocket`.
- **Downstream parity**:
  - Full agents: `downstream_mode=stream` supported (primary path)
  - Hybrid/local pipelines: file playback remains supported (`downstream_mode=file`)

## Contract (Capability + Responsibilities)

### Provider-owned VAD/barge-in (full agents)

**Provider responsibilities**
- Detect caller speech (native VAD).
- Decide when to stop/cancel a response generation (turn-taking).
- Potentially emit a provider “interruption” event to the platform (if supported).

**Platform responsibilities**
- Treat provider as authoritative for turn-taking.
- On interruption signal, **stop/flush local playback only**:
  - stop streaming playback immediately
  - stop any ARI playbacks (edge cases)
  - clear any platform gating tokens (pipeline/file playback only)
- Do **not** cancel provider sessions for Google/Deepgram.
  - OpenAI/ElevenLabs can still cancel internally in their provider code; platform does not initiate cancellation for them in this mode.

### Platform-owned VAD/barge-in (hybrid/local pipelines)

Platform remains authoritative for VAD gating + endpointing (existing behavior).

## Triggers

### 1) Provider event trigger (preferred)

When the provider emits a barge-in/interruption signal:
- Engine applies **flush-only** action immediately.

Examples:
- OpenAI Realtime: `input_audio_buffer.speech_started` → provider cancels response internally → engine flushes local playback.
- ElevenLabs: `interruption` event → engine flushes local playback.

### 2) Local VAD fallback trigger (only when safe)

Used primarily for providers that do not emit an explicit interruption event (currently: Google Live, Deepgram).

Local VAD fallback is allowed only if all of the following are true:
- **Media path confirmed**: first inbound audio frame observed for the call.
- **Agent is currently speaking**: streaming playback is active for the call.
- **Inbound audio is caller-isolated**:
  - no ARI playback is active for the call
  - no bridge MOH is active (background music/hold)

If those conditions are met:
- local VAD/energy detects caller speech overlap → engine applies **flush-only** action
- inbound audio continues to stream to provider uninterrupted (provider remains authoritative)

## Implementation Phases

### Phase 1 (this branch)
- Add “media RX confirmed” gating (first inbound frame seen) to prevent setup races.
- Centralize the flush-only barge-in action in engine (`_apply_barge_in_action`).
- Wire provider interruption events into engine (OpenAI + ElevenLabs).
- Add local VAD fallback for Google Live + Deepgram under strict “caller-isolated” gating.
- Add observability:
  - barge-in source (`provider_event` vs `local_vad_fallback`)
  - last reason/event
  - barge-in counters per call

### Phase 2 (defer / follow-up)
- Greeting-specific behavior (first-turn protection tuning).
- Stronger isolation proof (bridge membership validation / ARI bridge inspection).
- Endpointing FSM (true “speech start/end” + turn state machine) for platform-owned mode.

## Knobs (expected)

- `barge_in.*`:
  - `enabled`, `min_ms`, `energy_threshold`, `cooldown_ms`
  - local fallback allowlist for providers (Google/Deepgram)
- `vad.*`:
  - WebRTC VAD aggressiveness and confidence/energy thresholds (platform VAD path)

## Why this approach

- Matches production patterns: for full agents, treat provider as turn-taking authority, and keep platform actions limited to deterministic local media control (flush).
- Minimizes multi-provider regressions: platform does not cancel/stop provider sessions (except provider-internal logic).
- Gives immediate UX win without requiring users to be telephony experts.

