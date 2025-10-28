# FreePBX Integration Guide (AudioSocket-First Architecture)

**Note:** This guide reflects the GA-track deployment. AudioSocket is the default upstream transport, with automatic fallback to file playback. ExternalMedia RTP remains available for legacy scenarios and troubleshooting but is no longer the primary path.

## 1. Overview

The Asterisk AI Voice Agent v3.0 integrates with FreePBX by combining ARI call control with an AudioSocket TCP listener hosted in the `ai-engine`. Each inbound call enters Stasis, the engine originates an AudioSocket leg, and `StreamingPlaybackManager` paces provider audio downstream while retaining tmpfs file playback as a fallback. ExternalMedia RTP can be preserved as an optional path when needed.

## 2. Prerequisites

- FreePBX installation with Asterisk 18+ (or FreePBX 15+) and ARI enabled.
- Docker and Docker Compose installed on the same host as FreePBX.
- Repository cloned (e.g., `/root/Asterisk-AI-Voice-Agent`).
- Port **8090/TCP** accessible for AudioSocket connections (plus 18080/UDP if retaining the legacy RTP path).
- Valid `.env` containing ARI credentials and provider API keys.

### 2.2 Create/Verify ARI User in FreePBX

You must have a non-readonly ARI user for the engine to control calls.

Steps (FreePBX UI):

1. Navigate to: `Settings → Asterisk REST Interface Users`.
2. Click `+ Add User` (or edit an existing one).
3. Set:
   - User Name: e.g., `AIAgent`
   - User Password: a strong password
   - Password Type: `Crypt` or `Plain Text`
   - Read Only: `No`
4. Save Changes and “Apply Config”.

Use these in your `.env`:

```env
ASTERISK_ARI_USERNAME=AIAgent
ASTERISK_ARI_PASSWORD=your-strong-password
```

Snapshot:

![FreePBX ARI User](freepbx/img/snapshot-3-ari-user.png)

### 2.1 Prerequisite checks

- Verify ARI and AudioSocket modules:

  ```bash
  asterisk -rx "module show like res_ari_applications"
  asterisk -rx "module show like app_audiosocket"
  ```

  Expect both to show Status: Running. If your Asterisk is <18, on FreePBX Distro use:

  ```bash
  asterisk-switch-version   # aka asterisk-version-switch
  ```

  and select Asterisk 18+.

  Example output:

  ```
  Module                         Description                               Use Count  Status   Support Level
  res_ari_applications.so        RESTful API module - Stasis application   0          Running  core
  1 modules loaded

  Module                         Description                               Use Count  Status   Support Level
  app_audiosocket.so             AudioSocket Application                    20         Running  extended
  1 modules loaded
  ```

## 3. Dialplan Configuration

### 3.0 Edit extensions_custom.conf via FreePBX UI

Use the built‑in editor to add the contexts below.

Steps:

- Navigate to: Admin → Config Edit.
- In the left tree, expand “Asterisk Custom Configuration Files”.
- Click `extensions_custom.conf`.
- Paste the contexts from the next section, Save, then click “Apply Config”.

Snapshot 1:

![Config Edit - extensions_custom.conf](freepbx/img/snapshot-1-config-edit.png)

### 3.1 AudioSocket Contexts

Append the following contexts to `extensions_custom.conf` (or the appropriate custom include). Each context can be targeted from a FreePBX Custom Destination or IVR option so you can exercise a specific provider pipeline during testing.

Note:

- The `AI_PROVIDER` value must match a pipeline name in your active `config/ai-agent.yaml`. Example names provided below exist in `config/ai-agent.yaml` or the example templates under `config/`.

#### 3.1.1 AI_PROVIDER name mapping (by template)

| Template you copied to `config/ai-agent.yaml` | Use these `AI_PROVIDER` values in dialplan | Where they come from |
| --- | --- | --- |
| `config/ai-agent.yaml` (default) | `local_only`, `hybrid_support`, `default` | Pipelines defined in `config/ai-agent.yaml` |
| `config/ai-agent.hybrid.yaml` | `hybrid` | `pipelines.hybrid` in that template |
| `config/ai-agent.cloud-openai.yaml` | `cloud_only_openai` | `pipelines.cloud_only_openai` in that template |
| `config/ai-agent.openai-agent.yaml` | Provider override: `openai` | Monolithic provider (no pipeline needed) |
| `config/ai-agent.deepgram-agent.yaml` | Provider override: `deepgram` (or `deepgram_agent`) | Monolithic provider (no pipeline needed) |

Provider overrides vs Pipelines:

- If you set `AI_PROVIDER` to a known provider alias/name (e.g., `openai`, `deepgram`), the engine uses that provider directly for this call.
- Otherwise, `AI_PROVIDER` is treated as a pipeline name and must exactly match `pipelines.<name>` in your active config.

Tip: For a full explanation of each option (barge-in, VAD, streaming, transports, providers) see `docs/Configuration-Reference.md`. For ready-to-use tuning presets, see `docs/Tuning-Recipes.md`.

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Handing call directly to AI engine (default provider))
 same => n,Set(AI_PROVIDER=local_only)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-custom]
exten => s,1,NoOp(Handing call to AI engine with hybrid pipeline override)
 same => n,Set(AI_PROVIDER=hybrid_support)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-deepgram]
exten => s,1,NoOp(Handing call to AI engine with Deepgram override)
 same => n,Set(AI_PROVIDER=deepgram)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-openai]
exten => s,1,NoOp(Handing call to AI engine with OpenAI pipeline)
 same => n,Set(AI_PROVIDER=default)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

exten => _X.,1,NoOp(Local channel starting AudioSocket for ${EXTEN})
 same => n,Answer()
 same => n,Set(AUDIOSOCKET_HOST=127.0.0.1)
 same => n,Set(AUDIOSOCKET_PORT=8090)
 same => n,Set(AUDIOSOCKET_UUID=${EXTEN})
 same => n,AudioSocket(${AUDIOSOCKET_UUID},${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT},ulaw)
 same => n,Hangup()

; keep ;1 leg alive while the engine streams audio
exten => s,1,NoOp(Local keepalive for AudioSocket leg)
 same => n,Wait(60)
 same => n,Hangup()
```

### 3.2 Create Custom Destinations

Create a FreePBX Custom Destination for each context you want to expose to IVRs or inbound routes.

Steps:

- Navigate to: Admin → Custom Destination.
- Click “Add” to create a new destination.
- Set Target to your dialplan entry, e.g.:
  - `from-ai-agent,s,1` (local pipeline)
  - `from-ai-agent-custom,s,1` (hybrid pipeline override)
  - `from-ai-agent-deepgram,s,1` (Deepgram agent)
  - `from-ai-agent-openai,s,1` (OpenAI pipeline)
- Give it a Description (e.g., “OpenAI Agent”).
- Submit and Apply Config.

Snapshot 2:

![Custom Destination - Target](freepbx/img/snapshot-2-custom-destination.png)

### 3.3 Example ai-agent.yaml excerpt

```yaml
asterisk:
  host: 127.0.0.1
  port: 8088
  username: asterisk-ai-voice-agent
  password: ${ASTERISK_ARI_PASSWORD}
  app_name: asterisk-ai-voice-agent

audiosocket:
  host: 0.0.0.0
  port: 8090
  format: ulaw

streaming:
  min_start_ms: 120
  low_watermark_ms: 80
  provider_grace_ms: 500
  jitter_buffer_ms: 160

barge_in:
  post_tts_end_protection_ms: 350

providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
    input_sample_rate_hz: 8000
  openai:
    api_key: ${OPENAI_API_KEY}
  local:
    enable_stt: true
    enable_llm: true
    enable_tts: true

pipelines:
  default:
    stt: openai_stt
    llm: openai_llm
    tts: openai_tts
    options: {}
active_pipeline: default
```

## 5. Deployment Workflow

```bash
# Start services (both ai-engine + local-ai-server)
docker-compose up -d

# Watch logs for AudioSocket listener and ARI binding
docker-compose logs -f ai-engine
```

For cloud-only deployments you may run `docker-compose up -d ai-engine`. Ensure logs show `AudioSocket server listening` and `Successfully connected to ARI` before testing calls.

### Media path quick check (required for file playback)

Verify Asterisk can see generated files:

```bash
ls -ld /var/lib/asterisk/sounds/ai-generated
ls -l  /var/lib/asterisk/sounds/ai-generated | head
```

If the directory is missing or empty while calls run, rerun `./install.sh` and accept the media path setup, or manually create the symlink:

```bash
sudo mkdir -p /mnt/asterisk_media/ai-generated /var/lib/asterisk/sounds
sudo ln -sfn /mnt/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated
```

## 6. Verification & Testing

1. **Health Check**

   ```bash
   curl http://127.0.0.1:15000/health
   ```

   Expect `audiosocket_listening: true`, `audio_transport: "audiosocket"`, and provider readiness.

2. **Test Call**
   - Place a call into the inbound route.
   - Confirm log events: `AudioSocket connection accepted`, `AudioSocket connection bound to channel`, provider greeting, streaming buffer depth messages, and `PlaybackFinished` cleanup.
   - Scrape `/metrics` to capture latency gauges (`ai_agent_turn_latency_seconds`, etc.) before stopping containers.

3. **Log Monitoring**

   ```bash
   docker-compose logs -f ai-engine
   docker-compose logs -f local-ai-server
   tail -f /var/log/asterisk/full

## 7. Troubleshooting

- **Call never binds to AudioSocket**: verify port 8090 reachability, ensure the Local originate made it into Asterisk logs, and confirm `AUDIOSOCKET_UUID` matches the EXTEN passed from the dialplan.
- **Frequent streaming fallbacks**: adjust `streaming.min_start_ms` (higher warm-up) or `low_watermark_ms` (higher threshold). Capture logs and `/metrics` snapshots for regression notes.
- **Provider-specific failures**: check API credentials in `.env`, ensure `default_provider` and `active_pipeline` align, and review provider logs for `invalid_request_error` or throttle messages.

## 8. GA Readiness Checklist (FreePBX)

Use this checklist alongside `docs/plan/ROADMAP.md` and the launch strategy under `docs/plan/` to prepare your deployment for GA:
{{ ... }}

```yaml
# Application
default_provider: openai_realtime
audio_transport: audiosocket
downstream_mode: file
Keeping these items up to date ensures your FreePBX deployment stays aligned with the broader GA readiness plan.
