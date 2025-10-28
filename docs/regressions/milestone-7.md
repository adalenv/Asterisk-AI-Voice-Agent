# Milestone 7 Regression Guide — Configurable Pipelines & Hot Reload

Milestone 7 introduces pipeline orchestration, provider adapters, and hot reload. This guide documents the validation steps, tooling, and observability expectations to confirm the feature set is production-ready.

---

## 1. Prerequisites

1. **Environment Variables**
   - `ASTERISK_HOST`, `ASTERISK_ARI_USERNAME`, `ASTERISK_ARI_PASSWORD`
   - Provider keys as-needed:
     - `DEEPGRAM_API_KEY`
     - `OPENAI_API_KEY`
     - `GOOGLE_API_KEY` *or* `GOOGLE_APPLICATION_CREDENTIALS`
2. **Artifacts**
   - Config examples under [`examples/pipelines`](../../examples/pipelines):
     - [`local_only.yaml`](../../examples/pipelines/local_only.yaml)
     - [`hybrid_deepgram_openai.yaml`](../../examples/pipelines/hybrid_deepgram_openai.yaml)
     - [`cloud_only_google.yaml`](../../examples/pipelines/cloud_only_google.yaml)
3. **Services**
   - Local stack running via `docker-compose up ai-engine local-ai-server`.
   - Asterisk context routing calls into `Stasis(asterisk-ai-voice-agent)` with AudioSocket enabled (`app_audiosocket` required).

---

## 2. Hot Reload Validation

1. **Baseline**

   ```bash
   cp examples/pipelines/local_only.yaml config/ai-agent.yaml
   make engine-reload
   ```

   - Place a call → confirm local STT/LLM/TTS path in logs.
   - Metrics: `pipeline="local_only"` labels appear in `/metrics`.
2. **Switch Pipeline**

   ```bash
   cp examples/pipelines/hybrid_deepgram_openai.yaml config/ai-agent.yaml
   make engine-reload
   ```

   - Verify reload log: `Pipeline configuration reload succeeded`.
   - New calls show Deepgram/OpenAI adapters; existing calls stay on previous adapters.
3. **Failure Handling**
   - Introduce invalid YAML (e.g., typo in `stt` key), reload, and confirm:
     - Reload is rejected with validation errors.
     - Active pipeline remains unchanged.
     - Operator guidance emitted in logs (`Reload rejected; keeping previous pipeline.`).

---

## 3. Provider Regression Matrix

| Pipeline | Expected Adapters | Validation Steps |
|----------|-------------------|------------------|
| `local_only` | `local_stt`, `local_llm`, `local_tts` | Confirm Local AI server receives STT audio, emits transcripts, and returns μ-law TTS for playback. |
| `hybrid_deepgram_openai` | `local_stt`, `openai_realtime`, `deepgram_tts` | Check Deepgram REST synthesis logs (voice + format), OpenAI realtime session start, and ensure fallback to local STT works. |
| `cloud_only_google` | `google_stt`, `google_llm`, `google_tts` | Validate Google REST responses (HTTP 200). Confirm TTS audio arrives in μ-law and LLM responses include expected metadata. |

**Testing Commands**

```bash
pytest tests/test_pipeline_deepgram_adapters.py
pytest tests/test_pipeline_openai_adapters.py
pytest tests/test_pipeline_google_adapters.py
```

Each suite validates option propagation, factory registration, and error handling.

---

## 4. Call Workflow Checklist

For each pipeline:

1. **Greeting**
   - `PlaybackManager` logs: `Bridge playback started` with pipeline label.
2. **Upstream Audio**
   - `AudioSocket inbound chunk` logs with provider alias.
   - `/metrics`: `ai_agent_pipeline_audio_bytes_total{pipeline="..."}` increments.
3. **Provider Round Trip**
   - STT transcript log includes provider label (e.g., `provider=google_stt`).
   - LLM response log with `pipeline` and `component`.
   - TTS completion log showing audio size and format.
4. **Playback**
   - `PlaybackFinished` event clears gating tokens.
   - `/metrics`: `ai_agent_last_turn_latency_seconds{pipeline="..."}` updates.

Capture `/metrics` snapshot after each call for historical dashboards, per GA guidance.

---

## 5. Logging Expectations

- **Reload Watcher**: `Config change detected`, `Validation success`, or `Validation failure`.
- **Adapter Readiness**: `Adapter ready` logs for STT/LLM/TTS after instantiation.
- **Fallback Warnings**: On credential absence, orchestrator logs fallback to placeholder adapters.
- **Error Handling**: HTTP/WS failures surface as `error` logs with provider-specific context (`component`, `pipeline`, `request_id`).

---

## 6. Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|--------------|-----------|
| Reload rejected | Schema validation error | Fix YAML, rerun `make engine-reload`. |
| No audio output | TTS adapter credentials missing | Ensure provider API keys are set; check adapter readiness logs. |
| Pipeline label missing in metrics | Reload failure or stale orchestrator | Confirm reload succeeded; restart engine if necessary. |
| Hot reload kills active call | In-flight session not isolated | Regression failure—collect logs and open a bug before release. |

---

## 7. Validation Log Template

Capture for each regression run:

```
Date/Time:
Pipeline:
Adapters (stt/llm/tts):
Reload Result:
STT Transcript Sample:
LLM Response Summary:
TTS Playback ID:
Metrics Snapshot (key gauges):
Notes:
```

Store alongside call recordings or analysis artifacts for Milestone 8 dashboards.

---

## 8. Completion Criteria

- All pipelines load and execute end-to-end without restarts.
- Hot reload reliably swaps between sample configs with clear operator logs.
- Metrics reflect `pipeline` and `component` labels for every call.
- Regression notes recorded in this document (append sections per run).
- Milestone documentation updated (`docs/milestones/milestone-7-configurable-pipelines.md`) with validation summary — completed in Phase 5.

Once the above is satisfied, mark Milestone 7 Phase 5 regression tasks complete and coordinate with Milestone 8 for monitoring integration.
