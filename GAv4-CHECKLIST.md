# GA v4.0 Release Checklist

**Status**: 🟡 IN PROGRESS  
**Target**: Merge `develop` → `main` for General Availability v4.0  
**Timeline**: 5 days  
**Linear Project**: [GA v4.0 Release](https://linear.app/asterisk-ai-voice-agent/project/ga-v40-release-a621fcb51a45)

---

## Overview

This checklist tracks all work required to prepare the develop branch for public release as GA v4.0. All items are tracked as Linear issues in the "GA v4.0 Release" project.

### Linear Issue Tracking

All tasks are tracked in Linear with appropriate labels:
- **P0-blocking**: Must fix before GA (blocks merge to main)
- **P1-important**: Should fix before GA but not blocking
- **P2-nice-to-have**: Post-GA improvements
- **critical-fix**: Code fixes
- **feature-verification**: Feature validation
- **documentation**: Documentation work
- **testing**: Testing and validation

---

## Section A: Critical Code Fixes (P0 - Blocking)

These issues **MUST** be completed before GA release.

### AAVA-13: Fix VAD Sample Rate Mismatch
**Status**: ⏳ TODO  
**Priority**: P0 (blocks GA)  
**Effort**: 2-3 hours  
**Linear**: [AAVA-13](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-13)

**Problem**: WebRTC VAD hardcoded to 8kHz but OpenAI Realtime provides 24kHz audio, causing exceptions.

**Tasks**:
- [ ] Add `sample_rate` parameter to `vad_manager.process_frame()`
- [ ] Update `audio_gating_manager.py` to pass correct sample rate (24000 for OpenAI)
- [ ] Test with 5 OpenAI Realtime calls
- [ ] Verify zero WebRTC VAD errors in logs
- [ ] Confirm barge-in functionality works
- [ ] Verify no regressions on Deepgram calls (8kHz)

**Files to Modify**:
- `src/core/vad_manager.py`
- `src/core/audio_gating_manager.py`

**Acceptance Criteria**:
- ✅ Zero `webrtcvad.Error` in logs for OpenAI calls
- ✅ Barge-in detection operational
- ✅ All tests pass

---

### AAVA-14: Set Production Logging Levels
**Status**: ⏳ TODO  
**Priority**: P0 (security + performance)  
**Effort**: 30 minutes  
**Linear**: [AAVA-14](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-14)

**Problem**: Debug logging exposes sensitive data and impacts performance.

**Tasks**:
- [ ] Set `LOG_LEVEL=info` in `.env`
- [ ] Set `LOG_LEVEL=info` in `docker-compose.yml` (if present)
- [ ] Set `diag_enable_taps: false` in `config/ai-agent.yaml`
- [ ] Update `.env.example` to show info as default
- [ ] Restart and test with 3 calls (OpenAI, Deepgram, Local)
- [ ] Verify log volume reduction (~30-40%)
- [ ] Confirm no critical information missing

**Files to Modify**:
- `.env`
- `docker-compose.yml`
- `config/ai-agent.yaml`
- `.env.example`

**Acceptance Criteria**:
- ✅ LOG_LEVEL=info set
- ✅ No debug logs during test calls
- ✅ Warnings/errors still visible

---

### AAVA-15: Suppress Deepgram Low RMS Warning Spam
**Status**: ⏳ TODO  
**Priority**: P0 (log pollution)  
**Effort**: 1-2 hours  
**Linear**: [AAVA-15](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-15)

**Problem**: 10+ warnings/second during silence creates massive log volume.

**Tasks**:
- [ ] Add per-call silence tracking to `DeepgramProvider`
- [ ] Implement suppression logic (max 3 warnings + 1 suppression notice per call)
- [ ] Test with 5 Deepgram calls with silence periods
- [ ] Verify max 3 warnings + 1 suppression notice per call
- [ ] Confirm no functional regression

**Files to Modify**:
- `src/providers/deepgram.py`

**Acceptance Criteria**:
- ✅ Max 3 low RMS warnings per call
- ✅ Suppression notice logged once
- ✅ Log volume significantly reduced

---

## Section B: Important Improvements (P1)

These should be completed before GA but are not blocking.

### AAVA-16: Tune Jitter Buffer
**Status**: ⏳ TODO  
**Priority**: P1 (audio quality)  
**Effort**: 1 hour  
**Linear**: [AAVA-16](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-16)

**Problem**: 42-48 underflows per call indicates buffer too small.

**Tasks**:
- [ ] Set `jitter_buffer_ms: 150` in `config/ai-agent.yaml`
- [ ] Deploy and make 10 test calls
- [ ] Check Dashboard 2 → Stream Underflow Rate
- [ ] Verify average < 10 underflows per call

**Files to Modify**:
- `config/ai-agent.yaml`

**Acceptance Criteria**:
- ✅ jitter_buffer_ms: 150
- ✅ Average underflows < 10 per call

---

### AAVA-21: Add Configuration Validation
**Status**: ⏳ TODO  
**Priority**: P1 (prevents misconfigurations)  
**Effort**: 2 hours  
**Linear**: [AAVA-21](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-21)

**Problem**: No validation at startup leads to runtime issues.

**Tasks**:
- [ ] Create `validate_production_config()` in `src/config.py`
- [ ] Add validation checks (errors and warnings)
- [ ] Call validation at engine startup
- [ ] Test with valid/invalid configs
- [ ] Verify errors block startup with clear messages

**Files to Modify**:
- `src/config.py`
- `src/engine.py`
- `src/engine_external_media.py`

**Acceptance Criteria**:
- ✅ Validation function complete
- ✅ Called at startup
- ✅ Errors block startup
- ✅ Warnings logged but don't block

---

## Section C: Feature Verification

### AAVA-17: Verify File Playback Functionality
**Status**: ⏳ TODO  
**Priority**: Feature Verification  
**Effort**: 3-4 hours  
**Linear**: [AAVA-17](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-17)

**Objective**: Code review + live testing of file playback functionality.

**Code Review Tasks**:
- [ ] Review `play_media_on_bridge()` implementation
- [ ] Review `play_media_on_bridge_with_id()` implementation
- [ ] Review `stop_playback()` implementation
- [ ] Review playback cleanup handlers
- [ ] Review error handling and edge cases
- [ ] Review barge-in playback stops

**Live Testing Tasks**:
- [ ] Test greeting playback
- [ ] Test TTS playback
- [ ] Test barge-in during playback
- [ ] Test multiple sequential playbacks
- [ ] Test error scenarios (file missing, bridge destroyed, etc.)
- [ ] Verify file cleanup working

**Acceptance Criteria**:
- ✅ Code review complete with no critical issues
- ✅ All 5 test cases pass
- ✅ No file leaks

---

### AAVA-18: Verify ExternalMedia Functionality
**Status**: ⏳ TODO  
**Priority**: Feature Verification  
**Effort**: 4-5 hours  
**Linear**: [AAVA-18](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-18)

**Objective**: Code review + live testing of ExternalMedia RTP transport.

**Code Review Tasks**:
- [ ] Review `engine_external_media.py` lifecycle management
- [ ] Review `_start_external_media_channel()` implementation
- [ ] Review `_handle_external_media_stasis_start()` implementation
- [ ] Review RTP server implementation
- [ ] Review SSRC mapping and audio routing
- [ ] Review error handling and edge cases

**Live Testing Tasks**:
- [ ] Test basic ExternalMedia call flow
- [ ] Test RTP audio capture (caller → provider)
- [ ] Test RTP audio playback (provider → caller)
- [ ] Test multiple concurrent ExternalMedia calls (3+)
- [ ] Test error scenarios (RTP timeout, channel fails, etc.)
- [ ] Verify audio quality equivalent to AudioSocket

**Acceptance Criteria**:
- ✅ Code review complete with no critical issues
- ✅ All 5 test cases pass
- ✅ Audio quality equivalent to AudioSocket
- ✅ No RTP packet loss > 1%

---

## Section D: Documentation Updates

### AAVA-19: Update and Merge Documentation
**Status**: ⏳ TODO  
**Priority**: Documentation (GA requirement)  
**Effort**: 1 day (8 hours)  
**Linear**: [AAVA-19](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-19)

**Objective**: Prepare all documentation for GA v4.0 release.

### Core Documentation Updates
- [ ] **README.md** - Update to v4.0, add GA badge, update features
- [ ] **docs/INSTALLATION.md** - Merge improvements, add monitoring section
- [ ] **docs/Architecture.md** - Merge architectural improvements
- [ ] **docs/Configuration-Reference.md** - Merge new config options
- [ ] **docs/Tuning-Recipes.md** - Merge production recipes

### Merge & Consolidate
- [ ] **Merge ROADMAP.md + ROADMAPv4.md** → `docs/plan/ROADMAP.md`
  - Keep completed milestones
  - Update with v4.0 status
  - Remove outdated sections

### Create New Documentation
- [ ] **CHANGELOG.md** (root) - v4.0 release notes
  - Breaking changes
  - New features
  - Bug fixes
  - Improvements
  - Migration notes from v3.0

- [ ] **docs/MONITORING_GUIDE.md**
  - Dashboard setup and usage
  - Per-call filtering
  - Alert configuration
  - Troubleshooting with dashboards

- [ ] **docs/TROUBLESHOOTING_GUIDE.md**
  - Common issues
  - `agent doctor` usage
  - `agent troubleshoot` examples
  - Log analysis
  - Provider-specific issues

- [ ] **docs/PRODUCTION_DEPLOYMENT.md**
  - Security hardening
  - Performance tuning
  - Monitoring setup
  - Backup & disaster recovery
  - Upgrade procedures

### Preserve Important Development Docs
Move to `docs/case-studies/`:
- [ ] **OPENAI_REALTIME_GOLDEN_BASELINE.md** - Keep as case study
- [ ] **docs/agent-cli-examples.md** - Create with solid examples
  - `agent init` walkthrough
  - `agent doctor` interpretation
  - `agent demo` usage
  - `agent troubleshoot` real examples

### Remove Development Artifacts
Remove from develop branch (40+ files):

**Root-level RCA/Progress docs**:
```
OPENAI_*_RCA.md (9 files)
P1_*.md, P2_*.md, P3_*.md (20+ files)
PROGRESS_SUMMARY_*.md
DEPLOYED_FIX.md
DEPRECATED_CODE_AUDIT.md
TESTING_GUIDE_P1.md
Agents.md, Gemini.md
ASTERISK_18_VERIFICATION_REPORT.md
PRODUCTION_HARDENING_PLAN.md
ROADMAP_STATUS_REVIEW_OCT27.md
P3_TEST_CALLS_RCA.md
```

**Temporary/Internal docs**:
```
docs/AudioSocket with Asterisk_ Technical Summary for A.md
docs/LOG_ANALYSIS_VAD_IMPLEMENTATION.md
docs/VAD_CRITICAL_FIXES_SUMMARY.md
docs/VAD_IMPLEMENTATION_SUMMARY.md
docs/deepgram-agent-api.md
docs/plan/P1_IMPLEMENTATION_PLAN.md
docs/regression/issues/*
```

### Update Installation Script
- [ ] **install.sh** - Add monitoring prompt:
```bash
echo ""
echo "Enable monitoring (Prometheus/Grafana/Loki)? [y/N]"
read -r enable_monitoring

if [[ "$enable_monitoring" =~ ^[Yy]$ ]]; then
    # Start monitoring stack
    docker compose up -d prometheus grafana loki
    echo "✅ Monitoring enabled"
    echo "📊 Grafana: http://localhost:3000 (admin/admin2025)"
    echo "📈 Prometheus: http://localhost:9090"
else
    echo ""
    echo "ℹ️  Monitoring can be enabled later with:"
    echo "   docker compose up -d prometheus grafana loki"
    echo "   See docs/MONITORING_GUIDE.md for details"
fi
```

**Acceptance Criteria**:
- ✅ All user-facing docs updated for v4.0
- ✅ CHANGELOG.md complete
- ✅ New guides created
- ✅ Development artifacts removed
- ✅ Important case studies preserved
- ✅ All links validated

---

## Section E: Pre-GA Testing & Validation

### AAVA-20: Pre-GA Test Matrix
**Status**: ⏳ TODO  
**Priority**: P0 Testing (GA requirement)  
**Effort**: 4 days  
**Linear**: [AAVA-20](https://linear.app/asterisk-ai-voice-agent/issue/AAVA-20)

**Objective**: Execute comprehensive test matrix before merge to main.

### Provider Testing

#### OpenAI Realtime (Default Provider)
- [ ] Tests 1-5: Basic calls (greeting, Q&A, barge-in, long conversation, error recovery)
- [ ] Tests 6-8: Edge cases (network interruption, API timeout, concurrent calls)
- [ ] Tests 9-10: Performance (latency < 2s, no audio artifacts)

**Success Criteria**: All 10/10 calls successful, barge-in working, zero VAD errors

#### Deepgram Voice Agent
- [ ] Tests 1-5: Basic calls
- [ ] Tests 6-8: Edge cases
- [ ] Tests 9-10: Performance

**Success Criteria**: All 10/10 calls successful, max 3 low RMS warnings per call

#### Local AI (Offline Mode)
- [ ] Tests 1-3: Basic calls
- [ ] Tests 4-5: Performance validation

**Success Criteria**: All 5/5 calls successful, no container crashes

### Transport Testing

#### AudioSocket (Primary)
- [ ] Verify AudioSocket server listening
- [ ] Test with slin (PCM16) format
- [ ] Multiple concurrent calls (3+)
- [ ] Call cleanup and resource release

#### ExternalMedia RTP (Alternative)
- [ ] Enable ExternalMedia transport
- [ ] Test 3 calls with RTP
- [ ] Verify SSRC mapping
- [ ] Audio quality equivalent to AudioSocket

### CLI Tools Validation

- [ ] **agent doctor** - Run on fresh installation, all checks pass
- [ ] **agent demo** - Execute successfully, no errors
- [ ] **agent init** - Run on clean environment, validates config
- [ ] **agent troubleshoot** - Run on recent call, generates analysis

### Monitoring & Observability

#### Grafana Dashboards
- [ ] All 5 dashboards accessible
- [ ] Per-call filtering working (call_id variable)
- [ ] Panels showing data correctly
- [ ] No query errors

#### Metrics Collection
- [ ] 275 metrics available in Prometheus
- [ ] Metrics updating during calls
- [ ] Alert rules loaded and evaluating
- [ ] No scrape errors

#### Test Call Analysis
- [ ] Make 10 test calls to populate histogram metrics
- [ ] Verify jitter buffer underflows < 10/call
- [ ] Validate dashboard accuracy

### Fresh Installation Test

#### Clean Ubuntu 22.04
- [ ] Clone repository
- [ ] Run `./install.sh`
- [ ] Choose provider (OpenAI Realtime)
- [ ] Complete setup wizard
- [ ] Verify health endpoint
- [ ] Make first test call
- [ ] Validate monitoring (if enabled)

**Success Criteria**: Installation completes without errors, first call works within 5 minutes

### Regression Testing

#### Existing Functionality
- [ ] ARI connection and event handling
- [ ] Bridge creation and management
- [ ] Channel lifecycle
- [ ] SessionStore state management
- [ ] WebSocket provider connections
- [ ] VAD and audio gating
- [ ] Barge-in detection and handling

### Performance & Stability

#### Metrics to Validate
- [ ] Underflows < 10 per call average
- [ ] Latency < 2s (first audio response)
- [ ] CPU usage < 50% during active calls
- [ ] Memory stable (no leaks over 30 minutes)
- [ ] Log volume < 100MB/day with info level

#### Concurrent Call Testing
- [ ] 3 simultaneous calls (no issues)
- [ ] 5 simultaneous calls (if hardware allows)
- [ ] Verify correct audio routing (no cross-talk)
- [ ] Resource cleanup after all calls end

**Acceptance Criteria**:
- ✅ All provider tests: 25/25 calls successful
- ✅ All transport tests pass
- ✅ All CLI tools working
- ✅ All dashboards operational
- ✅ Fresh install successful
- ✅ Zero critical regressions
- ✅ All performance metrics within acceptable ranges
- ✅ Concurrent calls working

---

## Section F: Release Preparation

### Final Pre-Merge Tasks

#### Version Updates
- [ ] Update version to 4.0.0 in all relevant files
- [ ] Update `README.md` with v4.0 badge
- [ ] Ensure all version references consistent

#### GitHub Release Preparation
- [ ] Create release draft on GitHub
- [ ] Add release notes from CHANGELOG.md
- [ ] Prepare release announcement
- [ ] Tag release (v4.0.0)

#### Final Validation
- [ ] Run `agent doctor` - all checks pass
- [ ] Run `agent demo` - successful
- [ ] Verify health endpoint responding
- [ ] Check all dashboards one final time
- [ ] Review Linear: all P0 issues closed

#### Branch Merge
- [ ] Ensure all tests passing on develop
- [ ] Create PR: develop → main
- [ ] Code review (if applicable)
- [ ] Merge to main
- [ ] Verify main branch CI/CD (if configured)
- [ ] Tag release: `git tag v4.0.0`
- [ ] Push tag: `git push origin v4.0.0`

#### Post-Merge
- [ ] Publish GitHub release
- [ ] Update documentation links (if needed)
- [ ] Announce release (social media, mailing list, etc.)
- [ ] Monitor for any immediate issues

---

## Timeline

### Day 1 (Today): Critical Fixes + Feature Verification Start
- [ ] Fix VAD sample rate mismatch (AAVA-13)
- [ ] Set production logging levels (AAVA-14)
- [ ] Suppress Deepgram warning spam (AAVA-15)
- [ ] Start file playback code review (AAVA-17)
- [ ] Start ExternalMedia code review (AAVA-18)

### Day 2: Feature Verification Complete + Testing Start
- [ ] Complete file playback verification (AAVA-17)
- [ ] Complete ExternalMedia verification (AAVA-18)
- [ ] Tune jitter buffer (AAVA-16)
- [ ] Add config validation (AAVA-21)
- [ ] Start provider testing (AAVA-20)

### Day 3: Documentation Updates
- [ ] Update core documentation (AAVA-19)
- [ ] Create new guides (AAVA-19)
- [ ] Merge ROADMAP docs (AAVA-19)
- [ ] Create CHANGELOG.md (AAVA-19)
- [ ] Remove development artifacts (AAVA-19)
- [ ] Continue testing (AAVA-20)

### Day 4: Testing & Validation
- [ ] Complete all provider tests (AAVA-20)
- [ ] CLI tools validation (AAVA-20)
- [ ] Monitoring validation (AAVA-20)
- [ ] Fresh install test (AAVA-20)
- [ ] Regression testing (AAVA-20)
- [ ] Performance validation (AAVA-20)

### Day 5: Final Review + Merge to Main
- [ ] Final validation sweep
- [ ] Version updates
- [ ] GitHub release preparation
- [ ] Create PR: develop → main
- [ ] Merge to main
- [ ] Tag release v4.0.0
- [ ] Publish release
- [ ] Announce release

---

## Success Criteria

### Must Complete Before GA
- ✅ All P0 issues closed (AAVA-13, AAVA-14, AAVA-15)
- ✅ All feature verification complete (AAVA-17, AAVA-18)
- ✅ All documentation updated (AAVA-19)
- ✅ All tests passing (AAVA-20)
- ✅ Zero critical bugs in final validation
- ✅ Fresh install works on clean Ubuntu 22.04

### Quality Gates
- ✅ Zero `webrtcvad.Error` in OpenAI calls
- ✅ Log volume < 100MB/day at info level
- ✅ Underflows < 10 per call average
- ✅ 100% pass rate on test matrix (25/25 calls)
- ✅ All dashboards operational
- ✅ All CLI tools working
- ✅ Documentation accurate and complete

### Release Readiness
- ✅ CHANGELOG.md complete
- ✅ All user-facing docs updated
- ✅ Version bumped to v4.0
- ✅ GitHub release prepared
- ✅ No open P0 issues

---

## Risk Assessment

### Pre-Fixes
| Risk | Impact | Likelihood | Severity |
|------|--------|-----------|----------|
| VAD errors break barge-in | MEDIUM | HIGH | 🟡 MEDIUM |
| Log spam fills disk | HIGH | MEDIUM | 🟡 MEDIUM |
| Debug logs expose sensitive data | HIGH | MEDIUM | 🟡 MEDIUM |
| Underflows degrade audio | MEDIUM | HIGH | 🟡 MEDIUM |

### Post-Fixes
| Risk | Impact | Likelihood | Severity |
|------|--------|-----------|----------|
| All above risks | LOW | LOW | 🟢 LOW |

---

## Notes

- All issues tracked in Linear: https://linear.app/asterisk-ai-voice-agent/project/ga-v40-release-a621fcb51a45
- Use Linear to update progress, add comments, attach artifacts
- Create new P0 issues immediately if critical problems discovered during testing
- Update this checklist as tasks complete
- Keep stakeholders informed of progress

---

**Last Updated**: October 27, 2025  
**Next Review**: Daily during GA prep week
