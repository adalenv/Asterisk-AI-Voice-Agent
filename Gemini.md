# Gemini.md — GA Build & Ops Guide

This playbook summarizes how Gemini should operate on the Asterisk AI Voice Agent project. It mirrors `Agents.md`, the Windsurf rules, and the milestone instruction files under `docs/milestones/`.

## Mission & Scope

- **Primary objective**: Deliver the GA roadmap (Milestones 5–8) so the agent streams clean audio by default, supports both Deepgram and OpenAI Realtime, allows configurable pipelines, and exposes an optional monitoring stack.
- Always verify that `audio_transport=audiosocket` remains the default, with file playback as fallback when streaming pauses.
- Reference milestone instruction files before starting work:
  - `docs/milestones/milestone-5-streaming-transport.md`
  - `docs/milestones/milestone-6-openai-realtime.md`
  - `docs/milestones/milestone-7-configurable-pipelines.md`
  - `docs/milestones/milestone-8-monitoring-stack.md`

## Architectural Snapshot

- Two containers: `ai-engine` (Hybrid ARI controller + AudioSocket server) and `local-ai-server` (local STT/LLM/TTS).
- Upstream audio: Asterisk AudioSocket → `ai-engine` → provider (Deepgram/OpenAI/local).
- Downstream audio: Streaming transport managed by `StreamingPlaybackManager`; automatic fallback to tmpfs-based file playback.
- State: `SessionStore` + `ConversationCoordinator` orchestrate capture gating, playback, and metrics.
- Local provider: idle-finalize STT after ~1.2 s of silence, run TinyLlama via `asyncio.to_thread`, and rely on the engine’s ingest/transcript queues with transcript aggregation (≥ 3 words or ≥ 12 chars) so slow LLM responses never block AudioSocket or trigger premature replies.

## Configuration Keys to Watch

- `audio_transport`, `downstream_mode`, `audiosocket.format`.
- Streaming defaults (`streaming.min_start_ms`, `low_watermark_ms`, `fallback_timeout_ms`, `provider_grace_ms`).
- Pipeline definitions (`pipelines`, `active_pipeline`) once Milestone 7 lands.
- `vad.use_provider_vad` toggles provider-managed speech detection; leave local WebRTC/Enhanced VAD disabled when this is true.
- `openai_realtime.provider_input_sample_rate_hz` now sits at 24000 so inbound audio is upsampled to the Realtime API’s required 24 kHz PCM before commit.
- Session updates for OpenAI must set `input_audio_format` / `output_audio_format` to PCM16 (24 kHz); we still convert back to μ-law at the AudioSocket boundary.
- Local overrides: `LOCAL_WS_CHUNK_MS` defaults to 320 ms, `LOCAL_WS_RESPONSE_TIMEOUT` to 5 s, and `LOCAL_STT_IDLE_MS` now defaults to 1200 ms (tune per environment as needed).
- Logging levels per component (set via YAML when hot reload is implemented).

## Development Workflow

1. Work on the `develop` branch locally.
2. Use Makefile targets for builds/deploys (`make deploy`, `make deploy-force`, `make server-logs`, etc.).
3. Before touching the server, **commit and push** your changes; the server must `git pull` the exact commit you just pushed prior to any `docker-compose up --build`.
4. Never use `docker-compose restart` for code updates—always rebuild.

### Deployment Environment

- Server: `root@voiprnd.nemtclouddispatch.com`
- Repo: `/root/Asterisk-AI-Voice-Agent`
- Shared media: `/mnt/asterisk_media`

### Key Commands (local or server)

- `make deploy` / `make deploy-force`
- `make server-logs`, `make server-clear-logs`
- `make monitor-up` / `make monitor-down` (Milestone 8)
- `docker-compose logs -f ai-engine`, `docker-compose logs -f local-ai-server`

## GA Milestones — Gemini Focus

- **Milestone 5**: Implement streaming pacing config, telemetry, and documentation updates.
- **Milestone 6**: Add OpenAI Realtime provider with codec-aware streaming.
- **Milestone 7**: Support YAML-defined pipelines with hot reload.
- **Milestone 8**: Ship optional Prometheus + Grafana monitoring stack.
- After completion, assist with GA regression runs and documentation polish.

## GPT-5 Prompting Guidance

- **Precision & consistency**: Ensure prompts in Gemini stay aligned with `Agents.md`, `.cursor/rules/…`, and `.windsurf/rules/…`; avoid conflicting directions when updating workflows.
- **Structured prompts**: Use XML-style wrappers to organize guidance, for example:

  ```xml
  <code_editing_rules>
    <guiding_principles>
      - keep AudioSocket upstream primary; fall back to file playback automatically
    </guiding_principles>
    <reasoning_effort level="high" applies_to="streaming_changes"/>
  </code_editing_rules>
  ```

- **Reasoning effort**: Call for `high` effort on milestone-sized changes (streaming transport, pipeline orchestration); prefer medium or low on incremental edits to keep responses focused.
- **Tone calibration**: Favor cooperative language over rigid or all-caps directives so GPT-5 does not overreact to urgency cues.
- **Planning & self-reflection**: When starting novel functionality, include a `<self_reflection>` block prompting the model to outline a brief plan before coding.
- **Eagerness control**: Bound exploration with explicit tool budgets or `<persistence>` directives, specifying when to assume reasonable defaults versus re-querying the user.

Mirror any edits to this section into `Agents.md`, `.cursor/rules/asterisk_ai_voice_agent.mdc`, and `.windsurf/rules/asterisk_ai_voice_agent.md`.

## Regression & Troubleshooting Workflow

1. Clear logs (`make server-clear-logs`).
2. Tail `ai-engine`, `local-ai-server`, and Asterisk logs during calls.
3. Pull remote `ai-engine` logs when needed: `timestamp=$(date +%Y%m%d-%H%M%S); ssh root@voiprnd.nemtclouddispatch.com "cd /root/Asterisk-AI-Voice-Agent && docker-compose logs ai-engine --since 30m --no-color" > logs/ai-engine-voiprnd-$timestamp.log`.
4. Record call ID, streaming metrics, and tuning hints in `docs/regressions/*.md` and `call-framework.md`.
5. For streaming issues, inspect buffer depth logs and fallback counters; adjust YAML settings accordingly.

## Hot Reload Expectations

- Configuration watcher (or `make engine-reload`) refreshes streaming defaults, logging levels, and pipeline definitions without dropping active calls.
- Always validate config changes (`docs/milestones/milestone-7-configurable-pipelines.md`) before reloading.

## Monitoring Stack Notes

- Optional services added in Milestone 8 expose dashboards at the configured HTTP port.
- Ensure `/metrics` is reachable and that Grafana dashboards load streaming and latency panels.
- Document sentiment/transcript hooks for future enhancements.

## Logging & Metrics Etiquette

- Run at INFO in GA mode; enable DEBUG only when instructed and remember to revert.
- Capture `/metrics` snapshots after regression calls to populate dashboards.

## Deliverables Checklist Before Hand-off

- Updated documentation (`docs/Architecture.md`, `docs/ROADMAP.md`, milestone files, rule files).
- Regression notes logged with call IDs and audio quality assessment.
- Telemetry hints reviewed; YAML defaults adjusted if streaming restarts persist.

## Troubleshooting Steps (Recap)

1. Clear logs.
2. Reproduce call while tailing `ai-engine`, `local-ai-server`, and `/var/log/asterisk/full`.
3. Build a timeline; identify streaming restarts, buffer drops, or provider disconnects.
4. Apply fixes guided by milestone docs, then rerun regression.

---
*Keep this file aligned with `Agents.md` and `.windsurf/rules/asterisk_ai_voice_agent.md`. Update it whenever milestone scope or workflow changes.*
