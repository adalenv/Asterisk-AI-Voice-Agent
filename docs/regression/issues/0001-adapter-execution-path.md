# Issue 0001: Adapter execution path not wired into engine

- Status: Open
- Priority: High
- Affects: `src/engine.py` runtime

## Summary

Pipeline adapters (`LocalSTTAdapter`, `LocalLLMAdapter`, `LocalTTSAdapter`) are registered and unit-tested, but the engine does not yet execute STT→LLM→TTS via adapters. Inbound audio continues to be sent to full-agent providers via `provider.send_audio(...)`.

## Evidence

- `src/pipelines/local.py` exists and tests pass: `tests/test_pipeline_local_adapters.py`.
- `src/pipelines/orchestrator.py` builds `PipelineResolution`.
- `src/engine.py` calls `pipeline_orchestrator.get_pipeline(...)` and stores component metadata, but never calls `transcribe()`, `generate()`, or `synthesize()`.

## Impact

Configured pipelines will not take effect at runtime; conversations continue via the legacy full-agent provider path. This blocks Milestone 7 acceptance for adapter-driven paths.

## Proposed Fix

- Introduce an adapter-driven execution loop in `src/engine.py` to:
  - Buffer inbound 16 kHz PCM frames.
  - Call `stt_adapter.transcribe(call_id, pcm16, 16000, options)`.
  - Call `llm_adapter.generate(call_id, transcript, context, options)`.
  - Stream `tts_adapter.synthesize(call_id, text, options)` through `StreamingPlaybackManager` or fall back to file playback.
- Maintain full-agent providers for explicit per-call overrides.

## Acceptance Criteria

- When a pipeline is assigned to a session, the adapter loop is used.
- Deepgram/OpenAI full-agent overrides continue to work via `provider.send_audio(...)`.
- Regression test to confirm adapter execution path with mocked adapters.
