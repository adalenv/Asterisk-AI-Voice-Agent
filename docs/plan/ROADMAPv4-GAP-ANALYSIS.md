# ROADMAPv4 Gap Analysis — Critical Review Before Implementation

This document identifies gaps, limitations, risks, and missing pieces in `docs/plan/ROADMAPv4.md` that must be addressed before starting implementation.

---

## Executive Summary

- **[verdict]** The plan is directionally sound but has **14 critical gaps** across testing, backward compatibility, provider-specific edge cases, config migration, Asterisk integration, and operational concerns.
- **[recommendation]** Address P0-critical gaps (1–7) before any code changes. Address P1 gaps (8–11) before P1 milestone. P2 gaps (12–14) can be deferred to later milestones or post-GA.

---

## Critical Gaps (P0 — Must fix before any code changes)

### Gap 1: No Testing Strategy or Regression Plan

- **[issue]** The roadmap doesn't specify how to validate that P0 changes don't break existing working baseline (μ-law@8k Deepgram).
- **[risk]** Breaking the working baseline while attempting to fix `linear16` would halt all production use.
- **[fix needed]**
  - Add a "Pre-Flight Validation" section:
    - Run full baseline regression with `telephony_ulaw_8k` profile before any P0 code changes.
    - Establish golden metrics from working baseline: underflows, drift, wall_seconds, RMS, clipping, SNR.
    - After P0 changes, re-run the same regression and compare metrics; all must match or improve.
  - Add acceptance criteria for each milestone that references specific test scenarios:
    - P0: μ-law@8k (baseline), linear16@16k (fix target), linear16@24k (hifi preview).
    - P1: Multi-provider parity tests (Deepgram + OpenAI).
  - Document regression protocol in `docs/plan/ROADMAPv4.md` or reference `docs/regressions/`.

### Gap 2: Backward Compatibility and Migration Not Fully Specified

- **[issue]** The plan mentions "feature flag" for profiles but doesn't specify:
  - What happens to existing `.yaml` configs without `profiles.*`?
  - How does the engine behave if a user sets `audiosocket.format: slin16` but doesn't provide a profile?
  - Can we guarantee zero-config-change for users happy with current μ-law setup?
- **[risk]** Breaking existing deployments; forced config rewrites during upgrade.
- **[fix needed]**
  - Define a default implicit profile: if `profiles.*` is absent, synthesize `telephony_ulaw_8k` from existing `audiosocket.format` + `providers.*.input/output_encoding`.
  - Add a config validator/migration script: `scripts/migrate_config_v4.py` that:
    - Reads legacy config and emits a `profiles.*` block.
    - Validates that existing knobs map cleanly to a profile.
    - Warns if deprecated knobs are present (e.g., `egress_swap_mode`).
  - Document the migration path explicitly: "Zero-config users see no change; advanced users can opt into profiles."

### Gap 3: No Rollback Plan if P0 Breaks Production

- **[issue]** Removing egress swap and provider swap logic is a one-way door. If it breaks, how do we recover?
- **[risk]** Live deployments fail; no quick revert path.
- **[fix needed]**
  - Keep deprecated knobs in code but issue loud warnings if used:
    - `egress_swap_mode` → log "DEPRECATED: This knob will be removed in vX.X. AudioSocket PCM is LE by spec."
    - `allow_output_autodetect` → same.
  - Tag a release `v3.x-pre-orchestrator` before P0 changes so operators can `git checkout` if needed.
  - Add a "Rollback Instructions" section to ROADMAPv4 or a separate `docs/plan/ROLLBACK.md`.

### Gap 4: Inbound (Caller → Provider) Path Not Addressed in P0

- **[issue]** P0 focuses on outbound (provider → caller) path. But the RCA showed that:
  - Inbound caller audio to Deepgram was healthy (strong RMS).
  - The issue was outbound only (provider PCM swap + pacer underflows).
  - However, the plan doesn't confirm that inbound remains untouched or if it needs orchestration too.
- **[risk]** Incomplete fix; inbound may have latent issues we're not addressing.
- **[fix needed]**
  - Explicitly state in P0 scope: "Inbound path (AudioSocket → provider input) is already stable; P0 focuses on outbound only."
  - OR: Add inbound orchestration to P1 if we want the Orchestrator to manage both directions.
  - Document the decision in ROADMAPv4.

### Gap 5: Provider Capability Discovery Protocol Undefined

- **[issue]** P1 says "expose ProviderCapabilities" but doesn't specify:
  - Is this a static declaration in each provider adapter (`supported_encodings = ["ulaw", "linear16"]`)?
  - Or runtime discovery (query the provider API)?
  - What if a provider doesn't advertise capabilities (e.g., older adapters)?
- **[risk]** Orchestrator can't negotiate if providers don't implement caps; calls fail.
- **[fix needed]**
  - Define a `ProviderCapabilities` dataclass in `src/providers/base.py`:

    ```python
    @dataclass
    class ProviderCapabilities:
        supported_input_encodings: List[str]
        supported_output_encodings: List[str]
        supported_sample_rates: List[int]
        preferred_chunk_ms: int = 20
        can_negotiate: bool = True  # if False, use static config only
    ```

  - Each provider adapter must implement `def get_capabilities() -> ProviderCapabilities`.
  - If a provider returns `can_negotiate: False`, the Orchestrator uses config values only and skips runtime ACK checks.
  - Document this contract in `docs/Architecture.md` and ROADMAPv4.

### Gap 6: No Handling of Mid-Call Format Changes or Re-Negotiation

- **[issue]** What if a provider ACK arrives late (after first audio chunk)?
- **[risk]** TransportProfile is locked at call start; late ACK is ignored, causing format mismatch.
- **[fix needed]**
  - P0/P1 scope: Lock TransportProfile at call start; ignore late ACKs (log a warning).
  - Future (post-GA): Add "renegotiation" support if provider sends a new ACK mid-call.
  - Document the limitation in ROADMAPv4: "Current scope: negotiate once at call start. Mid-call changes not supported."

### Gap 7: Asterisk Dialplan Update Not Specified

- **[issue]** The plan says "originate AudioSocket with correct `c(...)`" but doesn't specify:
  - Who generates the dialplan line? Is it engine-side or user-side?
  - How does the engine know which AudioSocket type to request from Asterisk?
  - Does the `audiosocket.format` config map directly to `c(slin)` vs `c(slin16)`?
- **[risk]** Mismatch between config and dialplan; wrong AudioSocket type selected.
- **[fix needed]**
  - Add a mapping table in ROADMAPv4 and `docs/Architecture.md`:

    ```
    audiosocket.format  →  Asterisk dial parameter  →  AudioSocket Type
    slin                →  c(slin)                   →  0x10 (PCM16@8k)
    slin16              →  c(slin16)                 →  0x12 (PCM16@16k)
    slin24              →  c(slin24)                 →  0x13 (PCM16@24k)
    ```

  - Engine must validate: if a profile requests `transport_out: pcm16@16k`, ensure `audiosocket.format == slin16`.
  - If mismatch, log an error and refuse to start the call OR auto-remediate (e.g., force profile to match wire).
  - Document this in "Asterisk-First Guardrails" section.

---

## High-Priority Gaps (P1 — Before Orchestrator implementation)

### Gap 8: DC-Block and Bias Removal Not Mentioned in Orchestrator

- **[issue]** The working baseline applies DC bias removal + DC-block filter on inbound caller audio (see `docs/rca-working-baseline-20251023-022434/WORKING_BASELINE_DOCUMENTATION.md` lines 287–291). The Orchestrator plan doesn't mention this.
- **[risk]** If we refactor inbound path, we might lose these stability filters.
- **[fix needed]**
  - Explicitly state in P1 scope: "DC bias removal and DC-block filter remain in the inbound path; Orchestrator doesn't touch this."
  - OR: Move DC-block into a reusable preprocessing stage that Orchestrator manages.

### Gap 9: Attack Envelope, Normalizer, Limiter — Removal vs Internal Diagnostic

- **[issue]** P2 says "keep internal only" but doesn't define what "internal" means:
  - Hidden behind a debug flag?
  - Removed from config schema but still in code?
  - Fully deleted?
- **[risk]** Ambiguity leads to incomplete cleanup.
- **[fix needed]**
  - P2 scope: Remove from user-facing config schema; keep in code behind env var `DIAG_ENABLE_AUDIO_PROCESSING=true`.
  - Log loudly if env var is set: "WARNING: Using diagnostic audio processing. Not for production."
  - Document this in ROADMAPv4 P2.

### Gap 10: Segment Summaries and Metrics Schema Not Defined

- **[issue]** Observability section mentions "underflows, drift_pct, buffer depth histogram" but doesn't specify:
  - Where are these logged? (structlog, Prometheus, both?)
  - What's the schema?
  - Are they per-segment or per-call?
- **[risk]** Metrics don't align with RCA needs; hard to correlate logs during troubleshooting.
- **[fix needed]**
  - Define a schema in ROADMAPv4 or `docs/Architecture.md`:

    ```json
    {
      "event": "Streaming segment bytes summary v3",
      "call_id": "...",
      "stream_id": "...",
      "provider_bytes": 64000,
      "tx_bytes": 64000,
      "frames_sent": 100,
      "underflow_events": 0,
      "drift_pct": 0.0,
      "wall_seconds": 2.0,
      "buffer_depth_hist": {"0-20ms": 5, "20-80ms": 90, "80-120ms": 5},
      "idle_cutoff_triggered": false,
      "chunk_reframe_count": 3
    }
    ```

  - Emit this once per segment (after `AgentAudioDone` or idle cutoff).
  - Prometheus counters: `ai_agent_underflow_events_total`, `ai_agent_drift_pct`, etc.

### Gap 11: OpenAI Realtime vs Deepgram Voice Agent — Different Negotiation Flows

- **[issue]** Deepgram sends a `SettingsApplied` ACK early; OpenAI Realtime uses `session.update` with a different schema. The Orchestrator must handle both.
- **[risk]** Hard-coding Deepgram ACK parsing breaks OpenAI.
- **[fix needed]**
  - Each provider adapter must implement a `parse_ack(event_data) -> Optional[ProviderCapabilities]` method.
  - Orchestrator calls `provider.parse_ack(...)` for each provider event and updates TransportProfile if needed (within the "lock at start" constraint).
  - Document provider-specific ACK schemas in `docs/providers/deepgram.md`, `docs/providers/openai.md`.

---

## Medium-Priority Gaps (P2 — Before CLI ships)

### Gap 12: `agent demo` — No Reference Audio Defined

- **[issue]** P2 says "`agent demo` plays reference audio" but doesn't specify:
  - What reference audio? A pre-recorded PCM16 file?
  - Where does it live?
  - How do we validate it's "clean" (no garble)?
- **[risk]** Demo is useless if reference audio is itself distorted.
- **[fix needed]**
  - Ship a known-good reference audio file: `tests/fixtures/reference_tone_8khz.wav` (1 kHz sine wave @ 8k, 2 s, PCM16).
  - `agent demo` plays this over AudioSocket loopback and measures RMS/clipping/SNR.
  - Acceptance: RMS within 10% of source; no clipping; SNR > 60 dB.
  - Document in `scripts/agent_demo.py` and ROADMAPv4.

### Gap 13: `agent doctor` — No Checklist of What to Validate

- **[issue]** P2 says "validates ARI, app_audiosocket, ports, provider keys" but doesn't provide a comprehensive checklist.
- **[risk]** Incomplete validation; users miss critical issues.
- **[fix needed]**
  - Define a full checklist in ROADMAPv4 or `scripts/agent_doctor.py`:
    - ✅ ARI accessible (`GET /ari/asterisk/info`)
    - ✅ `app_audiosocket` loaded (`module show like audiosocket`)
    - ✅ AudioSocket port available (`nc -zv 127.0.0.1 8090`)
    - ✅ Dialplan context exists (`dialplan show from-ai-agent`)
    - ✅ Provider keys present in `.env` (`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`)
    - ✅ Provider endpoints reachable (HTTP ping to Deepgram/OpenAI)
    - ✅ Shared media directory writable (`/mnt/asterisk_media/ai-generated`)
    - ✅ Docker network connectivity (if containerized)
  - `agent doctor` should print ✅ or ❌ for each item with a fix-up suggestion if failing.

### Gap 14: Config Schema Versioning Not Addressed

- **[issue]** Adding `profiles.*` changes the YAML schema. How do we track schema versions?
- **[risk]** Users mix old and new configs; hard to debug.
- **[fix needed]**
  - Add a `config_version: 4` field to `config/ai-agent.yaml`.
  - Engine validates on load: if `config_version < 4` and `profiles.*` is missing, auto-migrate OR refuse to start.
  - Document in ROADMAPv4 and Architecture.

---

## Low-Priority Gaps (P3 or Post-GA)

### Gap 15: No A/B Testing Framework for Profiles

- **[issue]** How do operators compare `telephony_ulaw_8k` vs `wideband_pcm_16k` objectively?
- **[fix]** Post-GA: Add a `scripts/profile_compare.py` that runs parallel calls with different profiles and emits side-by-side metrics.

### Gap 16: No Multi-Language / Multi-Locale Support in Profiles

- **[issue]** Profiles don't specify language/locale (e.g., `en-US` vs `es-MX`).
- **[fix]** Post-GA: Add `locale` field to profiles; pass to provider adapters.

### Gap 17: No Echo Cancellation or Noise Suppression in Orchestrator

- **[issue]** Working baseline doesn't mention AEC/NS; Orchestrator plan doesn't either.
- **[fix]** Post-GA: Add optional preprocessing stages (AEC, NS, AGC) as profile options.

---

## Dependencies and Prerequisites (Before P0 Start)

- **[dep-1]** Baseline regression must pass with current code (no changes).
- **[dep-2]** RCA artifacts from latest `linear16` failure must be archived and referenced.
- **[dep-3]** A reference "clean audio" test file must exist in `tests/fixtures/`.
- **[dep-4]** `docs/Architecture.md` and `docs/AudioSocket with Asterisk_ Technical Summary for A.md` must be up to date (done).

---

## Recommended Actions (Prioritized)

- **[immediate]** Add Gap 1 (testing strategy) to ROADMAPv4 before any code changes.
- **[P0-pre]** Address Gaps 2, 3, 4, 5, 6, 7 in ROADMAPv4 and code scaffolding.
- **[P1-pre]** Address Gaps 8, 9, 10, 11 in Orchestrator design doc.
- **[P2-pre]** Address Gaps 12, 13, 14 in CLI implementation spec.
- **[deferred]** Gaps 15, 16, 17 are post-GA enhancements; document in a "Future Work" section.

---

## Risk Assessment Summary

- **[high risk]** Gaps 1, 2, 3, 7 — can break production or prevent rollback.
- **[medium risk]** Gaps 4, 5, 6, 8, 9, 10, 11 — can cause subtle failures or incomplete fixes.
- **[low risk]** Gaps 12, 13, 14 — UX/tooling only; don't affect core audio.

---

## Conclusion

The ROADMAPv4 plan is ambitious and well-structured, but **must not proceed to implementation** until Gaps 1–7 are explicitly addressed in the roadmap document and scaffolding code. Gaps 8–14 should be resolved during their respective milestones. Gaps 15–17 can be deferred.

**Next Step**: Update `docs/plan/ROADMAPv4.md` with the fixes for Gaps 1–7, then seek approval before any code changes.
