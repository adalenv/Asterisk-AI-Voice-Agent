# P1 Multi-Provider Deployment Guide

## ✅ Implementation Status

**P1 Multi-Provider Support: COMPLETE (Tasks 1-7)**

### Completed Components

1. ✅ **TransportOrchestrator** (`src/core/transport_orchestrator.py`)
   - Profile resolution with precedence
   - Format negotiation with provider capabilities
   - Context mapping for semantic routing
   - Backward compatibility (legacy config synthesis)

2. ✅ **Provider Capabilities**
   - Deepgram: `get_capabilities()` + `parse_ack()` for SettingsApplied
   - OpenAI Realtime: `get_capabilities()` + `parse_ack()` for session.updated

3. ✅ **Audio Profiles Configuration** (`config/ai-agent.yaml`)
   - `telephony_ulaw_8k` (default, 8kHz μ-law telephony)
   - `wideband_pcm_16k` (16kHz PCM for better quality)
   - `openai_realtime_24k` (24kHz native, downsampled to 16k for AudioSocket)

4. ✅ **Context Mapping** (`config/ai-agent.yaml`)
   - `default`, `sales`, `support`, `premium`
   - Each maps to profile + prompt + greeting + optional provider override

5. ✅ **Engine Integration** (`src/engine.py`)
   - `_resolve_audio_profile()` - Reads channel vars, negotiates formats
   - `_emit_transport_card()` - One-shot consolidated transport log
   - Called during StasisStart before provider initialization

---

## 🚀 Deployment Steps

### 1. Commit & Push (Already Done ✅)

```bash
# Already committed and pushed to develop:
# - 365e178: TransportOrchestrator + provider capabilities
# - fe01e27: Config + engine init
# - 39a2dfc: Complete engine integration
```

### 2. Deploy to Server

```bash
# SSH to server
ssh root@voiprnd.nemtclouddispatch.com

# Navigate to project
cd /root/Asterisk-AI-Voice-Agent

# Stash any local changes (if needed)
git stash save -u "server-pre-p1-pull-$(date +%Y%m%d-%H%M%S)"

# Pull latest code
git pull --rebase origin develop

# Verify config has profiles block
grep -A 5 "^profiles:" config/ai-agent.yaml

# Rebuild and force-recreate container
docker compose -f docker-compose.yml build ai-engine
docker compose -f docker-compose.yml up -d --force-recreate ai-engine

# Wait for startup
sleep 5

# Verify health
docker ps | grep ai_engine

# Check logs for TransportOrchestrator initialization
docker logs --since 2m ai_engine | grep -E "TransportOrchestrator|profiles|contexts"
```

**Expected Output**:

```
TransportOrchestrator initialized | profiles=['telephony_ulaw_8k', 'wideband_pcm_16k', 'openai_realtime_24k'] contexts=['default', 'sales', 'support', 'premium'] default='telephony_ulaw_8k'
```

---

## 🧪 Test Scenarios

### Scenario 1: Default Profile (Baseline Regression)

**Goal**: Verify P1 doesn't break existing Deepgram setup

**Dialplan**: No channel vars (uses default profile)

**Expected**:

- Profile: `telephony_ulaw_8k`
- Provider: `deepgram`
- Wire: `slin` @ 8kHz (320 bytes/frame)
- Provider I/O: `mulaw` @ 8kHz

**Test**:

```bash
# Place call to default extension
# Let AI speak greeting
# Have 2-3 turn conversation
# Verify clean audio both directions
```

**RCA Check**:

```bash
# From local machine after call:
bash scripts/rca_collect.sh

# Check TransportCard log:
grep "TransportCard" logs/remote/rca-*/logs/ai-engine*.log

# Verify:
# - profile: "telephony_ulaw_8k"
# - wire.encoding: "slin"
# - wire.sample_rate_hz: 8000
# - provider_input.encoding: "mulaw"
# - provider_output.encoding: "mulaw"
```

---

### Scenario 2: Sales Context (Wideband Profile)

**Goal**: Test context mapping with profile override

**Dialplan Setup**:

```
[from-ai-agent-sales]
exten => s,1,NoOp(AI Agent - Sales Context)
  same => n,Set(AI_CONTEXT=sales)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()
```

**Expected**:

- Profile: `wideband_pcm_16k` (from context)
- Provider: `deepgram` (from context)
- Wire: `slin16` @ 16kHz
- Provider I/O: `linear16` @ 16kHz
- Greeting: "Thanks for calling! How can I help you find what you need today?"
- Prompt: "...enthusiastic sales assistant..."

**Test**:

```bash
# Place call to sales extension
# Verify upbeat greeting
# Test response style (should be enthusiastic)
```

**RCA Check**:

```bash
grep "TransportCard" logs/remote/rca-*/logs/ai-engine*.log

# Verify:
# - profile: "wideband_pcm_16k"
# - context: "sales"
# - wire.sample_rate_hz: 16000
# - context_config.greeting: "Thanks for calling..."
```

---

### Scenario 3: OpenAI Realtime Direct Override

**Goal**: Test provider switching via AI_PROVIDER channel var

**Prerequisites**:

- OpenAI API key in `.env`: `OPENAI_API_KEY=sk-...`
- Verify `providers.openai_realtime.enabled: true` in YAML

**Dialplan Setup**:

```
[from-ai-agent-openai]
exten => s,1,NoOp(AI Agent - OpenAI Realtime)
  same => n,Set(AI_PROVIDER=openai_realtime)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()
```

**Expected**:

- Profile: `telephony_ulaw_8k` (default, unless overridden)
- Provider: `openai_realtime`
- Wire: `slin` @ 8kHz
- Provider I/O: `linear16` @ 24kHz (OpenAI native)
- Transcoding: 24k → 8k downsample for AudioSocket egress

**Test**:

```bash
# Place call to OpenAI extension
# Verify OpenAI's voice/personality
# Test 2-3 turn conversation
# Check for any format warnings
```

**RCA Check**:

```bash
grep -E "TransportCard|OpenAI|session.updated" logs/remote/rca-*/logs/ai-engine*.log

# Verify:
# - profile: "telephony_ulaw_8k" (or custom if set)
# - provider: "openai_realtime"
# - provider_output.sample_rate_hz: 24000
# - Look for "Parsed OpenAI session.updated ACK"
```

---

### Scenario 4: Premium Context (OpenAI + 24k Profile)

**Goal**: Test full context mapping with provider + profile override

**Dialplan Setup**:

```
[from-ai-agent-premium]
exten => s,1,NoOp(AI Agent - Premium Service)
  same => n,Set(AI_CONTEXT=premium)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()
```

**Expected**:

- Profile: `openai_realtime_24k` (from context)
- Provider: `openai_realtime` (from context)
- Wire: `slin16` @ 16kHz (downsampled from 24k)
- Provider I/O: `linear16` @ 24kHz
- Greeting: "Welcome to premium service..."
- Prompt: "...premium concierge assistant..."

**Test**:

```bash
# Place call to premium extension
# Verify premium greeting
# Test response style (should be courteous, attentive)
# Verify audio quality (should be wideband)
```

---

### Scenario 5: Profile Override (Advanced)

**Goal**: Test AI_AUDIO_PROFILE direct override

**Dialplan Setup**:

```
[from-ai-agent-custom]
exten => s,1,NoOp(AI Agent - Custom Profile)
  same => n,Set(AI_AUDIO_PROFILE=wideband_pcm_16k)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()
```

**Expected**:

- Profile: `wideband_pcm_16k` (from channel var)
- Provider: `deepgram` (default)
- Wire: `slin16` @ 16kHz
- Provider I/O: `linear16` @ 16kHz

---

## 📊 Success Criteria

### ✅ P1 Complete When

1. **Scenario 1 (Default)**: Clean audio, matches P0 baseline metrics
2. **Scenario 2 (Sales)**: Context greeting/prompt applied, wideband audio works
3. **Scenario 3 (OpenAI)**: Provider switch works, OpenAI voice heard clearly
4. **Scenario 4 (Premium)**: Full context mapping works (provider + profile + prompt)
5. **TransportCard logs**: Present in all scenarios with correct values
6. **No regressions**: P0 golden baseline still passes
7. **Backward compat**: Calls without channel vars still work (legacy profile synthesis)

---

## 🔍 Validation Checklist

After each test call:

```bash
# Collect RCA
bash scripts/rca_collect.sh

# Check TransportCard
grep "TransportCard" logs/remote/rca-*/logs/ai-engine*.log | python3 -m json.tool

# Check for errors
grep -i "error\|failed\|warning" logs/remote/rca-*/logs/ai-engine*.log | grep -i "transport\|profile\|orchestrator"

# Verify metrics
grep "Audio profile resolved" logs/remote/rca-*/logs/ai-engine*.log

# Check audio quality
python3 scripts/wav_quality_analyzer.py logs/remote/rca-*/recordings/*.wav
```

---

## 🐛 Troubleshooting

### Issue: TransportOrchestrator not initializing

**Symptoms**: No "TransportOrchestrator initialized" log

**Check**:

```bash
docker logs ai_engine 2>&1 | grep -i "profile\|orchestrator\|error"
```

**Possible causes**:

- Config parse error (check YAML syntax)
- Missing `profiles` block (should synthesize legacy)
- Import error

**Fix**: Check container logs for Python traceback

---

### Issue: Channel var not recognized

**Symptoms**: AI_PROVIDER set but wrong provider used

**Check**:

```bash
# In Asterisk CLI:
core show channel <CHANNEL> | grep AI_

# Or check engine logs:
docker logs ai_engine | grep "AI_PROVIDER\|AI_CONTEXT\|AI_AUDIO_PROFILE"
```

**Possible causes**:

- Dialplan not setting variable
- Variable set after Stasis entry
- Typo in variable name

**Fix**: Set variable before `Stasis()` call

---

### Issue: Provider capabilities not found

**Symptoms**: Warning "Provider not found for audio profile resolution"

**Check**:

```bash
docker logs ai_engine | grep "providers:"
```

**Possible causes**:

- Provider not initialized (check `enabled: true` in YAML)
- Provider name mismatch (use exact name: `deepgram`, `openai_realtime`)
- Provider init failed earlier

**Fix**: Check provider configuration and credentials

---

## 📝 Next Steps After Validation

1. **Tag Release**: `v1.0-p1-multi-provider`
2. **Update Documentation**: Add multi-provider guide to docs/
3. **Performance Testing**: Load test with multiple providers
4. **P2 Config Cleanup**: Remove diagnostic knobs, add CLI tools

---

## 🎯 Key Files Modified

- `src/core/transport_orchestrator.py` (new)
- `src/providers/deepgram.py` (added `parse_ack()`)
- `src/providers/openai_realtime.py` (added `parse_ack()`)
- `src/engine.py` (orchestrator init + integration)
- `config/ai-agent.yaml` (profiles + contexts blocks)
- `docs/plan/ROADMAPv4.md` (references updated)

---

**Status**: ✅ Ready for deployment and testing

**Estimated Test Time**: 1-2 hours (5 scenarios @ 10-15 min each)

**Risk Level**: Low (backward compatible, graceful error handling)

**Rollback**: Revert to commit `83ff718` (P0 complete) if issues arise
