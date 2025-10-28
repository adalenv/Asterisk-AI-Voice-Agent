# Call Framework Analysis — Deepgram Provider

## 2025-09-24 14:56 PDT — Two-way OK; residual self-echo; ARI cleanup errors gone

**Outcome**

- Two-way telephonic conversation is functional end-to-end.
- Occasional agent self-echo observed: Deepgram hears some of the agent audio and starts answering itself in a few follow-on turns.
- ARI cleanup errors (Bridge/Channel 404s) are gone; cleanup is idempotent.

**Key Evidence (this call)**

- Single bridge teardown per call; no error-level 404 logs for `DELETE /bridges/{id}` — when present, a DEBUG log appears: “Bridge destroy idempotent – already gone”.
- Channel hangup 404s, when they occur, log at DEBUG: “likely already hung up.”
- Exactly one “Disconnected from Deepgram Voice Agent.” info log at teardown (no duplicate disconnect logs).
- Despite `post_tts_end_protection_ms=350`, intermittent self-echo persists on some turns.

**Configuration Used (Highlights)**

- `audiosocket.format: slin16` (upstream PCM16 @ 8 kHz)
- `providers.deepgram.input_sample_rate_hz: 8000`
- `barge_in.initial_protection_ms: 400`
- `barge_in.post_tts_end_protection_ms: 350`
- `streaming.provider_grace_ms: 500`

**Diagnosis**

- Residual leakage of agent audio into the uplink just after TTS playback ends. The 350 ms post‑TTS guard reduces feedback but does not fully eliminate it across all trunks/turn shapes. Likely causes include late provider chunks arriving during or just after gating clear, brief bridge mixback, or small trunk echo.

**Next Tuning Options**

- Increase `barge_in.post_tts_end_protection_ms` from 350 → 400–500 ms (start with 400 ms) and retest.
- Sequence hardening: delay capture re‑enable until after `streaming.provider_grace_ms` in stream cleanup to absorb late provider frames.
- Add VAD confirmation just after playback ends: only re‑enable capture when VAD indicates sustained silence or inbound energy drops below threshold for N frames.
- Optional future: lightweight AEC evaluation if trunk echo remains a factor.

**Verification (next run)**

- Expect no error‑level ARI logs for idempotent operations; 404s remain at DEBUG only.
- Exactly one Deepgram disconnect info log on teardown.
- Fewer or no self‑echo incidents with `post_tts_end_protection_ms ≥ 400` and sequencing tightened.

---

## 2025-09-24 13:17 PDT — Two-way Conversation Stable; Echo-Loop Resolved

**Outcome**

- Two-way telephonic conversation is now acceptable end-to-end.
- No self-echo or loop observed in follow-on turns.

**What Changed Since Prior Run**

- Post‑TTS end protection window added in engine (`post_tts_end_protection_ms`) to drop inbound audio momentarily after TTS ends.
- Deepgram input sample rate aligned to 8 kHz to match AudioSocket upstream frames.

**Key Observations (Summary)**

- Gating toggles around TTS as expected; audio capture remains disabled during TTS, then re-enables post‑playback.
- Immediately after TTS ends, inbound frames within the configured protection window are dropped; no agent audio is re‑captured.
- Subsequent user speech is recognized normally and turns progress without runaway loops.

**Configuration Used (Highlights)**

- `audiosocket.format: slin16` (upstream PCM16 @ 8 kHz)
- `providers.deepgram.input_sample_rate_hz: 8000`
- `barge_in.initial_protection_ms: 400`
- `barge_in.post_tts_end_protection_ms: 350`
- `streaming.provider_grace_ms: 500`

**Next Tuning Options**

- If legitimate user barge-ins feel delayed: lower `post_tts_end_protection_ms` to 250–300 ms.
- If rare echo slips through on noisy trunks: increase to 400–500 ms.
- Optional sequencing hardening: clear gating after `provider_grace_ms` in streaming cleanup.

---

## 2025-09-24 12:47 PDT — Follow-on Echo Loop (Agent hears self)

**Outcome**

- Two-way audio now working; greeting clean.
- During follow-on turns, Deepgram began to hear its own TTS and loop.

**Key Evidence**

- Gating logic: capture is disabled while TTS plays and re-enabled immediately at playback end (`🔊 TTS GATING - Audio capture enabled`).
- Streaming path clears gating at stream cleanup before `provider_grace_ms` delay completes (`src/core/streaming_playback_manager.py::_cleanup_stream`). Late provider chunks may still egress to caller for up to `provider_grace_ms` after gating clears.
- With `audiosocket.format: slin16`, outbound μ-law from provider is converted to PCM16 for AudioSocket, and those trailing frames can be re-captured immediately after gating clears in the bridge mix.

**Diagnosis**

- Residual agent TTS frames (late provider chunks and bridge mix) arrive just after gating clears, so inbound capture resumes too early and forwards the agent’s own audio to Deepgram, causing a feedback loop.

**Change Implemented**

- Added a short post-TTS end guard window in engine to drop inbound audio right after gating clears:
  - `src/core/models.py`: add `tts_ended_ts` to `CallSession`.
  - `src/core/session_store.py`: stamp `tts_ended_ts` when last gating token is cleared.
  - `src/config.py`: add `barge_in.post_tts_end_protection_ms` (default 250 ms; env override supported).
  - `src/engine.py::_audiosocket_handle_audio`: drop inbound frames while `now - tts_ended_ts < post_tts_end_protection_ms`.
  - `config/ai-agent.yaml`: set `post_tts_end_protection_ms: 350`.

**Next Steps**

- Deploy and place a short call to validate no self-echo during handoffs between turns.
- Optional: move gating clear in `StreamingPlaybackManager._cleanup_stream()` to after `provider_grace_ms` sleep for stricter sequencing (current guard should already mitigate).
- Keep current barge-in thresholds; revisit only if genuine user barge-ins feel delayed.

---

## 2025-09-24 12:23 PDT — Greeting OK; Caller Audio Not Reaching Deepgram (8k→16k mismatch)

**Outcome**

- Initial greeting heard cleanly.
- No two-way conversation; caller audio not recognized/upstreamed by Deepgram.

**Key Evidence (call_id=1758741664.993)**

- Transport healthy and provider connected:
  - `AudioSocket server listening ...`
  - `✅ Successfully connected to Deepgram Voice Agent.`
  - `Deepgram agent configured. input_encoding=linear16 input_sample_rate=16000 output_encoding=mulaw output_sample_rate=8000`
- AudioSocket inbound confirmed at start of agent turn:
  - `AudioSocket inbound first audio bytes=320` (20 ms of PCM16 at 8 kHz)
- TTS protection window working as tuned (no early barge-in):
  - `Dropping inbound during initial TTS protection window ... protect_ms=400`
  - Later `Dropping inbound during TTS (candidate_ms=..., energy=...)` shows energy accumulating but not reaching `min_ms=400` — no `BARGE-IN triggered` in this run (as desired).
- Gating cleared at end of greeting, enabling capture:
  - `🔊 TTS GATING - Audio capture enabled (token removed) active_count=0 audio_capture_enabled=True`
- After gating clears, there are no explicit logs of forwarding (by design), but Deepgram remains configured at input_sample_rate=16000 while inbound frames are 8 kHz PCM16.

**Diagnosis**

- Audio sample rate mismatch: we forward 8 kHz PCM16 frames from AudioSocket (`format: slin16`), but Deepgram is configured to expect 16 kHz linear16 input. This mismatch likely prevents the Voice Agent from properly ingesting caller speech after the greeting, yielding “no conversation.”
- Barge-in guardrails are now working better (no spurious triggers during greeting), so the current blocker is upstream ingestion, not barge-in/gating.

**Remediation Options**

- Fast config alignment (recommended first):
  - Set `providers.deepgram.input_sample_rate_hz: 8000` to match AudioSocket (8 kHz PCM16) and redeploy.
- Robust engine-side fix (next):
  - Resample inbound AudioSocket audio 8 kHz → 16 kHz in `src/engine.py::_audiosocket_handle_audio` using `audioop.ratecv`, keyed by `conn_id` to preserve state, then continue to send 16 kHz PCM16 to Deepgram regardless of dialplan sample rate.

**Next Steps**

- Apply the fast config fix (8 kHz) and redeploy with `make deploy-safe`, then place a short call to confirm two-way audio.
- Implement engine resampling and richer decision logs:
  - Add 8k→16k resample when provider requires 16 kHz.
  - Log forwarded frame counts and effective sample rate at debug to accelerate future diagnosis.
- Continue VAD integration work for barge-in reliability after upstream audio is flowing.

---

## 2025-09-24 11:42 PDT — Greeting Only (server still on file); Switched to stream

**Outcome**

- Only initial greeting heard; no subsequent agent responses.

**Diagnosis**

- Server `config/ai-agent.yaml` was still on `downstream_mode: "file"` during this call, so live provider audio was not streamed over AudioSocket.

**Change Applied**

- Switched local config to `downstream_mode: "stream"` and ran `make deploy-safe`.
- Verified on server logs:
  - `Runtime modes  audio_transport=audiosocket downstream_mode=stream`
- `/ready` remained green post-restart.

**Next Verification**

- Place a new call; expect streaming logs from `StreamingPlaybackManager` and audible agent responses beyond the greeting.
- If issues persist, capture server logs immediately after the call and update this section with evidence (stream IDs, start/stop, pacing).

---

## 2025-09-24 11:12 PDT — Agent Cut‑offs; VAD + Barge‑in Evaluation

**Outcome**

- Audio pipeline intact; two-way conversation established, but agent responses frequently cut off.
- Conversation quality low; interruptions prevent coherent turns.

**Key Evidence (call_id=1758737312.949)**

- Transport and provider OK:
  - `AudioSocket server listening ...`
  - `✅ Successfully connected to Deepgram Voice Agent.`
  - `Deepgram agent configured. input_encoding=linear16 input_sample_rate=8000 output_encoding=mulaw output_sample_rate=8000`
- Inbound gating during TTS protection window observed repeatedly:
  - `Dropping inbound during initial TTS protection window protect_ms=200 tts_elapsed_ms=0`
  - This indicates the server is still running with a 200 ms protection window (YAML with 400 ms not yet live on server for this call).
- Missing barge‑in confirmations in the log:
  - No `🎧 BARGE-IN triggered` or `Playback stopped` entries present in this capture, suggesting barge‑in did not trigger during the excerpted window.

**What’s Implemented (Current State)**

- Engine barge‑in detection (in `src/engine.py::_audiosocket_handle_audio`):
  - Protection window after TTS start (`barge_in.initial_protection_ms` → target 400 ms).
  - Energy‑based detection using `audioop.rms(...)` per 20 ms frame.
  - Trigger when `candidate_ms ≥ barge_in.min_ms` (default 250 ms) and energy ≥ threshold (default 1000), with a cooldown.
  - On trigger: stop active playback via ARI `DELETE /playbacks/{id}`, clear gating tokens, forward inbound frames.
- Session/gating plumbing (`src/core/session_store.py`, `ConversationCoordinator`):
  - `audio_capture_enabled` toggled via TTS gating tokens.
  - Prometheus gauges/counters for gating and barge‑in attempts (ready for Milestone 8 charts).
- VAD: `py-webrtcvad` available, but NOT yet consulted by barge‑in logic (energy only for now).

**Diagnosis**

- The 200 ms protection window (still effective on server during this call) caused early inbound frames to be dropped, which the caller perceived as cut‑offs. The intended 400 ms setting hadn’t been applied on server for this run.
- Energy‑only barge‑in is brittle in telephony. Without VAD confirmation and with mixed downlink/uplink energy during playback, detection either under‑triggers (no barge‑in when user speaks softly) or over‑triggers (cuts playback due to leakage/noise). In this capture, no barge‑in triggers were logged; however, the perceived agent cut‑offs likely occurred later in the call when energy bursts crossed thresholds.

**Actions Taken**

- Local config updated to `barge_in.initial_protection_ms: 400` (see `config/ai-agent.yaml`).
- Engine restarted earlier, but this call still showed `protect_ms=200`; synchronize repo on server (git pull) before next call to ensure 400 ms is active.

**Next Steps (Plan to Achieve Smooth Full‑Duplex Turns)**

- Parameters and deployment
  - Ensure server uses latest YAML (pull repo, restart `ai-engine`).
  - Start with: `initial_protection_ms=400`, `min_ms=350–400`, `energy_threshold=1200–1500`, `cooldown_ms=1000`.
- Add VAD confirmation
  - Introduce `barge_in.use_vad: true` and require VAD=True for N consecutive 20 ms frames in addition to energy/time thresholds.
  - Keep VAD aggressiveness at 0 initially; tune if false positives persist.
- Improve barge‑in logging and metrics
  - Log candidate window, energy, thresholds, cooldown state, and explicit `BARGE-IN triggered` with playback IDs stopped.
  - Expose counters for successful barge‑ins and near‑misses (attempts) to `/metrics`.
- Optional echo mitigation (next sprint)
  - Gate with a short protection window, then rely on VAD+energy to trigger.
  - Evaluate lightweight AEC (SpeexDSP) to reduce downlink bleed that causes spurious energy.
- Conversation design
  - Keep agent prompts succinct; avoid long monologues.
  - If cut‑offs persist, slightly raise `min_ms` and/or `energy_threshold`.

**Verification on Next Call**

- Confirm logs show `protect_ms=400` in the protection window messages.
- If speaking over the first 0.3–0.5 s, expect no cut‑off; if user continues speaking ≥350 ms with VAD=True, expect `🎧 BARGE-IN triggered` and playback stopped.
- Check `/metrics` for barge‑in counters; confirm fewer interruptions and coherent back‑and‑forth turns.

---

## 2025-09-24 11:04 PDT — Barge-In Protection Window Tune (cut-offs; increased to 400 ms)

**Outcome**

- Two-way conversation working; initial cut-offs observed at start of agent turns.
- Increased barge-in protection window to 400 ms to reduce early downlink bleed and cut-offs.
- No barge-in trigger occurred during this run; inbound speech within protection window was dropped as designed.

**Key Evidence (call_id=1758703315.931)**

- `AudioSocket server listening ...` and ARI connected lines present.
- `Deepgram agent configured. input_encoding=linear16 input_sample_rate=8000 output_encoding=mulaw output_sample_rate=8000`
- Multiple lines of `Dropping inbound during initial TTS protection window protect_ms=200 tts_elapsed_ms=0` before the change.
- No `🎧 BARGE-IN triggered` lines in this call's logs.

**Diagnosis**

- Protection window of 200 ms was too short relative to agent playback onset and early uplink mix; caller’s first syllables landed during the protection period and were dropped, perceived as cut-offs.

**Change Applied**

- Set `barge_in.initial_protection_ms: 400` in `config/ai-agent.yaml` and restarted `ai-engine`.
- `/ready` remained green after restart.

**Next Verification**

- Place a call and attempt to speak over the first 0.3–0.5 s of agent playback.
- Expect reduced cut-offs and still no self-echo.
- If brief cut-offs persist, tune:
  - `barge_in.energy_threshold` (lower if under-triggering),
  - `barge_in.min_ms` (reduce to 200 ms),
  - or add VAD confirmation using `webrtcvad`.

---

## 2025-09-24 00:53 PDT — AudioSocket Deepgram Regression (8 kHz alignment; initial self‑echo)

**Outcome**

- Audio pipeline functional end-to-end. Two-way conversation achieved.
- Deepgram input aligned to 8 kHz linear PCM; audible quality improved significantly.
- Residual issue: Deepgram hears its own voice for the first few seconds on a turn (initial self-echo).

**Key Evidence (call_id=1758700412.883)**

- Provider session handshake and codec alignment:
  - `Connecting to Deepgram Voice Agent...`
  - `✅ Successfully connected to Deepgram Voice Agent.`
  - `Deepgram agent configured. input_encoding=linear16 input_sample_rate=8000 output_encoding=mulaw output_sample_rate=8000`
- AudioSocket bridge and media flow:
  - `AudioSocket server listening host=0.0.0.0 port=8090`
  - `AudioSocket connection accepted ...`
  - `AudioSocket UUID bound ...`
  - `🎯 HYBRID ARI - ✅ AudioSocket channel added to bridge`
  - `AudioSocket inbound first audio bytes=320` (20 ms @ 8 kHz slin16)
- File playback lifecycle (deterministic IDs) confirms greeting/response delivery:
  - `🔇 TTS GATING - Audio capture disabled (token added)`
  - `Bridge playback started with deterministic ID ... sound:ai-generated/...`
  - `🔊 AUDIO PLAYBACK - Started ...`
  - `🔊 PlaybackFinished - Audio playback completed ...`

**Diagnosis**

- The initial self-echo occurs because inbound AudioSocket audio is forwarded to the provider even while agent TTS playback is active on the bridge. Since AudioSocket is bridged with the caller, the downlink (agent audio) can be present on the uplink mix. Without gating on the AudioSocket inbound handler, Deepgram briefly hears its own audio at the start of a turn.

**Fix (engine, targeted)**

- In `src/engine.py::_audiosocket_handle_audio`, drop inbound frames when TTS gating is active for the call session (e.g., check `session.tts_active_count > 0` or `session.audio_capture_enabled == False` via `ConversationCoordinator/SessionStore`) before calling `provider.send_audio(...)`.
- Also deduplicate `PlaybackFinished` handler registration (register once in `Engine.start()`); repeated events can jitter gating and produce “unknown playback ID” warnings.

**Deepgram Provider Tuning (adopted and recommended)**

- Input (adopted now):
  - `providers.deepgram.input_encoding: linear16`
  - `providers.deepgram.input_sample_rate_hz: 8000`
- Output (file-playback path):
  - `output_encoding: mulaw`
  - `output_sample_rate_hz: 8000`
- Conversation stability (recommended):
  - Keep `continuous_input: true` on provider, but rely on engine-side gating to suppress capture during TTS playback windows.

**Jitter Buffer and Engine Timing (recommended starting points)**

- For current file-based playback (downstream_mode=`file`): jitter buffer has little impact on downlink, but keep Streaming config stable for future streaming tests:
  - `streaming.sample_rate: 8000`
  - `streaming.jitter_buffer_ms: 40` (range 40–60 ms is reasonable on host networking)
  - `streaming.chunk_size_ms: 20`
  - `streaming.min_start_ms: 120`
  - `streaming.low_watermark_ms: 80`
  - `streaming.fallback_timeout_ms: 4000`

**Next Verification**

- After gating change in `_audiosocket_handle_audio`, expect that Deepgram no longer hears its own voice at turn start. Logs should show the same codec alignment line with 8 kHz, stable playback lifecycle, and no duplicate `PlaybackFinished` warnings.

---

## ✅ REGRESSION PASS — September 22, 2025 (AudioSocket Two-Way Conversation)

**Outcome**

- AudioSocket-first Deepgram regression executed end-to-end with both caller and agent audio flowing cleanly through the engine.
- No playback backlog or gating stalls observed; capture re-enabled between turns and barge-in window reopened as expected.

**Highlights**

- Greeting reached the caller immediately via tmpfs file playback; upstream speech was relayed to Deepgram with stable chunk sizes (~320 B PCM-16/8 kHz).
- Deepgram returned transcripts for each caller turn, and responses were synthesized and delivered back over the existing file-based playback path without glitches.
- Health check after hangup reported `ari_connected=true`, `audiosocket_listening=true`, and `active_calls: 0`.
- Provider prompt/instructions now emphasise concise (<20 word) answers to reduce LLM and TTS processing time per turn.

**Latest Verification (2025-09-22 23:47 UTC-7)**

- Conversation turns completed in ≤1.8 s from speech end to playback, as captured by `ai_agent_turn_latency_seconds` histograms during the call.
- `ai_agent_last_turn_latency_seconds` and `ai_agent_last_transcription_latency_seconds` reset to 0 after cleanup, confirming coordinator metrics unwind correctly between sessions.
- Greeting playback now skips Deepgram TTS synthesis when the provider lacks `text_to_speech`, eliminating repeated `AttributeError` stack traces.

**Remaining Work**

- Capture Prometheus metrics immediately after regressions (before container restarts) so histogram buckets persist for trend analysis.
- Prepare for downstream streaming by wiring feature-flag guards and jitter buffer defaults without regressing the current file playback path.
- Cross-IDE note: mirror these findings into `.cursor/` and `.windsurf/` rules when behaviour changes so Cursor/Windsurf sessions inherit the same regression state.

---

## 2025-09-23 18:39 PDT — AudioSocket Deepgram Regression (multi-stream jitter)

**Observed Behaviour**

- 50-second call using the latest streaming fixes.
- No greeting was heard at call start; Deepgram jumped straight into follow-on responses.
- Two-way conversation proceeded but audio quality was intermittently garbled, especially at the start of each agent turn.

**Key Evidence (call_id=1758677818.841)**

- No greeting playback logged (`Deepgram AgentAudio first chunk bytes=960` appears without a preceding greeting entry).
- Streaming restarted repeatedly within the first response:
  - `🎵 STREAMING PLAYBACK - Started ... stream_id=stream:...:1758676582323`
  - Immediately followed by `🎵 STREAMING PLAYBACK - Stopped` and new `stream_id` values.
  - Multiple fallback entries (`timeout=2.0`) show the manager kept timing out and restarting.
- VAD never reset to silence: `consecutive_speech_frames` climbed past 1,900 even after playback ended, indicating the session stayed in “speaking” state.

**Diagnosis**

- **Streaming debounce incomplete**: Even though we reuse queues, `_handle_streaming_audio_chunk()` still kicked off new streams while the previous task was winding down. Each restart introduced jitter into the opening frames, causing the garble.
- **Fallback timeout too short**: The 2-second timeout triggered while we were refilling the jitter buffer, forcing a stream restart and bypassing the greeting.
- **VAD reset missing**: After playback ended we didn’t reset `session.vad_state`, so VAD assumed continuous speech and Deepgram kept talking without the initial greeting phase.

**Next Improvements**

- **Streaming stability**:
  - Guard `start_streaming_playback()` to reuse the existing task unless it has finished (implemented immediately after this regression).
  - Increase the fallback timeout to accommodate jitter warm-up (implemented).
- **VAD state reset**:
  - Explicitly reset VAD and fallback counters after streaming (`_reset_vad_after_playback()` added to the engine).
- **Retest** once these changes are deployed to confirm the greeting returns and stream IDs stay stable.

**Status**

- Partial success: audio and conversation flow work, but the repeated streaming restarts degrade quality. Fixes have been deployed to stabilize streaming and VAD before the next run.

---

## 2025-09-23 17:59 PDT — AudioSocket Deepgram Regression (channel interface; partial success)

**Observed Behaviour**

- 40-second test call with the new ARI-originated AudioSocket channel interface.
- Agent audio was audible; caller and agent exchanged two turns, but the first few seconds of playback sounded garbled before stabilizing.
- Conversation flow completed without crashes; cleanup released the AudioSocket TCP connection.

**Key Evidence (call_id=1758675462.831)**

- Channel interface path in action:
  - `AudioSocket connection accepted conn_id=0288eeda97ca4c298004ad510c255d8f`
  - `AudioSocket UUID bound ... uuid=7644fc76-70d9-46c3-9423-b4b1ad93152b`
  - `Deepgram AgentAudio first chunk bytes=960`
  - `🎵 STREAMING OUTBOUND - First frame ... frame_bytes=320 audiosocket_format=slin16`
- VAD captured caller speech continuously: multiple `🎤 AUDIO CAPTURE - ENABLED` and `🎤 VAD - Consecutive speech frames ... webrtc_decision=True` entries.
- Cleanup confirmed both the provider session and AudioSocket connection were closed cleanly.

**Diagnosis**

- **Successes**:
  - New channel interface flow bridged AudioSocket to the caller; outbound audio was heard without manual dialplan involvement.
  - Deepgram streaming handshake and codec configuration worked (`linear16` input / `mulaw` output, converted to slin16 on send).
  - Two-way audio running end-to-end verified the architecture change.
- **Quality issues**:
  - Early-response garble suggests the first few AudioSocket frames may still be slightly mis-timed or converted before the jitter buffer stabilises. There were several back-to-back `🎵 STREAMING STARTED` logs during the first response (multiple stream IDs within the same second), implying the engine restarted the streaming playback manager when new chunks arrived faster than expected.
  - VAD kept logging `consecutive_speech_frames` in the thousands before an utterance boundary, meaning we never triggered the speech-end condition quickly—likely why streaming restarts kept appearing (provider responses overlapped with long-running capture).

**Next Improvements**

- **Streaming stabilisation**:
  - Ensure only one streaming session per call is active at a time. Investigate why multiple `stream:streaming-response:*` IDs were started within the first response and adjust `_handle_streaming_audio_chunk()` to reuse the existing stream.
  - Add a small pre-stream delay or fade-in to the first 2–3 frames to mask jitter while Asterisk begins playback.
- **VAD tuning**:
  - After provider playback finishes, shorten the silence/end thresholds to avoid long stretches of `consecutive_speech_frames` that delay the next provider turn.
  - Consider resetting `session.streaming_audio_queue` at the start of each provider response to prevent leftover chunks from triggering extra restarts.
- **Monitoring**:
  - Capture `/mnt/asterisk_media` audio output for the first agent response to confirm whether the garble originates on the engine side or from Asterisk playback.
  - Add metrics/logs for average jitter-buffer depth and streaming restart counts to quantify improvements.

**Status**

- Partial success: AudioSocket channel interface works and audio is audible, but startup jitter and overlapping streaming sessions need smoothing before calling it production-ready.

---

## 2025-09-23 17:06 PDT — AudioSocket Deepgram Regression (pinned + broadcast; still silent)

**Observed Behaviour**

- Caller heard no audio despite both fixes enabled:
  - Outbound pinned to first bound AudioSocket connection
  - Broadcast debug on (frames sent to both conn_ids)
- Engine logs during the call:
  - `AudioSocket connection accepted conn_id=2a32acb4...`
  - `Deepgram agent configured. input_encoding=linear16 input_sample_rate=16000 output_encoding=mulaw output_sample_rate=8000`
  - `AudioSocket UUID bound conn_id=2a32acb4... uuid=8be64e2e-...`
  - `Deepgram AgentAudio first chunk bytes=960`
  - `🎵 STREAMING STARTED - Real-time audio streaming initiated ... stream_id=...7322`
  - `🎵 STREAMING OUTBOUND - First frame audiosocket_format=slin16 frame_bytes=320 conn_id=2a32acb4...`
  - `AudioSocket connection accepted conn_id=ec8fc00e...`
  - `🎵 STREAMING STARTED - Began on AudioSocket bind ... stream_id=...7383`
  - `AudioSocket UUID bound conn_id=ec8fc00e...`
  - `AudioSocket inbound first audio bytes=320 conn_id=ec8fc00e...` and also for `2a32acb4...`
  - `AudioSocket broadcast sent recipients=2 ...` repeated for ~3 seconds
  - `🎵 STREAMING PLAYBACK - End of stream`
- After streaming ended, VAD resumed and raised a separate bug:
  - `Error in VAD processing ... KeyError: 'silence_duration_ms'` (post‑stream, not cause of silence)

**What Worked**

- **Provider output**: Deepgram produced agent audio (`AgentAudio first chunk bytes=960`).
- **Codec alignment**: Outbound frames were slin16@8k, 320 bytes every 20 ms; inbound frames were also 320 bytes (slin16@8k).
- **Transport**: Multiple `AudioSocket broadcast sent recipients=2` entries confirm the engine sent frames to both Local ;1/;2 legs without send failures.

**What Failed (Likely Root Cause)**

- **Local channel bridge membership is missing.**
  - In working Hybrid ARI runs, we expect logs like:
    - `🎯 HYBRID ARI - Local channel entered Stasis`
    - `🎯 HYBRID ARI - Adding Local channel to bridge`
    - `🎯 HYBRID ARI - ✅ Local channel added to bridge`
  - For this call, those lines are absent. We only see caller bridge creation and caller added to the bridge, not the Local channel. If the Local channel executing `AudioSocket(...)` is not joined to the caller’s mixing bridge, Asterisk will not deliver the AudioSocket downlink frames to the caller—resulting in silence, even though the engine is streaming.

**Why We Believe This**

- With broadcast enabled, frames went to both sockets every 20 ms for several seconds—this rules out leg misselection and codec mismatch as primary causes.
- No Asterisk write/translate errors appeared; inbound frames were received on both connections; the only remaining gate is mix/bridge wiring for the Local leg producing playback.

**Next Diagnostic Checks (No Code Changes)**

- Verify Local channel bridge membership during a call:
  - Look for logs: `HYBRID ARI - Local channel entered Stasis` and `Local channel added to bridge`.
  - If absent, the Local ;1 leg likely never enters Stasis and therefore is never bridged.
- Inspect dialplan for `[ai-agent-media-fork]` currently used:
  - If it only runs `AudioSocket(...); Hangup()`, the Local channel will not enter Stasis. In prior successful runs, the Local ;1 path also hit Stasis so ARI could add it to the caller bridge.
- At call time, on Asterisk shell: `core show channels verbose` and check whether a `Local/...;1` is in the mixing bridge with the caller.
- Optionally tail `/var/log/asterisk/full` during the call for any `app_audiosocket` messages indicating downlink handling.

**Follow-ups (Separate Bug, not the cause of silence)**

- The VAD pipeline threw `KeyError: 'silence_duration_ms'` after streaming ended; ensure VAD state bootstraps all keys when capture re-enables. This does not affect downlink audio during streaming.

**Status**

- Outbound streaming from the engine is confirmed healthy (frames paced/broadcast). The remaining blocker is within Asterisk call wiring: ensuring the Local channel executing `AudioSocket(...)` is properly joined to the caller’s bridge so downlink frames are audible to the caller.

---

## 2025-09-23 16:05 PDT — AudioSocket Deepgram Regression (no audio; outbound first-frame logged)

**Observed Behaviour**

- Caller heard no audio. Engine logs show:
  - Deepgram session established and configured:
    - `Deepgram agent configured. input_encoding=linear16 input_sample_rate=16000 output_encoding=mulaw output_sample_rate=8000`
    - `Deepgram AgentAudio first chunk bytes=960`
  - AudioSocket duplicate legs connected and bound for the same UUID:
    - `AudioSocket connection accepted conn_id=f1777...`
    - `AudioSocket UUID bound conn_id=f1777...`
    - `AudioSocket connection accepted conn_id=b8cd7...`
    - `AudioSocket UUID bound conn_id=b8cd7...`
    - `AudioSocket inbound first audio bytes=320` for BOTH conn_ids (20 ms slin16@8k)
  - Outbound streaming first frame logged:
    - `🎵 STREAMING OUTBOUND - First frame audiosocket_format=slin16 frame_bytes=320 ... conn_id=f1777...`
  - TTS gating active for a few seconds, then:
    - `🎵 STREAMING PLAYBACK - End of stream` and cleanup

**Diagnosis**

- Codec alignment is correct (slin16@8k on AudioSocket wire; Deepgram output μ-law@8k converted to PCM16 by engine) and provider produced audio.
- Transport was healthy (no send failures, no Asterisk buffer/translate errors).
- Silence cause: Outbound likely streamed to the wrong AudioSocket leg after the second leg bound and sent the first inbound frame. Prior logic “selected primary on first inbound frame,” which can select the non-playback leg in Local ;1/;2 patterns. The first bound connection (f1777...) is typically the dialplan leg that should receive outbound audio.

**Fixes Implemented**

1. Pin outbound to the first bound connection
   - In `src/engine.py::_audiosocket_handle_uuid`, the first conn_id that binds is now persisted to `session.audiosocket_conn_id` and marked pinned for the duration of the call. We no longer switch targets on the first inbound frame.
   - Secondary legs remain open to avoid Asterisk EPIPE on write.
2. Broadcast debug option (for one-call diagnostics)
   - In `src/core/streaming_playback_manager.py`, added `audiosocket_broadcast_debug` flag to send each outbound frame to all tracked AudioSocket conn_ids in the session.
   - Enable via environment: `AUDIOSOCKET_BROADCAST_DEBUG=1`.
3. Codec/flow instrumentation (shipped earlier in the day)
   - Deepgram: logs codec in/out and first output chunk bytes.
   - AudioSocket server: logs first inbound audio frame bytes per conn.
   - Streaming manager: logs first outbound frame with conn_id, format and frame size.

**What to Verify Next Regression**

- Expect these logs early in the call:
  - `Deepgram agent configured. ... output_encoding=mulaw output_sample_rate=8000` (or linear16 if we switch)
  - `Deepgram AgentAudio first chunk bytes=...` (960 ≈ ~120 ms at 8k μ-law)
  - `AudioSocket UUID bound` for two conn_ids
  - `🎵 STREAMING OUTBOUND - First frame ... conn_id=<first-bound>`
  - If `AUDIOSOCKET_BROADCAST_DEBUG=1`: `AudioSocket broadcast sent recipients=2`
- Caller should hear audio. If broadcast mode yields audio while pinned mode does not, we will capture which conn_id produced audio and harden the mapping policy accordingly.

**Optional Codec Tuning**

- To eliminate μ-law conversion entirely, set Deepgram TTS output to linear PCM:
  - `providers.deepgram.output_encoding: "linear16"`
  - `providers.deepgram.output_sample_rate_hz: 8000`
  - Engine will then send slin16@8k frames without ulaw→lin conversion.

---

## 2025-09-23 15:17 PDT — AudioSocket Deepgram Regression (1-minute call, no audio)

**Observed Behaviour**

- No initial greeting, no audio for ~60s, normal hangup/cleanup.
- ai-engine logs show multiple Deepgram streaming bytes followed by a quick end:
  - `Received provider event event_type=AgentAudio` repeated many times (all at 22:14:55Z)
  - Then `Received provider event event_type=AgentAudioDone`
  - Streaming manager cleanup messages:
    - `🎵 STREAMING PLAYBACK - Stopped`
    - `🎵 STREAMING DONE - Real-time audio streaming completed`
  - After that, AudioSocket handshake lines appear for this call:
    - `AudioSocket connection accepted`
    - `AudioSocket duplicate connection received`
    - `AudioSocket connection bound to channel`
    - `AudioSocket UUID bound`
  - Notably absent: `AudioSocket first frame received` (no inbound audio frame observed by engine).

**What Worked**

- Deepgram provider produced `AgentAudio` bytes and signaled `AgentAudioDone`.
- Streaming manager engaged (gating set/cleared) and shut down cleanly.
- AudioSocket listener accepted and bound the UUID to the channel.

**What Failed and Why**

- No audible downstream audio:
  - Engine’s outbound AudioSocket streaming requires a valid `session.audiosocket_conn_id`.
  - We recently delayed primary selection until the first inbound audio frame to avoid closing the wrong duplicate leg. Because zero inbound frames were observed (`AudioSocket first frame received` never logged), the session never gained a valid `audiosocket_conn_id` during the streaming window. As a result, outbound frames had no target connection.
  - This explains silence despite `AgentAudio` arrivals and a clean streaming lifecycle.
- Inbound frames missing:
  - With AudioSocket dialplan app, Asterisk should send slin16 8 kHz to the server. The absence of inbound frames suggests either:
    - Dialplan Local pattern did not deliver media to the AudioSocket app (common timing race with Local ;1/;2), or
    - Asterisk write failures occurred again (not captured in this call’s excerpt), or
    - The server accepted connections after Deepgram already streamed and closed its short greeting window.

**Immediate Fix Plan**

1. Provisional conn selection on UUID bind:
   - Set `session.audiosocket_conn_id = conn_id` when the UUID binds (if none set) so outbound streaming has a target, then update to the true primary on the first inbound frame. Keep both ;1/;2 sockets open (no disconnections) to avoid Asterisk write errors.
2. Confirm slin16 end-to-end:
   - We now default `audiosocket.format=slin16`. Inbound path treats socket bytes as PCM16@8k; outbound converts Deepgram μ-law → PCM16@8k and sends 320-byte 20 ms frames with pacing.
3. Add start-of-call probe (optional):
   - Stream a 500 ms slin16 test tone on bind (behind a debug flag) to validate the downlink path independently of provider timing.

**Next Diagnostic Steps**

- Instrumentation to add (short-term):
  - Log when `Streaming transport missing AudioSocket connection` to confirm/deny the missing-conn hypothesis.
  - Log provisional vs primary `audiosocket_conn_id` assignment, including which conn_id receives outbound frames.
  - Count and log frames sent to AudioSocket during the first 2 seconds of streaming.
- Asterisk log capture:
  - Save `/var/log/asterisk/full` lines around the bind time to detect any `ast_audiosocket_send_frame` write errors on `;2`.
- Correlate timings:
  - Ensure UUID bind occurs before provider streaming begins; if not, delay provider start until AudioSocket bind completes or buffer the first N AgentAudio chunks until a conn_id is available.

## 2025-09-23 13:06 PDT — AudioSocket Deepgram Regression (Asterisk write failure)

**Observed Behaviour (25 s call)**

- No audio heard end-to-end.
- Asterisk logs:
  - `AudioSocket(UUID,127.0.0.1:8090)` (no codec arg shown)
  - `WARNING res_audiosocket.c: ast_audiosocket_send_frame: Failed to write data to AudioSocket`
  - `ERROR app_audiosocket.c: audiosocket_run: Failed to forward channel frame from Local/...;2 to AudioSocket`

**Analysis**

- The `;2` Local leg attempted to write media into the AudioSocket TCP, but the write failed (peer closed or not readable). Prior engine logic disconnected duplicate AudioSocket connections on UUID handshake or on first audio frame, which can leave Asterisk holding a socket it still tries to stream into (yielding the above errors).
- The dialplan invocation lacked an explicit format argument, so Asterisk likely used its default wire format (often `slin16`). The engine had been assuming μ-law by default, which risks mis-decode on inbound and wrong framing on outbound.

**What Worked**

- Engine accepted and bound AudioSocket connection(s); Deepgram provider session initialized; streaming manager engaged and closed cleanly when signaled.

**What Failed**

- Asterisk writes to AudioSocket failed shortly after connect (write error on `;2` leg).
- No audible downstream audio reached the caller.

**Fixes Implemented (in code)**

1. Keep both Local `;1/;2` AudioSocket connections open and select a primary on first inbound audio frame (do not disconnect the other leg). See `src/engine.py` (`channel_to_conns`, `audiosocket_primary_conn`).
2. Add configurable wire format `audiosocket.format` (`ulaw` default) and align outbound streaming:
   - μ-law: 160-byte 20 ms frames; PCM16: 320-byte 20 ms frames.
   - Real-time pacing on outbound to prevent Asterisk buffer overruns.
3. Decode inbound AudioSocket according to configured format (μ-law → PCM16 @8k) before 16 kHz resample for VAD.
4. Provider streaming events wired (`AgentAudio` with `streaming_chunk=true` + `AgentAudioDone`).

**Next Actions**

1. Redeploy latest engine (done) and retest the same DID.
2. Ensure dialplan explicitly specifies the format in `AudioSocket(UUID,host:port,ulaw)` (or set `AUDIOSOCKET_FORMAT=slin16` and use `slinear`).
3. During the call, watch for:
   - No `Failed to write data to AudioSocket` in `/var/log/asterisk/full`.
   - Engine logs: `AudioSocket first frame received`, `🎵 STREAMING PLAYBACK - Started`.
4. If still silent, capture 30 s of `ai-engine` logs and attach here; we will then adjust the Deepgram `AgentAudioDone` boundary detection to avoid prematurely stopping streams on mid-response JSON frames.

## 2025-09-23 12:55 PDT — AudioSocket Deepgram Regression (Asterisk buffer warnings)

**Observed Behaviour**

- No audio heard on the call; Asterisk logged repeated:
  - `WARNING translate.c: framein: Out of buffer space`
- Engine showed short-lived streaming windows:
  - `🎵 STREAMING PLAYBACK - Started` → `🎵 STREAMING PLAYBACK - Stopped` → `🎵 STREAMING DONE`
  - `AgentAudioDone with empty buffer`

**Diagnosis**

- Asterisk buffer overruns indicate outbound frames were sent too quickly and/or with the wrong size/format. Prior engine logic forwarded provider μ-law in variable sizes without pacing and expected PCM16 on the AudioSocket leg.
- Provider emitted frequent JSON control frames; our earlier logic treated any JSON as a stream-boundary and closed streaming too early, yielding near-zero audible output.

**Fixes Implemented (2025-09-23)**

- Wire format selection via config: `audiosocket.format` (`ulaw` default, or `slin16`).
- Outbound pacing and segmentation: exact 20 ms frames (160 B for μ-law, 320 B for PCM16) with real-time cadence.
- Inbound AudioSocket decode: μ-law → PCM16 @8k before resampling to 16 kHz for VAD.
- Provider streaming flags: Deepgram emits `AgentAudio` with `streaming_chunk=true` and `AgentAudioDone` with `streaming_done=true` (plus `call_id`).

**Expected Results Next Regression**

- Asterisk logs should no longer report `Out of buffer space`.
- Engine should show continuous streaming during agent replies, then clean `Streaming DONE` without empty buffer warnings.
- Caller should hear Deepgram greeting/response.

**Next Test Plan**

1. `make server-clear-logs` then place a call to the Deepgram AudioSocket context.
2. Watch `ai-engine` logs for: AudioSocket bind, `🎵 STREAMING PLAYBACK - Started`, steady per-frame pacing (no error bursts).
3. Confirm audible agent audio.
4. If still silent, verify dialplan third arg to `AudioSocket(...)` matches `audiosocket.format` and adjust (`ulaw` vs `slin16`).

## 2025-09-23 12:06 PDT — AudioSocket Deepgram Regression (No audio heard)

**Call Setup**

- Inbound route hit `from-ai-agent-deepgram` and entered `Stasis(asterisk-ai-voice-agent)`.
- Engine created caller bridge and originated the Local leg to `[ai-agent-media-fork]` to spin up AudioSocket.

**Observed Behaviour (server logs, ai-engine)**

- `AudioSocket connection accepted` followed by `AudioSocket connection bound to channel`.
- Immediately after bind: `AudioSocket provider session starting ... provider=local` (provider mismatch).
- A second Local leg attempted to connect; engine logged `AudioSocket duplicate connection received` and closed the extra socket (expected with Local ;1/;2 legs).
- ~14 seconds later, call cleanup ran; no greeting or playback logs emitted, and no `AudioSocket first frame received` appeared.

**Why No Audio Was Heard**

- Provider mismatch: the engine started the `local` provider even though the dialplan used `from-ai-agent-deepgram`. The engine does not currently read the `AI_PROVIDER` dialplan variable; it uses `config.default_provider` (currently `local`).
- AudioSocket path does not play a greeting in `_audiosocket_handle_uuid`. Unlike the ExternalMedia path (which calls `_play_initial_greeting_hybrid`/`playback_manager`), the AudioSocket bind only starts the provider session; no greeting is synthesized. With `local` selected and no explicit greeting call, the line stays silent.
- Streaming mode is still `downstream_mode: file` (per `config/ai-agent.yaml`), so even if Deepgram streamed, the engine would buffer chunks for file playback rather than live-stream back. This does not explain total silence by itself, but it confirms the streaming path is not engaged.
- Possible codec mismatch to verify: `[ai-agent-media-fork]` has historically used `AudioSocket(..., ulaw)` while the engine’s `_audiosocket_handle_audio` treats inbound frames as PCM16 (resampling with `audioop.ratecv(..., width=2)`). If the dialplan is actually using `ulaw`, inbound decode would be wrong and could lead to VAD seeing silence even if frames arrive. In this specific call, there were no `AudioSocket first frame received` logs, suggesting no audio frames surfaced at all (so greeting absence is the primary cause here).

**Evidence Snippets**

```
🎯 HYBRID ARI - StasisStart ... context=from-ai-agent-deepgram
AudioSocket connection accepted conn_id=...
AudioSocket connection bound to channel channel_id=...
AudioSocket provider session starting provider=local
AudioSocket duplicate connection received ... existing_conn=...
... ~14s later ...
Call resources cleaned up successfully channel_id=...
```

**Fix Plan**

1. Provider selection
   - Honor dialplan `AI_PROVIDER` by reading the ARI channel variable on `StasisStart` and persisting it into the `CallSession` (fallback to `config.default_provider` if missing).
   - As an immediate workaround for regression testing, set `default_provider: "deepgram"` in `config/ai-agent.yaml` when using the Deepgram route.

2. Greeting on AudioSocket path
   - In `_audiosocket_handle_uuid`, call the same greeting flow as ExternalMedia: synthesize via provider (or let Deepgram agent greeting handle it) and play via `PlaybackManager` to the bridge so callers hear sound on connect.

3. Streaming mode and encoding
   - Enable `downstream_mode: "stream"` for live-stream tests; otherwise the engine will buffer to file playback.
   - For AudioSocket downstream, convert Deepgram μ-law chunks to PCM16 8 kHz before `AudioSocketServer.send_audio(...)` (use `audioop.ulaw2lin(chunk, 2)`) so Asterisk hears correct audio.
   - Verify `[ai-agent-media-fork]` uses `slinear` if we keep PCM16 on the wire; if the dialplan uses `ulaw`, then send μ-law downstream and update inbound handling accordingly.

4. Duplicate AudioSocket handshakes
   - Current behavior (closing the second leg silently) is acceptable; ensure we do not send an Error TLV to avoid Asterisk reporting `non-audio` frames.

**Next Steps**

- Flip `default_provider` to `deepgram` or implement `AI_PROVIDER` override, redeploy, and place a new call.
- Confirm a greeting plays immediately after AudioSocket bind.
- If testing full-duplex, switch `downstream_mode=stream` and verify audible streamed audio; otherwise verify file playback path triggers on `AgentAudioDone`.

## ❌ REGRESSION BLOCKER — September 22, 2025 (Streaming playback loop / caller audio dropped)

**Outcome**

- Deepgram keeps talking in a loop; the caller never hears an opportunity to respond and any speech from the caller is ignored.

**Key Evidence**

- After the greeting, the engine immediately schedules ~100 micro-playbacks in succession (`🔊 AUDIO PLAYBACK - Started … audio_size=960`) and each one adds a gating token; active_count climbs into the 20s while `audio_capture_enabled=False` (`logs/server-ai-engine-call.log:324-900`).
- Every RTP frame arriving from the caller while Deepgram is speaking is discarded with `RTP audio dropped - capture disabled or TTS playing` (`logs/server-ai-engine-call.log:930-1130`).
- When the playback backlog finally drains, gating flips back to zero but by then Deepgram has already queued the next response, so capture never stays enabled long enough to stream caller audio; the conversation devolves into Deepgram talking to itself.
- Cleanup ends with `Cannot clear gating token - call not found` warnings because the session is destroyed before the final backlog finishes (`logs/server-ai-engine-call.log:19769-20130`).

**Why It Broke**

- Our file-based playback pipeline treats every `AgentAudio` chunk as a separate deterministic playback. Deepgram streams dozens of micro-segments per response, so the ConversationCoordinator keeps capture disabled almost continuously. Because WebRTC capture is gated during playback, caller audio is dropped for the entire response, preventing Deepgram from ever hearing the user.
- The refactor fixed the earlier `NameError`, so we now process these events, but the gating strategy is still incompatible with continuous streaming providers.

**Follow-up**

- ✅ **Fix applied (2025-09-22):** `AgentAudio` chunks now buffer in-session and flush on `AgentAudioDone`, producing a single playback per response so gating drops back to zero quickly.
- Allow continuous providers to receive caller audio even while TTS playback tokens are active (e.g., keep provider streaming but suppress VAD while agent speaks).
- Ensure cleanup drains any remaining playback references before removing the session to avoid the `Cannot clear gating token` warning storm.
- Re-test after revising gating so that logs show caller RTP frames processed (`audio_capture_enabled=True`) immediately after each response and Deepgram stops self-looping.

## ❌ REGRESSION BLOCKER — September 22, 2025 (AgentAudio handler NameError)

**Outcome**

- Deepgram streamed back-to-back responses, never un-gated capture, and caller speech was ignored.

**Key Evidence**

- `Error in provider event handler ... name 'call_data' is not defined` on every `AgentAudio` chunk (`logs/server-ai-engine-call.log:210-233`).
- TTS gating `active_count` climbs steadily (1→13) while `audio_capture_enabled=False`, so each RTP frame is dropped (`logs/server-ai-engine-call.log:147-339`, `73-130`).
- After hangup we spam `Cannot clear gating token - call not found` and `PlaybackFinished for unknown playback ID` because the session already cleaned up.

**Why It Broke**

- Refactor replaced dict-based `call_data` with `CallSession`, but the Deepgram `AgentAudio` path still referenced the old dict variable. The handler now raises before clearing provider timeouts or updating conversation state, so gating tokens never release.

**Follow-up**

- ✅ **Fix applied (2025-09-22):** `on_provider_event` now uses the `CallSession` object, cancels any pending provider timeout task, updates `session.conversation_state`, and persists the session (`src/engine.py:2368-2434`).
- Needs redeploy verification: expect to see a single `AgentAudio` playback per response, `audio_capture_enabled=True` once playback finishes, and no `NameError` / gating warnings on the next regression.

## ❌ REGRESSION BLOCKER — September 22, 2025 (VAD `frame_buffer` missing)

**Outcome**

- Deepgram greeting looped, but live caller audio never reached the provider; engine eventually fell back to dumping a 4 s buffer into the local provider.

**Key Evidence**

- `Error in VAD processing ... KeyError: 'frame_buffer'` repeats for every frame once capture re-enabled (`logs/ai-engine-latest.log:365-978`).
- With VAD blown up, the engine pushes a 4-second batch to the legacy pipeline instead of streaming (`logs/ai-engine-latest.log:989-990`).
- Local AI server, not Deepgram, handled TTS/STT for the entire call (`logs/local-ai-server-latest.log:62-76`).

**Why It Broke**

- When capture resumed after the greeting, the session’s VAD state lacked the `frame_buffer` key even though the guard existed, so `_process_rtp_audio_with_vad` crashed on every chunk. Because the coroutine raised before hitting `provider.send_audio`, upstream audio never left the engine, leaving Deepgram idle while playback manager kept serving greetings/responses.

**Follow-up**

- Harden the VAD state bootstrap: ensure `frame_buffer` (and the rest of the baseline keys) are restored whenever gating flips capture back on; emit a one-off log dumping `session.vad_state.keys()` the next time the guard fires.
- Confirm why Deepgram never emitted a handshake (`Connecting to Deepgram Voice Agent`) — we may still be short-circuiting to the local provider when greeting synthesis is requested. Re-run regression once the VAD guard is fixed and Deepgram session start is confirmed.
- ✅ **Fix applied (2025-09-22):** `src/engine.py` now rebuilds the default VAD state and patches missing keys whenever capture resumes via `_ensure_vad_state_keys`. Expect one-off warnings (`VAD state missing keys, patching defaults`) on the next call; if they reappear afterward, re-open this blocker.
- Next regression should show uninterrupted VAD processing (no `KeyError: 'frame_buffer'`) and downstream Deepgram events (`Connecting to Deepgram Voice Agent`, `AgentAudio`). Capture fresh logs after redeploy with `make server-logs SERVICE=ai-engine`.

## ❌ REGRESSION BLOCKER — September 22, 2025 (Typed Config Not Deployed)

**Outcome**

- No greeting or downstream audio played; call was torn down after a few seconds.

**Key Evidence**

- `Error starting provider session for ExternalMedia ... 'dict' object has no attribute 'api_key'` (`ai-engine` container, 20:23:00 UTC-7, channel_id=1758572574.571).
- Continuous `🎤 AUDIO CAPTURE - Check audio_capture_enabled=False` followed by `RTP audio dropped` while RTP frames streamed in.

**Why It Broke**

- The remote container still runs the pre-fix code path that hands a raw dict into `DeepgramProvider`, so accessing `.api_key` crashes provider startup before the greeting can synthesize.

**Follow-up**

- Rebuild and redeploy the ai-engine with the new `DeepgramProviderConfig` wiring, then rerun the regression call.
- After redeploy, verify logs show `Connecting to Deepgram Voice Agent...` and `Provider session started for ExternalMedia` before testing audio.

## ❌ REGRESSION BLOCKER — September 22, 2025 (Second Attempt, Same Crash)

**Outcome**

- Deepgram call again produced silence; provider startup failed before greeting playback.

**Key Evidence**

- `ExternalMedia channel mapped to caller ...`
- `Error starting provider session for ExternalMedia ... 'dict' object has no attribute 'api_key'` (`ai-engine`, 20:29:22 UTC-7, caller_channel_id=1758572957.574).
- Stack trace shows `DeepgramProvider.start_session` still receiving a dict instead of the typed config.

**Why It Broke**

- The running container still uses the old Deepgram implementation; redeploy has not occurred, so the bug persists.

**Follow-up**

- Force rebuild and redeploy the `ai-engine` image (`make deploy-force` or equivalent). Confirm the new container logs the typed-config validation line and the Deepgram WebSocket handshake before scheduling another regression.
- While the server is still tracking `develop` and our fixes live on a feature branch, copy the patched files directly onto `/root/Asterisk-Agent-Develop/src` before redeploying to confirm the change in place. Once audio is verified, merge the feature branch into `develop` and perform a clean `git pull` + `docker-compose up --build -d ai-engine` on the server to normalize.

## 🛠️ Verification Snapshot — September 22, 2025 (Post-Redeploy Sanity Check)

- Copied patched `src/config.py`, `src/engine.py`, and `src/providers/deepgram.py` directly onto `/root/Asterisk-Agent-Develop/src` and rebuilt `ai-engine`.
- Manual `docker exec ai_engine python -` probe confirmed `DeepgramProvider.config` resolves to `DeepgramProviderConfig` and a test `provider.start_session('test')` successfully established the Deepgram websocket (see logs for `Connecting to Deepgram Voice Agent...` followed by `✅ Successfully connected`).
- If a regression call still throws `'dict' object has no attribute "api_key"`, double-check timestamp—older log entries can appear after redeploy. Run the inline probe again to verify the container is serving the typed provider before the next call.

## ⚠️ PARTIAL SUCCESS — September 22, 2025 (Greeting Plays, Caller Audio Not Routed)

**Outcome**

- Deepgram greeted the caller and the call cleaned up cleanly, but no downstream response followed the user’s speech.

**Key Evidence**

- `🎤 AUDIO CAPTURE - ENABLED - Processing audio ... audio_capture_enabled=True` (caller audio reached the engine).
- No `Provider input` or Deepgram transcription events after the greeting; instead VAD reported silence (`webrtc_silence_frames=948`) and the call timed out.

**Hypothesis**

- Caller audio is flowing but VAD never sees a voiced frame, so the engine never sends chunks to Deepgram.

**Next Checks**

- Cross-check Deepgram websocket logs (look for `Transcription` events) and verify chunk forwarding in `_process_rtp_audio_with_vad` now that the typed config is live.

## INVESTIGATION — September 22, 2025 (VAD Not Detecting Speech)

**Outcome**

- After the greeting, the caller spoke but received no response. The call eventually timed out. The system was "listening" but not "hearing" any speech.

**Root Cause Analysis**

- The Voice Activity Detection (VAD) system failed to detect any voiced frames from the caller's RTP audio stream.
- The `_process_rtp_audio_with_vad` function was called, and `audio_capture_enabled` was `True`.
- However, the WebRTC VAD reported only silence (`webrtc_silence_frames=948`), so no audio was ever buffered or sent to the Deepgram provider for transcription.
- This is confirmed by the absence of `Provider input` or Deepgram `Transcription` events in the logs after the greeting.

**Architectural Context**

- As noted in `Architecture.md`, even though Deepgram supports continuous streaming, the conversation state (i.e., deciding when the user has finished speaking) is still driven by the engine's VAD.
- This VAD failure is the root cause of the one-way conversation.

**Next Steps & Recommended Fixes**

1. **Tune VAD Aggressiveness**: The `webrtc_aggressiveness` setting in `config/ai-agent.yaml` is likely too high for telephony audio. Lower it from `2` to `0` (least aggressive) to make it more sensitive.
2. **Verify Fallback Mechanism**: Ensure the `fallback_interval_ms` logic, which forces audio processing even when VAD is silent, is correctly engaging for the Deepgram provider path. The current VAD failure suggests it is not.
3. **Add Debug Logging**: Add temporary logging in `_process_rtp_audio_with_vad` to inspect the raw audio frames. This will confirm if the audio is corrupted/silent or if the VAD is simply misinterpreting it.

---

## Milestone 6 — Streaming Observability Checklist (In Progress)

- **What to verify**
  - Streaming path engages when `downstream_mode=stream` is enabled for Deepgram and provider emits streaming chunks.
  - `/health` exposes a `streaming` block with sensible values while the call is in flight.
  - Prometheus metrics reflect live streaming activity and fallbacks.

- **Quick commands**
  - Health:

    ```bash
    make test-health
    # or
    curl -sS ${HEALTH_URL:-http://127.0.0.1:15000/health} | jq .
    ```

  - Metrics:

    ```bash
    make test-metrics
    # or
    curl -sS ${METRICS_URL:-http://127.0.0.1:15000/metrics} \
      | egrep "ai_agent_streaming_|ai_agent_rtp_|ai_agent_(turn|transcription)_|ai_agent_audio_capture_enabled|ai_agent_tts_gating_active"
    ```

- **Metrics to watch during a streaming call**
  - `ai_agent_streaming_active{call_id}` should be `1` while audio is streaming.
  - `ai_agent_streaming_bytes_total{call_id}` should increase steadily.
  - `ai_agent_streaming_jitter_buffer_depth{call_id}` should stay within a few queued chunks (config-driven).
  - `ai_agent_streaming_last_chunk_age_seconds{call_id}` should remain low; spikes indicate provider stalls.
  - `ai_agent_streaming_keepalives_sent_total{call_id}` increments during the call.
  - `ai_agent_streaming_keepalive_timeouts_total{call_id}` increments only when the downstream path stalls.
  - When inducing stalls/silence, expect `ai_agent_streaming_fallbacks_total{call_id}` and `/health.streaming.fallbacks_total` to increment.

- **/health expectations (during call)**
  - `streaming.active_streams >= 1`
  - `streaming.ready_count` and `streaming.response_count` flip as provider events arrive.
  - `streaming.fallbacks_total` increases only when a fallback is triggered.
  - `streaming_details[]` includes per-call records: `call_id`, `provider`, `streaming_started`, `bytes_sent`, `fallbacks`, `last_error`.

- **Post-call checks**
  - `make test-health` → `active_calls: 0` and `streaming.active_streams: 0` once cleanup completes.
  - Streaming gauges (active, jitter depth, last chunk age) drop back to 0 shortly after cleanup.

- **Notes**
  - Maintain detailed Deepgram streaming logs in this file and reference this checklist from `call-framework.md` instead of duplicating content.

---

## 2025-09-22 - Milestone 6: Streaming TTS Implementation

**Implementation Summary**

- ✅ Extended `config/ai-agent.yaml` with streaming configuration parameters
- ✅ Created `StreamingPlaybackManager` for real-time audio streaming via AudioSocket/ExternalMedia
- ✅ Updated Deepgram provider to support incremental AgentAudio streaming with Ready/AgentResponse states
- ✅ Integrated streaming playback into engine with automatic fallback to file playback
- ✅ Added comprehensive unit tests for streaming functionality
- ✅ Documented dialplan contexts for testing streaming vs file-based playback

**Key Features Implemented**

1. **Streaming Configuration**: Added `streaming.*` knobs for sample rate, jitter buffer, keepalive, timeouts
2. **StreamingPlaybackManager**: Handles real-time audio chunk streaming with jitter buffering and keepalive
3. **Deepgram Streaming**: Enhanced provider to emit streaming events and handle incremental audio chunks
4. **Automatic Fallback**: Streaming automatically falls back to file playback on errors/timeouts
5. **ConversationCoordinator Integration**: Streaming respects gating rules and state management
6. **Dialplan Contexts**: `from-ai-agent` (local/file), `from-ai-agent-deepgram` (Deepgram/file), `ai-agent-media-fork` (AudioSocket binder reused for streaming tests)

**Configuration Changes**

```yaml
downstream_mode: "file"  # Default, can be set to "stream"
streaming:
  sample_rate: 8000
  jitter_buffer_ms: 50
  keepalive_interval_ms: 5000
  connection_timeout_ms: 10000
  fallback_timeout_ms: 2000
  chunk_size_ms: 20
```

**Testing Status**

- ✅ Unit tests created for `StreamingPlaybackManager` and engine integration
- ⚠️ Integration tests pending (requires Docker environment)
- ⚠️ Manual regression testing pending (requires server deployment)

**Expected Log Patterns**

- File-based: `🔊 TTS START - Response playback started via PlaybackManager`
- Streaming: `🎵 STREAMING STARTED - Real-time audio streaming initiated`
- Fallback: `🎵 STREAMING FALLBACK - Switched to file playback`

**Next Actions**

1. Deploy to test server and run integration tests
2. Test streaming mode via `[from-ai-agent-deepgram]` with `DOWNSTREAM_MODE=stream`
3. Verify fallback behaviour under network stress
4. Update this regression log with real streaming metrics once outbound transport is wired

---

## 2025-09-22 19:18 PDT — AudioSocket Deepgram Regression (from-ai-agent-deepgram)

**Call Setup**

- Dialed inbound route landed in `ivr-3`, jumped to the new context: `Goto("SIP/callcentricB12-00000067", "from-ai-agent-deepgram,s,1")`.
- Context sets `AI_PROVIDER=deepgram` and enters `Stasis(asterisk-ai-voice-agent)`; engine originated the usual `Local/<uuid>@ai-agent-media-fork` leg to spin up AudioSocket on port 8090.
- Config left at `downstream_mode=file` (streaming flag off) so downstream audio still flowed over file playback; upstream capture remained AudioSocket-first.

**Results**

- Greeting and subsequent Deepgram responses played cleanly with no gating backlog; `AudioSocket connection accepted` and `bound to channel` logged within ~200 ms of call start.
- Health check after hangup (`make server-health`) reported `active_calls: 0`, `streaming.active_streams: 0`, and both providers ready.
- Prometheus scrape (`make test-metrics`) showed new streaming gauges at zero (expected while downstream_mode=file) and populated RTP ingress counters for the call.
- Asterisk log snippet confirms context routing and no media errors were emitted.

**Artifacts Gathered**

- `docker-compose logs -n 200 ai-engine` captured AudioSocket bind, Deepgram transcription/response events, fallback counters staying at 0.
- `/metrics` snapshot stored under `logs/2025-09-22-deepgram-streaming-metrics.txt` (local) for latency comparison.

**Next Steps**

1. Enable `downstream_mode=stream` on the next regression to exercise the StreamingPlaybackManager path now that the control-plane is stable.
2. Capture `/metrics` mid-call looking for `ai_agent_streaming_*` gauges (expect active/fallback counters to tick once outbound streaming is wired).
3. Add a short README note on the server documenting which DID targets `from-ai-agent-deepgram` so on-call engineers can re-run the check.

---

## Streaming Regression Checklist — `downstream_mode=stream` (2025-09-22 19:52 PDT)

- **Prerequisites**
  - Set `downstream_mode=stream` (env `DOWNSTREAM_MODE=stream` or update `config/ai-agent.yaml`).
  - Confirm `make server-health` reports `streaming.active_streams: 0` and both providers ready.

- **Call Flow**
  1. Route a call through `from-ai-agent-deepgram`.
  2. During the call, watch `docker-compose logs -f ai-engine` for `🎵 STREAMING PLAYBACK - Started` and `RTP streaming send` entries.
  3. Run `make test-metrics` mid-call; expect `ai_agent_streaming_active{call_id}=1`, `ai_agent_streaming_bytes_total` increasing, and `ai_agent_streaming_fallbacks_total=0`.
  4. After hangup, `make server-health` should show `active_calls: 0`, `streaming.active_streams: 0`, and `streaming.last_error` cleared.

- **Troubleshooting**
  - If `ai_agent_streaming_fallbacks_total` increments, inspect `streaming.last_error` (e.g., `transport-failure` or `timeout`) and check the Asterisk RTP path. The engine will continue with file playback automatically.
  - Silence on the call usually indicates the ExternalMedia leg is missing or blocked; verify bridge membership and firewall rules for the RTP port.

- **Artifacts**
  - Metrics snapshot, `/health` JSON, and ai-engine logs archived alongside this entry (`logs/2025-09-22-streaming-regression/`).

## ✅ Regression Pass — 2025-09-22 20:38 PDT (Streaming Enabled)

- `DOWNSTREAM_MODE=stream` enabled; ai-engine logs show `🎵 STREAMING PLAYBACK - Started` and continuous RTP ingress/egress without triggering `STREAMING FALLBACK` entries.
- Live `/health` after hangup reported `total_frames_received: 1029`, `total_packet_loss: 0`, `active_streams: 0`.
- `/metrics` currently exposes only baseline gauges; capture mid-call metrics next run to confirm streaming counters emit once Prometheus wiring is extended.
- Next focus: add client-visible streaming metrics, tighten keepalive/reconnect handling, and exercise barge-in behaviour.

---

## 2025-09-23 00:26 PDT — AudioSocket Regression Failure (from-ai-agent-deepgram)

**Call Setup**

- Routed DID into `from-ai-agent-deepgram`, which set `AI_PROVIDER=deepgram` and entered `Stasis(asterisk-ai-voice-agent)`.
- Engine created the caller bridge and originated the Local leg (`Local/82c5869a-10d5-4ecb-96ab-92875c4fd856@ai-agent-media-fork/n`) exactly as in the previous AudioSocket flow.

**Observed Behaviour**

- Asterisk attempted to execute `AudioSocket(82c5869a-10d5-4ecb-96ab-92875c4fd856,127.0.0.1:18090)` and immediately logged `Connection refused` (`/var/log/asterisk/full`, 00:26:51). The Local legs tore down before any audio flowed.
- `docker-compose logs ai-engine` for the same window only shows the hybrid bridge set-up messages (`🎯 DIALPLAN EXTERNALMEDIA - ...`) followed by `Channel destroyed` events—there is no `AudioSocket connection accepted/bound` entry.
- Local AI server log confirms a websocket handshake from the engine, but no STT/LLM/TTS activity was triggered (`docker-compose logs local-ai-server`).

**Preliminary Root Cause**

- The dialplan is targeting `AUDIOSOCKET_PORT=18090`, but the ai-engine build does not expose a listener on that socket (there is no `AudioSocket server listening` log, and the container has no bound port). The prior regression used port 8090.
- Because the AudioSocket bind never happens, the engine never starts a provider session; the call ends after the Local channels hang up, producing silence on the line.

**Next Actions**

1. Align the dialplan port back to the engine’s configured AudioSocket listener (historically 8090) or expose the correct port from the engine if it has moved.
2. Confirm the engine is actually launching its AudioSocket server at container start—capture the `AudioSocket server listening` log (or add it back if it regressed) before placing the next call.
3. Re-run the regression once the TCP bind succeeds and verify `AudioSocket connection accepted` plus Deepgram transcription/response events return to the logs.

---

## 2025-09-23 01:01 PDT — AudioSocket Regression (engine listener enabled)

**Observed Behaviour**

- Ai-engine now reports `AudioSocket connection accepted` and binds the UUID from the first Local leg, but `/var/log/asterisk/full` immediately logs `res_audiosocket.c: Received non-audio AudioSocket message` and tears down the call.
- `docker-compose logs ai-engine` reveals a second AudioSocket connection from the complementary Local leg; the engine sends a `uuid-rejected` error TLV back, which Asterisk classifies as the non-audio frame.
- Local AI server still notes only the transient handshake and no STT/LLM traffic, confirming the media path never opens.

**Root Cause**

- The `[ai-agent-media-fork]` dialplan executes for both `Local/...;1` and `Local/...;2`. Each leg opens an AudioSocket connection with the same UUID. The engine treated any subsequent UUID handshake as an error and responded with an `Error` TLV (type `0xFF`), which Asterisk logged before dropping the bridge.

**Next Actions**

1. Permit duplicate UUID handshakes to close silently (or reuse the existing session) instead of emitting an `Error` frame so Asterisk does not abort the bridge.
2. Keep the UUID mapping in place until all legs have completed their handshakes, then proceed with provider processing once the primary connection remains.
3. Retry the regression after the duplicate-handshake guard is in place and ensure the first socket stays bound long enough for audio frames to reach the provider.

---
