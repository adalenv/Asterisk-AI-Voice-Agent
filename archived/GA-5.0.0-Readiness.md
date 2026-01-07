# GA 5.0.0 Readiness (Working Doc)

**Last GA:** `4.6.0` (note: tag naming is inconsistent historically)  
**Target tag:** `v5.0.0` (3-part)  
**Release positioning:** **GA release** with one major feature flagged as **Alpha** (Outbound Campaign Dialer).  
**Alpha scope (confirmed):** validated end-to-end at **1 call at a time** (campaign `max_concurrent=1`).  
**Scale note:** UI allows higher `max_concurrent` values, but anything > `1` is **not validated for GA** yet and should be treated as experimental.  
**Release flow (confirmed):** prep changes on `develop` → maintainer review → PR/merge → `main` → tag `v5.0.0`.

This is a planning + readiness artifact for maintainers and contributors. It is not a release sign-off.

---

## 1) Release Scope Summary (5.0.0)

### Core themes

- **Outbound Campaign Dialer (Alpha)** (Milestone 22):
  - Engine-driven scheduler + SQLite state (reuses Call History DB)
  - Admin UI “Call Scheduling” management UX (campaigns/leads/outcomes + inline call history)
  - Dialplan-assisted AMD (`AMD()`) with voicemail drop
  - Optional **consent gate** (DTMF `1` accept / `2` deny; capture consent outcomes)
  - Recording library (upload once, reuse across campaigns) + shipped default consent/voicemail prompts
  - **Alpha testing constraint:** validated with `max_concurrent=1` (one active outbound attempt at a time)
  - **Routing assumption:** outbound dialing uses whatever routes FreePBX/Asterisk already applies for **your configured outbound identity extension** (AAVA virtual extension), and trunk registration/route patterns are out-of-scope for the project.
- **Groq Speech (STT + TTS) modular pipeline adapters** (Milestone 15):
  - `groq_stt` + `groq_tts` components for cloud-only modular pipelines
  - Example pipeline + docs, plus Admin UI support for modular configs
- **Ollama improvements**:
  - Pipeline robustness, segment gating, minimal-mode hardening
  - UX fixes in Admin UI for Ollama pipeline config overrides
- **Telephony tools improvements**:
  - Attended (warm) transfer tool with DTMF acceptance flow
  - Dedupe + rate limiting for Resend-backed email tools (summary/transcript)
- **Operational & DX consistency**:
  - Standardized compose/container naming convention: `admin_ui`, `ai_engine`, `local_ai_server`
  - Pinned base images / cross-platform robustness improvements:
    - `admin_ui`: Python `3.11` on Debian `bookworm` (pinned to avoid drift)
    - `ai_engine`: Python `3.11` on Debian `bookworm`
    - `local_ai_server`: Python `3.11` on Debian `trixie` (intentional; needed for embedded Kroko glibc compatibility)

### Explicit non-goals (call these out for v5.0.0 messaging)

- **Not a call center suite**: no predictive dialer, agent seats, pacing algorithms, abandonment control, QA scoring.
- **No automatic retry automation in v1**: retries are **manual** (Recycle / Reset lead). Future roadmap may add max-attempts + backoff.
- **No compliance automation** in v1: DNC, call windows by jurisdiction, consent law handling beyond the optional consent gate.
- **No guarantees on AMD accuracy** across all carriers/regions/languages (AMD is heuristic and must be tuned).
- **No multi-node**: single host only.

---

## 2) Documentation Changes (GA Checklist)

### Blocking (must ship with v5.0.0)

- `CHANGELOG.md`
  - Add `5.0.0` entry (Outbound Dialer marked Alpha, plus Groq/Ollama/transfer highlights).
  - Ensure `[Unreleased]` compare links are based on `v5.0.0`.
- `README.md`
  - Bump version badge to `5.0.0`.
  - Update “What’s New” section to v5.0.0 highlights (clear Alpha labeling for outbound).
- `docs/OUTBOUND_CALLING.md`
  - Dedicated doc for the outbound dialer (setup + testing + troubleshooting).
- `docs/FreePBX-Integration-Guide.md`
  - Reference outbound calling doc in the canonical “first successful call” flow.
- `.env.example`
  - Document outbound dialer env vars and constraints.
  - Include notes on the recording media dir + size limits.

### Recommended (GA polish)

- `docs/INSTALLATION.md`: update upgrade section (`v4.6.0 → v5.0.0`) and ensure commands match current compose naming.
- `docs/Transport-Mode-Compatibility.md`: update to `v5.0.0+` where versioned.
- `docs/MONITORING_GUIDE.md`: update version wording where needed.
- `docs/contributing/milestones/milestone-15-groq-speech-pipelines.md`: ensure milestone header is correct (“Milestone 15”).

---

## 3) GA-Level Community Issues (5.0.0)

### Issue A (intentional): Outbound Dialer is Alpha

**Decision:** ship as Alpha, but with a strong “known-good” path and a tested smoke test.

**Expected friction points (document + UI guardrails):**
- Requires **Asterisk dialplan** snippet to be installed/reloaded.
- Requires outbound trunks/routes to be correct (project intentionally does not manage trunk config).
- Requires the outbound identity extension to match your routing expectations (`AAVA_OUTBOUND_EXTENSION_IDENTITY`, default `6789`).
- Alpha is validated at `max_concurrent=1` only; higher values are allowed but should be treated as experimental until expanded testing exists.

**Success criteria for Alpha:** users can run a first campaign and observe outcomes reliably (human / voicemail / consent) without reading source code.

### Issue B (GA concern): AMD false positives / tuning

**Observed behavior:** AMD can misclassify HUMAN vs MACHINE depending on greeting patterns, silence, carrier audio, and locale.

**Mitigation (v5.0.0):**
- Expose AMD settings per campaign (UI “Advanced AMD”).
- Default “NOTSURE → MACHINE” to conserve AI sessions.
- Document tuning recommendations and a test procedure (extension test + cell test).

### Issue C (GA concern): SQLite permissions + WAL/SHM with multi-container mounts

**Failure mode:** WAL sidecar files can be created with restrictive permissions, leading to `sqlite3.OperationalError: attempt to write a readonly database`.

**Mitigation:** preflight + Admin UI boot hardening to keep DB + WAL/SHM group-writable; keep all DB writes non-blocking for the real-time loop.

### Issue D (GA concern): Container/service naming consistency

**Decision:** standardize on:
- Compose service names: `admin_ui`, `ai_engine`, `local_ai_server`
- Container names: `admin_ui`, `ai_engine`, `local_ai_server`

**Mitigation:** update docs/examples/scripts to use the underscore naming consistently; call out that older blog posts may use `ai-engine` etc.

### Issue E: Email tools rate limits / duplicates

**Failure mode:** third-party email APIs can rate limit (429), and retries can double-send without dedupe.

**Mitigation in 5.0.0:** add bounded retries + dedupe protections for transcript/summary emails.

---

## 4) Go / No-Go Criteria (5.0.0)

### No-Go (blocking)

- Docs “Blocking” list not complete.
- CI not green for the chosen release head.
- At least one successful golden-baseline call per claimed baseline (per `docs/RELEASE_CHECKLIST.md`).
- Outbound dialer smoke tests fail on a reference FreePBX system:
  - HUMAN path attaches AI with the correct context/provider
  - Voicemail path leaves voicemail drop media and does not connect AI
  - Consent gate outcomes are captured (`accepted`, `denied`, `timeout`)
  - UI reflects correct outcomes and shows call history link/modal
- Compose/service naming inconsistency remains (docs or scripts still reference old names without a note).
- Outbound dialer docs do not clearly state the Alpha limits (single call at a time + operator-managed routing requirements).

### Go (minimum)

- Release head chosen on `main` and reviewed.
- Changelog + docs are consistent with tag naming (`v5.0.0`).
- Golden baseline calls recorded (minimum evidence captured: host/Asterisk/provider/transport, plus logs or call history snapshot).
- Outbound alpha smoke tests executed and recorded.

---

## 5) Execution Plan (proposed)

1. **Stabilize `develop`** (no pending regressions; sanity tests pass).
2. Create a **single PR** `develop → main` titled: “Release v5.0.0 (Outbound Dialer Alpha + Groq Speech + Ollama improvements)”.
3. Ensure CI green on PR.
4. Deploy `main` candidate to a reference server and run:
   - Golden baselines (claimed providers/transports)
   - Outbound dialer smoke tests (below)
5. Final doc pass (typos, commands, naming).
6. Merge PR to `main`.
7. Tag `v5.0.0` on `main` and publish GitHub Release notes from `CHANGELOG.md`.

### Outbound dialer smoke tests (minimum)

- **Internal extension lead** (e.g., `2765`):
  - Consent enabled: press `1` → AI connects; tool `hangup_call` works
  - Consent enabled: press `2` → call ends; outcome `consent_denied`
  - Consent enabled: no input → outcome `consent_timeout`
- **Voicemail** (cell or extension with voicemail):
  - AMD detects MACHINE/NOTSURE → voicemail drop plays; AI does not connect
- **UI verification**:
  - Lead row updates (attempt count, time/duration, outcome, AMD, DTMF)
  - “Call History” opens inline modal (no new tab)

---

## Open Questions (for maintainer confirmation)

- Tag naming: confirmed `v5.0.0` (3-part).
- Golden baseline claims: are we still advertising “5 production-ready golden baselines” in README, and do we have current evidence for all five on `main` head?
- Release messaging: do we want the outbound dialer described as **GA** or **Alpha** (and keep the wording consistent across README/docs/changelog/release notes)?

---

## Appendix A: File Inventory (since last GA tag)

This section lists files changed since the last GA tag (`4.6.0`) and the delta needed for a PR into `main`.

### A1) Commit list since `4.6.0`

Use:

```bash
git log --oneline 4.6.0..develop
```

### A2) Files changed since `4.6.0`

Use:

```bash
git diff --name-status 4.6.0..develop
```

Notes:
- This release includes broad UI, engine, pipeline, and docs changes, plus a new outbound subsystem.
- Ensure `docker-compose.yml` naming is consistent with `admin_ui`, `ai_engine`, `local_ai_server` across docs and scripts.
