# Production Hardening Plan — Pre-Release Audit

**Date**: October 27, 2025  
**Status**: CRITICAL ISSUES IDENTIFIED  
**Target**: Public Release Readiness  

---

## Executive Summary

**Current State**: Platform is functionally production-ready but has **3 critical issues** and **5 improvements** needed before public release.

**Timeline**: 2-3 days to resolve all critical issues + improvements

**Risk Level**: 🟡 **MEDIUM** — Issues are non-blocking for continued use but must be fixed before public release

---

## 🚨 Critical Issues (MUST FIX)

### Issue 1: WebRTC VAD Sample Rate Mismatch ⚠️ HIGH PRIORITY

**Severity**: HIGH (causes exceptions, though caught)

**Symptoms**:
```python
webrtcvad.Error: Error while processing frame
File "/app/src/core/vad_manager.py", line 150, in process_frame
    webrtc_result = self.webrtc_vad.is_speech(audio_frame_pcm16, 8000)
```

**Root Cause**:
- `vad_manager.py` line 150 hardcodes sample rate to 8000 Hz
- OpenAI Realtime provides 24kHz PCM16 audio
- Audio gating manager passes 24kHz frames to VAD expecting 8kHz
- WebRTC VAD rejects frames with incorrect size for declared sample rate

**Impact**:
- VAD interrupt detection fails for OpenAI Realtime calls
- Falls back to pure gating (no interrupt detection)
- Logged as debug (not visible in production logs at info level)
- Does NOT break calls but reduces functionality

**Fix Required**:

```python
# src/core/vad_manager.py line 150

# BEFORE (BROKEN):
webrtc_result = self.webrtc_vad.is_speech(audio_frame_pcm16, 8000)

# AFTER (FIXED):
# Need to pass correct sample rate based on audio format
# Option 1: Resample to 8kHz before VAD (preferred for consistency)
# Option 2: Support multiple sample rates in VAD (16kHz, 24kHz)
# Option 3: Disable WebRTC VAD for 24kHz, use energy-only

# RECOMMENDED: Add sample_rate parameter to process_frame()
async def process_frame(
    self, 
    call_id: str, 
    audio_frame_pcm16: bytes,
    sample_rate: int = 8000  # Make it explicit
) -> VADResult:
    ...
    if self.webrtc_vad:
        try:
            webrtc_result = self.webrtc_vad.is_speech(audio_frame_pcm16, sample_rate)
```

**Files to Modify**:
1. `src/core/vad_manager.py` - Add sample_rate parameter
2. `src/core/audio_gating_manager.py` - Pass correct sample rate (24000 for OpenAI)
3. `src/engine.py` - Pass sample rate from AudioSocket (8000 for Deepgram)

**Testing**:
- Make 5 OpenAI Realtime calls
- Check logs for WebRTC VAD errors → should be zero
- Test barge-in functionality → should work
- Verify no regressions on Deepgram calls

**Effort**: 2-3 hours

---

### Issue 2: Deepgram Low RMS Warning Spam 📢 MEDIUM PRIORITY

**Severity**: MEDIUM (log spam, no functional impact)

**Symptoms**:
```json
{
  "rms": 8,
  "rms_ma": 8,
  "streak": 130,
  "event": "Deepgram upstream low RMS sustained",
  "level": "warning"
}
```

**Frequency**: 10+ warnings per second during silence periods

**Root Cause**:
- Deepgram provider logs warning for every low-RMS frame during silence
- Legitimate silence (no speech) triggers continuous warnings
- Creates massive log volume (hundreds of warnings per call)

**Impact**:
- Log files grow quickly (GB/day in production)
- Makes it harder to find real issues
- May affect log aggregation systems (Loki, CloudWatch)
- Not a functional problem (silence handling works correctly)

**Fix Options**:

**Option A: Suppress After Threshold** (RECOMMENDED):
```python
# src/providers/deepgram.py

# Add per-call silence tracking
class DeepgramProvider:
    def __init__(self):
        self._silence_warnings_logged = {}  # call_id -> count
        
    def _check_low_rms(self, call_id, rms, streak):
        # Only log first 3 warnings, then suppress
        warnings_count = self._silence_warnings_logged.get(call_id, 0)
        
        if warnings_count < 3:
            logger.warning("Deepgram upstream low RMS sustained", ...)
            self._silence_warnings_logged[call_id] = warnings_count + 1
        elif warnings_count == 3:
            logger.info(
                "Deepgram low RMS warnings suppressed (silence is normal)",
                call_id=call_id,
                total_warnings=3
            )
            self._silence_warnings_logged[call_id] = warnings_count + 1
        # Else: suppress (already logged 3 times + suppression notice)
```

**Option B: Change to Debug Level**:
```python
# Change from warning to debug
logger.debug("Deepgram upstream low RMS sustained", ...)
```

**Option C: Remove Entirely**:
- Silence is expected and normal
- Remove logging unless RMS is unexpectedly high (indicates actual problem)

**Recommendation**: **Option A** - Allows initial visibility, then suppresses spam

**Files to Modify**:
1. `src/providers/deepgram.py` - Add silence tracking and suppression

**Testing**:
- Make 5 Deepgram calls with silence periods
- Check logs: Should see max 3 low RMS warnings per call
- Verify suppression message logged once
- Confirm no functional regression

**Effort**: 1-2 hours

---

### Issue 3: Production Logging Levels 📝 MEDIUM PRIORITY

**Severity**: MEDIUM (performance and security impact)

**Current State**:
- Debug-level logging enabled in production
- Logs include sensitive data (audio bytes, API responses)
- High log volume impacts performance
- Makes troubleshooting harder (noise vs signal)

**Fix Required**:

```bash
# .env or docker-compose.yml

# CURRENT (DEVELOPMENT):
LOG_LEVEL=debug
STREAMING_LOG_LEVEL=debug

# PRODUCTION (RECOMMENDED):
LOG_LEVEL=info
STREAMING_LOG_LEVEL=info

# For troubleshooting specific issues:
# LOG_LEVEL=debug  # Temporarily only
```

**Additional Changes**:

```yaml
# config/ai-agent.yaml

# Remove or comment out diagnostic settings:
# streaming:
#   diag_enable_taps: false  # Set false in production
#   diag_out_dir: /tmp/ai-engine-taps  # Not needed in production
```

**Impact of Info Level**:
- ✅ WebRTC VAD errors: Won't be visible (that's OK, they're caught)
- ✅ Low RMS warnings: Still visible (warning level)
- ✅ Critical errors: Still visible (error level)
- ✅ Performance: ~30-40% reduction in log volume
- ✅ Security: Sensitive debug data not logged

**Files to Modify**:
1. `.env` - Set LOG_LEVEL=info
2. `docker-compose.yml` - Set environment LOG_LEVEL=info
3. `config/ai-agent.yaml` - Set diag_enable_taps: false

**Testing**:
- Restart ai_engine with new log level
- Make 3 test calls
- Verify: No debug messages, warnings/errors still visible
- Check log volume reduction

**Effort**: 30 minutes

---

## 💡 Important Improvements (SHOULD FIX)

### Improvement 1: Jitter Buffer Tuning 🔧

**Issue**: 42-48 underflows per call (from monitoring data)

**Fix**:
```yaml
# config/ai-agent.yaml
streaming:
  jitter_buffer_ms: 150  # Increase from current (~100)
```

**Validation**:
- Make 5 test calls
- Check Dashboard 2 → Underflow Rate
- Target: < 10 underflows per call

**Effort**: 1 hour (config + validation)

---

### Improvement 2: Security Hardening 🔒

**Issues**:
1. Grafana credentials: `admin/admin2025` (not production-safe)
2. API keys in plain text `.env` file
3. No rate limiting on AudioSocket port
4. No authentication on Prometheus/Grafana

**Fixes**:

```bash
# 1. Change Grafana password
docker exec -it grafana grafana-cli admin reset-admin-password <strong-password>

# 2. Use secrets management (optional but recommended)
# Instead of:
OPENAI_API_KEY=sk-xxx
DEEPGRAM_API_KEY=xxx

# Use Docker secrets:
docker secret create openai_api_key /path/to/key
# Then reference in docker-compose.yml

# 3. Add nginx reverse proxy with basic auth
# monitoring/nginx/nginx.conf
location /grafana/ {
    auth_basic "Monitoring";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://grafana:3000/;
}

# 4. Firewall rules (if public server)
# Only allow AudioSocket from Asterisk server IP
iptables -A INPUT -p tcp --dport 8090 -s <asterisk-ip> -j ACCEPT
iptables -A INPUT -p tcp --dport 8090 -j DROP
```

**Effort**: 2-3 hours

---

### Improvement 3: Configuration Validation 

 ✅

**Current State**: Config loaded but not validated at startup

**Fix**:
```python
# src/config.py

def validate_production_config(config):
    """Validate configuration for production deployment."""
    errors = []
    warnings = []
    
    # Critical checks
    if config.get('vad', {}).get('enhanced_enabled') and not config.get('vad', {}).get('webrtc_aggressiveness'):
        errors.append("VAD enabled but webrtc_aggressiveness not set")
    
    if config.get('audiosocket', {}).get('format') not in ['slin', 'slin16', 'slin24']:
        errors.append(f"Invalid audiosocket format: {config['audiosocket']['format']}")
    
    # Production warnings
    if config.get('streaming', {}).get('diag_enable_taps', False):
        warnings.append("Diagnostic taps enabled in production (performance impact)")
    
    if os.getenv('LOG_LEVEL', 'info').lower() == 'debug':
        warnings.append("Debug logging in production (security/performance risk)")
    
    # Check API keys
    if not os.getenv('OPENAI_API_KEY') and not os.getenv('DEEPGRAM_API_KEY'):
        errors.append("No provider API keys configured")
    
    if errors:
        logger.error("❌ Configuration validation FAILED", errors=errors, warnings=warnings)
        raise ConfigValidationError(errors)
    
    if warnings:
        logger.warning("⚠️ Configuration warnings", warnings=warnings)
    
    logger.info("✅ Configuration validation passed")
```

**Call at startup**:
```python
# src/engine.py line ~100
config = Config.load()
validate_production_config(config.to_dict())
```

**Effort**: 2 hours

---

### Improvement 4: Graceful Shutdown 🛑

**Issue**: Container may not clean up resources properly on shutdown

**Fix**:
```python
# src/engine.py

import signal

class AIVoiceEngine:
    def __init__(self):
        self._shutdown_event = asyncio.Event()
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("🛑 Shutdown signal received", signal=signum)
        self._shutdown_event.set()
    
    async def run(self):
        """Main event loop with graceful shutdown."""
        try:
            # Existing startup code...
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        finally:
            logger.info("🧹 Cleaning up resources...")
            
            # Close all active calls
            for call_id in list(self._active_calls.keys()):
                await self._cleanup_call(call_id)
            
            # Close provider connections
            for provider in self._providers.values():
                await provider.cleanup()
            
            # Close AudioSocket server
            if self._audiosocket_server:
                self._audiosocket_server.close()
                await self._audiosocket_server.wait_closed()
            
            logger.info("✅ Shutdown complete")
```

**Effort**: 2-3 hours

---

### Improvement 5: Health Check Endpoint 🏥

**Issue**: No way to check if service is healthy from outside

**Fix**:
```python
# src/engine.py

from aiohttp import web

class AIVoiceEngine:
    async def _start_health_server(self):
        """Start HTTP health check server."""
        app = web.Application()
        app.router.add_get('/health', self._health_check)
        app.router.add_get('/ready', self._readiness_check)
        app.router.add_get('/metrics', self._metrics_endpoint)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 15001)  # Different from metrics port
        await site.start()
        
        logger.info("🏥 Health check server started", port=15001)
    
    async def _health_check(self, request):
        """Liveness probe - is the service running?"""
        return web.json_response({
            'status': 'healthy',
            'timestamp': time.time()
        })
    
    async def _readiness_check(self, request):
        """Readiness probe - is the service ready for traffic?"""
        checks = {
            'asterisk_ari': await self._check_ari_connection(),
            'audiosocket_server': self._audiosocket_server is not None,
            'providers': len(self._providers) > 0,
        }
        
        all_ready = all(checks.values())
        
        return web.json_response({
            'status': 'ready' if all_ready else 'not_ready',
            'checks': checks,
            'timestamp': time.time()
        }, status=200 if all_ready else 503)
```

**Docker Compose Health Check**:
```yaml
# docker-compose.yml
services:
  ai-engine:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:15001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

**Effort**: 3-4 hours

---

## 📋 Production Release Checklist

### Code Changes (2-3 days)

- [ ] **Fix VAD sample rate mismatch** (2-3 hours)
  - [ ] Add sample_rate parameter to vad_manager.process_frame()
  - [ ] Update audio_gating_manager to pass 24000 for OpenAI
  - [ ] Test with 5 OpenAI calls
  - [ ] Verify barge-in works
  - [ ] Commit: `fix(vad): support multiple sample rates for WebRTC VAD`

- [ ] **Suppress Deepgram low RMS spam** (1-2 hours)
  - [ ] Add silence warning suppression logic
  - [ ] Test with 5 Deepgram calls
  - [ ] Verify max 3 warnings + 1 suppression notice per call
  - [ ] Commit: `fix(deepgram): suppress low RMS warning spam during silence`

- [ ] **Set production logging levels** (30 min)
  - [ ] Update .env: LOG_LEVEL=info
  - [ ] Update docker-compose.yml environment
  - [ ] Set diag_enable_taps: false
  - [ ] Restart and verify log volume reduction
  - [ ] Commit: `chore: set production-safe logging levels`

- [ ] **Tune jitter buffer** (1 hour)
  - [ ] Set jitter_buffer_ms: 150
  - [ ] Test with 5 calls
  - [ ] Verify < 10 underflows per call
  - [ ] Commit: `fix(streaming): increase jitter buffer to reduce underflows`

- [ ] **Add config validation** (2 hours)
  - [ ] Implement validate_production_config()
  - [ ] Add validation call at startup
  - [ ] Test with valid and invalid configs
  - [ ] Commit: `feat(config): add production configuration validation`

- [ ] **Implement graceful shutdown** (2-3 hours)
  - [ ] Add signal handlers
  - [ ] Implement cleanup logic
  - [ ] Test shutdown scenarios
  - [ ] Commit: `feat(engine): add graceful shutdown and resource cleanup`

- [ ] **Add health check endpoint** (3-4 hours)
  - [ ] Implement /health and /ready endpoints
  - [ ] Add Docker healthcheck
  - [ ] Test liveness and readiness probes
  - [ ] Commit: `feat(health): add health check and readiness probes`

### Security (2-3 hours)

- [ ] **Change default passwords**
  - [ ] Grafana: admin/admin2025 → strong password
  - [ ] Prometheus: Add basic auth if exposed
  - [ ] Document password management in deployment guide

- [ ] **Review API key management**
  - [ ] Ensure .env is in .gitignore
  - [ ] Document secrets management best practices
  - [ ] Consider Docker secrets for production

- [ ] **Network security**
  - [ ] Document firewall rules for AudioSocket port
  - [ ] Add nginx reverse proxy config (optional)
  - [ ] Document secure deployment architecture

### Documentation (1-2 days)

- [ ] **Production Deployment Guide** (`docs/PRODUCTION_DEPLOYMENT.md`)
  - [ ] Hardware/resource requirements
  - [ ] Security hardening steps
  - [ ] Monitoring setup
  - [ ] Backup and disaster recovery
  - [ ] Upgrade procedures

- [ ] **Operations Runbook** (`docs/operations/RUNBOOK.md`)
  - [ ] Dashboard usage guide
  - [ ] Alert response procedures
  - [ ] Common troubleshooting scenarios
  - [ ] Escalation paths

- [ ] **Quick Start Guide** (update `README.md`)
  - [ ] 30-minute setup workflow
  - [ ] Prerequisites checklist
  - [ ] First call validation
  - [ ] Common pitfalls

- [ ] **VAD Tuning Guide** (`docs/VAD_TUNING_GUIDE.md`)
  - [ ] When to use aggressiveness levels 0-3
  - [ ] Provider-specific recommendations
  - [ ] Noise environment considerations

- [ ] **Dashboard Usage Guide** (`docs/monitoring/DASHBOARD_USAGE.md`)
  - [ ] Per-call filtering workflow
  - [ ] Common troubleshooting queries
  - [ ] Alert interpretation

### Testing (1 day)

- [ ] **Regression Testing**
  - [ ] 10 Deepgram calls (mix of scenarios)
  - [ ] 10 OpenAI Realtime calls (test barge-in)
  - [ ] Verify all metrics in dashboards
  - [ ] Check `agent troubleshoot` output
  - [ ] Validate alert thresholds

- [ ] **Load Testing** (optional but recommended)
  - [ ] 10 concurrent calls
  - [ ] 50 concurrent calls (if target scale)
  - [ ] Monitor CPU, memory, network
  - [ ] Check for resource leaks

- [ ] **Failure Testing**
  - [ ] Provider API outage simulation
  - [ ] Network disconnection scenarios
  - [ ] Container restart during active call
  - [ ] Graceful shutdown with active calls

### Final Validation

- [ ] **agent doctor** passes all checks
- [ ] **agent demo** completes successfully
- [ ] **agent troubleshoot** on last call shows EXCELLENT or GOOD
- [ ] All 5 dashboards showing data
- [ ] Alert rules loaded and evaluating
- [ ] Health check endpoint responding
- [ ] Logs at info level (no debug spam)
- [ ] Zero WebRTC VAD errors in logs
- [ ] < 5 Deepgram low RMS warnings per call
- [ ] < 10 underflows per call average
- [ ] Documentation complete and reviewed

---

## 📊 Risk Assessment

### Pre-Fixes

| Risk | Impact | Likelihood | Severity |
|------|--------|-----------|----------|
| VAD errors break barge-in | MEDIUM | HIGH | 🟡 MEDIUM |
| Log spam fills disk | HIGH | MEDIUM | 🟡 MEDIUM |
| Debug logs expose sensitive data | HIGH | MEDIUM | 🟡 MEDIUM |
| Underflows degrade audio | MEDIUM | HIGH | 🟡 MEDIUM |
| No health checks → undetected failures | HIGH | LOW | 🟠 LOW |

### Post-Fixes

| Risk | Impact | Likelihood | Severity |
|------|--------|-----------|----------|
| VAD errors break barge-in | MEDIUM | LOW | 🟢 LOW |
| Log spam fills disk | HIGH | LOW | 🟢 LOW |
| Debug logs expose sensitive data | HIGH | LOW | 🟢 LOW |
| Underflows degrade audio | MEDIUM | LOW | 🟢 LOW |
| No health checks → undetected failures | HIGH | LOW | 🟢 LOW |

---

## 🎯 Recommended Approach

### Phase 1: Critical Fixes (Day 1)

**Priority Order**:
1. Fix VAD sample rate mismatch (affects functionality)
2. Set production logging levels (security + performance)
3. Suppress Deepgram warning spam (log hygiene)
4. Tune jitter buffer (audio quality)

**Goal**: Address all critical issues that affect functionality or security

**Validation**: Make 20 test calls (10 each provider), verify zero critical errors

### Phase 2: Improvements (Day 2)

**Priority Order**:
1. Add config validation (prevents misconfigurations)
2. Implement graceful shutdown (operational stability)
3. Add health check endpoint (monitoring integration)
4. Security hardening (change passwords, document best practices)

**Goal**: Production operational readiness

**Validation**: Run `agent doctor`, check health endpoints, test graceful shutdown

### Phase 3: Documentation (Day 3)

**Priority Order**:
1. Production Deployment Guide (critical for users)
2. Operations Runbook (critical for operators)
3. Quick Start Guide (user onboarding)
4. Dashboard Usage Guide (monitoring)
5. VAD Tuning Guide (troubleshooting)

**Goal**: Enable users and operators to deploy and maintain the system

**Validation**: Have someone unfamiliar follow guides, collect feedback

---

## 📈 Success Criteria

### Must Have (Blocking Release)
- [ ] ✅ Zero WebRTC VAD errors in logs
- [ ] ✅ Log volume < 100MB/day for 50 calls
- [ ] ✅ Underflows < 10 per call average
- [ ] ✅ Production logging levels set (info)
- [ ] ✅ Default passwords changed
- [ ] ✅ Production Deployment Guide complete
- [ ] ✅ Operations Runbook complete

### Should Have (Non-Blocking but Important)
- [ ] ✅ Config validation at startup
- [ ] ✅ Graceful shutdown implemented
- [ ] ✅ Health check endpoint available
- [ ] ✅ All documentation guides complete
- [ ] ✅ Security best practices documented

### Nice to Have (Future)
- [ ] Load testing completed (10/50/100 concurrent)
- [ ] Failure scenario testing
- [ ] Automated deployment pipeline
- [ ] Multi-region deployment guide

---

## 🚀 Public Release Go/No-Go Criteria

### GO Criteria
- ✅ All "Must Have" items complete
- ✅ Zero critical bugs in last 20 test calls
- ✅ Documentation reviewed and approved
- ✅ Security audit passed
- ✅ Performance benchmarks met (< 10 underflows, < 2s latency)

### NO-GO Criteria
- ❌ Any critical bug found in testing
- ❌ Documentation incomplete or inaccurate
- ❌ Security issues unresolved
- ❌ Performance below acceptable thresholds

---

## 📝 Next Steps

### Immediate (Today)
1. Review this plan with team
2. Prioritize fixes based on impact
3. Start with Phase 1 (Critical Fixes)

### Short-Term (This Week)
1. Complete all Phase 1 fixes
2. Complete Phase 2 improvements
3. Validate with 20+ test calls

### Medium-Term (Next Week)
1. Complete all documentation
2. Security review and hardening
3. Final validation and testing
4. Public release decision

---

## 📞 Contact & Support

**Technical Questions**: Review code comments and documentation
**Operational Issues**: See Operations Runbook (when complete)
**Security Concerns**: Follow security hardening guide
**General**: GitHub Issues (after public release)

---

**Status**: 🟡 **READY FOR HARDENING** — Platform functional, needs production polish before public release

**Estimated Time to Public Release**: 2-3 days (if all phases completed)

**Confidence Level**: HIGH — Issues are well-understood and fixable
