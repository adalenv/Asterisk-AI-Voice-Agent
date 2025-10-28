# Agents.md — Build & Ops Guide for Codex Agent

This document captures how I (the agent) work most effectively on this repo. It distills the project rules, adds hands‑on runbooks, and lists what I still need from you to build, deploy, and test quickly.

## Mission & Scope

- **Current mandate (GA track)**: Execute Milestones 5–8 to deliver production‑ready streaming audio, dual cloud providers (Deepgram + OpenAI Realtime), configurable pipelines, and an optional monitoring stack. Each milestone has a dedicated instruction file under `docs/milestones/`.
- **Always ensure** the system remains AudioSocket-first with file playback as fallback; streaming transport must be stable out of the box.

## Current Status (2025-09-23)

- Deepgram AudioSocket regression passes end-to-end, but streaming transport still restarts after greeting; Milestone 5 addresses adaptive pacing and jitter buffering (`docs/milestones/milestone-5-streaming-transport.md`).
- Latency histograms/gauges (`ai_agent_turn_latency_seconds`, `ai_agent_transcription_to_audio_seconds`, `ai_agent_last_turn_latency_seconds`) are emitted during calls; capture `/metrics` snapshots before restarting containers so dashboards (Milestone 8) have data.
- Streaming defaults (`streaming.min_start_ms`, etc.) will be configurable via YAML; ensure documentation updates land in `docs/Architecture.md` and `docs/ROADMAP.md` after each change.
- IDE rule files (`Agents.md`, `.windsurf/rules/...`, `Gemini.md`, `.cursor/rules/asterisk_ai_voice_agent.mdc`) must stay in sync; update them whenever workflow expectations shift.
`develop` mirrors `main` plus the IDE rule set and `tools/ide/Makefile.ide`. Use `make -f tools/ide/Makefile.ide help` for the rapid inner-loop targets described in `tools/ide/README.md`.
- Local-only pipeline now has idle-finalized, aggregated STT: the local server promotes partials to finals after ~1.2 s of silence and the engine drains AudioSocket frames while buffering transcripts until they reach ≥ 3 words or ≥ 12 chars, so slow TinyLlama responses no longer stall STT.

## Architecture Snapshot (Current) — Runtime Contexts (Always Current)

- Two containers: `ai-engine` (ARI + AudioSocket) and `local-ai-server` (models).
- Upstream (caller → engine): AudioSocket TCP into the engine.
- Downstream (engine → caller): ARI file playback via tmpfs for low I/O latency.
- Providers: pluggable via `src/providers/*` (local, deepgram, etc.).

Active contexts and call path (server):

- `ivr-3` (example) → `from-ai-agent` → Stasis(asterisk-ai-voice-agent)
- Engine originates `Local/<exten>@ai-agent-media-fork/n` to start AudioSocket
- `ai-agent-media-fork` generates canonical UUID, calls `AudioSocket(UUID, host:port)`, sets `AUDIOSOCKET_UUID=${EXTEN}` for binder
- `ai-engine` now embeds the AudioSocket TCP listener itself (`config/ai-agent.yaml` → `audiosocket.host/port`, default `0.0.0.0:8090`)
- Engine binds socket to caller channel; sets upstream input mode `pcm16_8k`; provider greets immediately (no demo tone)

## Feature Flags & Config

- `audio_transport`: `audiosocket` (default) | `externalmedia` (fallback RTP path) | `legacy` (deprecated snoop path).
- `downstream_mode`: `file` (default) | `stream` (enabled once Milestone 5 tasks complete; retains file fallback automatically).
- `streaming.*` (Milestone 5): `min_start_ms`, `low_watermark_ms`, `fallback_timeout_ms`, `provider_grace_ms`, `chunk_size_ms`, `jitter_buffer_ms`.
- `pipelines` (Milestone 7): defines STT/LLM/TTS combinations; `active_pipeline` selects which pipeline new calls use.
- `vad.use_provider_vad`: when `true`, rely on provider (e.g., OpenAI server VAD) and disable local WebRTC/Enhanced VAD.
- `openai_realtime.provider_input_sample_rate_hz`: set to `24000` so ingested audio is upsampled to the Realtime API’s expected 24 kHz linear PCM before commit.
- OpenAI session config should advertise `input_audio_format` / `output_audio_format` as PCM16 24 kHz; the engine handles μ-law conversion at the AudioSocket edges.
- Logging levels are configurable per service via YAML once the hot-reload work lands; default is INFO for GA builds.

## Pre‑flight Checklist (Local or Server)

- Asterisk:
  - `app_audiosocket.so` loaded: `module show like audiosocket`.
  - Dialplan context uses AudioSocket + Stasis.
  - ARI enabled (http.conf, ari.conf) and user has permissions.
- System:
  - Docker + docker‑compose installed.
  - `/mnt/asterisk_media` mounted as tmpfs (or fast storage) and mapped for the engine.
- Secrets:
  - `.env` present with `ASTERISK_HOST`, `ASTERISK_ARI_USERNAME`, `ASTERISK_ARI_PASSWORD`, provider API keys.

## Dialplan Example (AudioSocket + Stasis)

```
[ai-voice-agent]
exten => s,1,NoOp(Starting AI Voice Agent with AudioSocket)
 same => n,Set(AUDIOSOCKET_HOST=127.0.0.1)
 same => n,Set(AUDIOSOCKET_PORT=8090)
 same => n,Set(AUDIOSOCKET_UUID=${UNIQUEID})
 same => n,AudioSocket(${AUDIOSOCKET_UUID},${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT},ulaw)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

### Deepgram Test Entry (Provider Override)

Add a dedicated context when you want to force the Deepgram provider without touching the default local flow:

```
[ai-voice-agent-deepgram]
exten => s,1,NoOp(AudioSocket AI Voice Agent using Deepgram)
 same => n,Set(AI_PROVIDER=deepgram)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[ai-agent-media-fork]
exten => _X.,1,NoOp(Local channel starting AudioSocket for ${EXTEN})
 same => n,Answer()
 same => n,Set(AUDIOSOCKET_HOST=127.0.0.1)
 same => n,Set(AUDIOSOCKET_PORT=8090)
 same => n,Set(AUDIOSOCKET_UUID=${EXTEN})
 same => n,AudioSocket(${AUDIOSOCKET_UUID},${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT},ulaw)
 same => n,Hangup()

; keep ;1 leg alive while the engine streams audio
exten => s,1,NoOp(Local)
 same => n,Wait(60)
 same => n,Hangup()
```

Route specific DIDs or test extensions to `ai-voice-agent-deepgram` when exercising streaming; leave existing routes on `[ai-voice-agent]` so the local provider flow stays untouched. The engine reads `AI_PROVIDER` on `StasisStart` and falls back to the configured default when the variable is absent.

## Active Contexts & Usage (Server)

- Entry context (`from-ai-agent`): hands call directly to `Stasis(asterisk-ai-voice-agent)`.
- Media-fork context (`ai-agent-media-fork`): originated by the engine to start AudioSocket.
  - Generates canonical UUID and calls `AudioSocket(UUID, host:port)`
  - Sets `AUDIOSOCKET_UUID=${EXTEN}` to trigger engine binder to map the socket to the original caller channel.
  - Minimal `s` extension keeps the Local ;1 leg alive.

Current server snippet (working):

```
[from-ai-agent]
exten => s,1,NoOp(Handing call directly to Stasis for AI processing)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[ai-agent-media-fork]
exten => _X.,1,NoOp(Local channel starting AudioSocket for ${EXTEN})
 same => n,Answer()
 same => n,Set(AUDIOSOCKET_HOST=127.0.0.1)
 same => n,Set(AUDIOSOCKET_PORT=8090)
 same => n,Set(AUDIOSOCKET_UUID=${EXTEN})
 same => n,Set(AS_UUID_RAW=${SHELL(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null)})
 same => n,Set(AS_UUID=${TOUPPER(${FILTER(0-9A-Fa-f-,${AS_UUID_RAW})})})
 same => n,ExecIf($[${LEN(${AS_UUID})} != 36]?Set(AS_UUID=${TOUPPER(${FILTER(0-9A-Fa-f-,${SHELL(uuidgen 2>/dev/null)})})}))
 same => n,NoOp(AS_UUID=${AS_UUID} LEN=${LEN(${AS_UUID})})
 same => n,AudioSocket(${AS_UUID},${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT})
 same => n,Hangup()

; keep ;1 leg alive
exten => s,1,NoOp(Local)
 same => n,Wait(60)
 same => n,Hangup()
```

## Runtime Context — Quick Checks Before Each Test

- Health: `curl http://127.0.0.1:15000/health` → `ari_connected`, `audiosocket_listening`, `active_calls`, providers’ readiness.
- Engine logs (tail): `docker-compose logs -f ai-engine`
  - Expect: `AudioSocket server listening`, `AudioSocket connection bound to channel`, `Set provider upstream input mode ... pcm16_8k`.
  - First inbound chunks: `AudioSocket inbound chunk bytes=... first8=...`.
- Asterisk logs:
  - Confirm Local originate and `AudioSocket(UUID,127.0.0.1:8090)` (no parse errors).
  - No `getaddrinfo(..., "8090,ulaw")` errors — use host:port only.

## GA Track — At A Glance

- **Milestone 5**: Harden streaming transport, add telemetry, document tuning tips.
- **Milestone 6**: Implement OpenAI Realtime provider; verify codec negotiation and regression docs (`docs/regressions/openai-call-framework.md`).
- **Milestone 7**: Deliver configurable pipelines with hot reload; add pipeline examples and tests.
- **Milestone 8**: Provide monitoring stack and dashboards; document `make monitor-up` workflow.
- After these milestones, tag GA and update quick-start instructions.

## Common Commands

- Build & run locally (both services): `docker-compose up -d --build`
- Logs (engine): `docker-compose logs -f ai-engine`
- Logs (local models): `docker-compose logs -f local-ai-server`
- Containers: `docker-compose ps`
- Asterisk CLI (host): `asterisk -rvvvvv`

## Development Workflow

1) Edit code on `develop`.
2) `docker-compose restart ai-engine` for local code-only spikes (do not use on the server).
3) **Before touching the server**: commit + push to `develop`. Never rely on `scp` or manual edits; the server must `git pull` the exact commit you just pushed before any `docker-compose up --build` run.
4) Full rebuild only on dependency/image changes.
5) Keep `.env` out of git; configure providers via env and YAML.

## Testing Workflow

- Smoke test AudioSocket ingest:
  - Confirm: `AudioSocket server listening ...:8090` in engine logs.
  - Place a call into the AudioSocket + Stasis context, watch for:
    - `AudioSocket connection accepted` and `bound to channel` in logs.
    - Provider session started.
  - Verify file‑based playback (ensure sound URIs without file extensions).

## Observability & Troubleshooting

- Engine logs: ARI connection errors, AudioSocket binds, playback IDs.
- Known gotcha: Do not append `.ulaw` to `sound:` URIs (Asterisk adds extensions automatically).
- Metrics: hit `curl http://127.0.0.1:15000/metrics` after each regression to capture latency histograms and `ai_agent_last_*` gauges before recycling containers.
- Remote logs: from the local repo run `timestamp=$(date +%Y%m%d-%H%M%S); ssh root@voiprnd.nemtclouddispatch.com "cd /root/Asterisk-AI-Voice-Agent && docker-compose logs ai-engine --since 30m --no-color" > logs/ai-engine-voiprnd-$timestamp.log` to pull the most recent `ai-engine` output for RCA.

## IDE Hand-Off Notes

- **Codex CLI**: Follow this file plus `call-framework.md` for deployment + regression steps.
- **Cursor**: `.cursor/rules/asterisk_ai_voice_agent.mdc` mirrors the same guardrails for code edits; keep it updated when workflows change.
- **Windsurf**: `.windsurf/rules/asterisk_ai_voice_agent.md` references the roadmap; ensure milestone docs stay in sync so prompts remain accurate.
- **Shared history**: Document every regression in `docs/regressions/` so all IDEs inherit the same context without log-diving.

### GPT-5 Prompting Guidance

- **Precision & consistency**: Keep instructions aligned across `Agents.md`, `.cursor/…`, `.windsurf/…`, and `Gemini.md`; avoid conflicting language when updating prompts or workflow notes.
- **Structured prompts**: Wrap guidance in XML-style blocks when scripting Codex messaging, e.g.

  ```xml
  <code_editing_rules>
    <guiding_principles>
      - streaming transport stays AudioSocket-first with file fallback
    </guiding_principles>
    <tool_budget max_calls="6"/>
  </code_editing_rules>
  ```

- **Reasoning effort**: Request `high` effort for complex streaming/pipeline work; prefer medium/low for routine edits to avoid over-analysis.
- **Tone calibration**: Use collaborative wording instead of caps or forceful commands so GPT-5 balances initiative without overcorrecting.
- **Planning & self-reflection**: For zero-to-one changes, include a `<self_reflection>` block or explicit planning cue before execution.
- **Eagerness control**: Set exploration limits with tags such as `<persistence>` or explicit tool budgets; clarify when to assume-and-proceed versus re-asking.

Mirror any updates to this guidance in `.cursor/rules/asterisk_ai_voice_agent.mdc`, `.windsurf/rules/asterisk_ai_voice_agent.md`, and `Gemini.md`.

## Ports & Paths

- AudioSocket: TCP 8090 (default; configurable via `AUDIOSOCKET_PORT`).
- ARI: default 8088 HTTP/WS (from Asterisk).
- Shared media dir: `/mnt/asterisk_media/ai-generated/`.

## Deploy (Server) — Runbook

Assumptions: server `root@voiprnd.nemtclouddispatch.com`, repo at `/root/Asterisk-AI-Voice-Agent`, branch `develop`.

```
ssh root@voiprnd.nemtclouddispatch.com \
  'cd /root/Asterisk-AI-Voice-Agent && \
   git checkout develop && git pull && \
   docker-compose up -d --build ai-engine local-ai-server && \
   docker-compose ps && \
   docker-compose logs -n 100 ai-engine'
```

**Deployment rule**: the server must only run committed code. Before executing this runbook, ensure the local changes (e.g., `src/engine.py`, `config/ai-agent.yaml`) are committed and pushed so `git pull` brings them across.
Then place a test call. Expect:

- `AudioSocket connection accepted` → `bound to channel` → provider session → playback.
If no connection arrives in time, the engine will fall back to legacy snoop (logged warning).

## Acceptance (Current Release)

- Upstream audio via AudioSocket reaches provider (or snoop fallback).
- Downstream responses play via file‑based playback reliably.
- P95 response time ~≤ 2s under basic load; robust cleanup of temp audio files.

## Next Phase (Streaming TTS)

- Enable `downstream_mode=stream` (when implemented): full‑duplex streaming, barge‑in (<300ms cancel‑to‑listen), jitter buffer, keepalives, telemetry.
- Keep `file` path as fallback.

## What I Still Need From You

1) Server details to deploy:
   - SSH host/user, repo path (confirm `/root/Asterisk-Agent-Develop`).
   - Whether to rebuild both `ai-engine` and `local-ai-server`, or only `ai-engine`.
2) Asterisk specifics:
   - Confirmation that `app_audiosocket` is available and dialplan context is in place.
   - ARI user creds are correct and reachable from the container.
3) Environment:
   - `.env` on server with required secrets and `ASTERISK_HOST`.
4) Test plan:
   - Extension/DID to dial for the test call.
   - Preferred provider (`default_provider`); confirm local vs deepgram.

## Nice‑to‑Haves to Work Faster

- Health endpoint in ai‑engine (optional) exposing ARI, AudioSocket, provider status.
- A Makefile or npm scripts for common ops (build, logs, ps, deploy).
- A dev compose override for mapping ports explicitly if host networking isn’t used.
- Sample `.env.example` entries for ARI and providers reflecting production usage.
- Pre‑baked dialplan snippet files in `docs/snippets/` for quick copy/paste.

## Rollback Plan

- Switch `audio_transport=legacy` to re‑enable snoop capture.
- Revert `downstream_mode` to `file` (default).
- `git checkout` previous commit on develop and rebuild `ai-engine` if needed.

## Security Notes

- Keep API keys and ARI credentials strictly in `.env` (never commit them).
- Restrict AudioSocket listener to `127.0.0.1` when engine and Asterisk are co‑located; otherwise secure the path appropriately.
