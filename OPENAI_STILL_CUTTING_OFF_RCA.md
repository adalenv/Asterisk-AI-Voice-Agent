# OpenAI Realtime Still Cutting Off - Final Root Cause Analysis

## Call ID: 1761432876.2107 | Date: Oct 25, 2025 15:54 UTC

---

## 🎯 **CRITICAL FINDING: Double Cancellation Problem**

### ✅ What's Fixed

1. **session.created handshake** - Working correctly!

```text
   "Waiting for session.created from OpenAI..."
   "✅ Received session.created - session ready"
   "session_id": "sess_CUhRnrlirgyR67cGcslB0"
```

1. **Format ACK** - Working correctly!

```text
   "✅ OpenAI session.updated ACK received"
   "output_format": "pcm16"
   "acknowledged": true
```

### ❌ What's Still Broken

**TWO systems are cancelling responses simultaneously!**

---

## 🔍 The Two Cancellation Systems

### Problem #1: OpenAI VAD Too Sensitive (from YAML)

**Evidence**:

```log
"Using custom turn_detection config from YAML"
"threshold": 0.5
"silence_ms": 200
```

**From ai-agent.yaml (lines 276-279)**:

```yaml
turn_detection:
  type: "server_vad"
  silence_duration_ms: 200  # ❌ WAY TOO SHORT! (default is 500ms)
  threshold: 0.5  # Default sensitivity
```

**Why This Causes Cutoffs**:

- 200ms silence = OpenAI thinks user stopped speaking after 0.2 seconds
- Any pause in agent's speech triggers "user must be talking"
- OpenAI cancels response and creates new one
- Result: Agent never finishes sentences

**Response Pattern**:

```text
22:54:50 - response.created #1
22:54:52 - speech_started (echo detected)
22:54:52 - response.created #2 (cancelled #1)
22:54:57 - speech_started (echo detected)  
22:54:57 - response.created #3 (cancelled #2)
22:54:59 - response.created #4 (cancelled #3)
22:55:00 - response.created #5 (cancelled #4)
...
Total: 14 responses created, 0 completed
```

---

### Problem #2: Engine-Level Barge-In (from our code)

**Evidence**:

```log
🎧 BARGE-IN triggered
"energy": 15034
"vad_speech": false  ← No speech detected
"webrtc": false      ← WebRTC VAD not triggered
```

**8 Barge-In Events During Call**:

```text
22:54:51 - BARGE-IN triggered (energy: 15034)
22:54:53 - BARGE-IN triggered (energy: 13572)
22:54:58 - BARGE-IN triggered (energy: 14134)
22:55:02 - BARGE-IN triggered (energy: 13579)
22:55:08 - BARGE-IN triggered (energy: 15312)
22:55:19 - BARGE-IN triggered (energy: 15415)
22:55:30 - BARGE-IN triggered (energy: 14367)
22:55:36 - BARGE-IN triggered (energy: 15349)
```

**Why This Triggers**:

- Engine detects audio energy (high RMS from caller)
- Assumes user is speaking
- Stops agent playback
- Clears audio queue
- Result: Agent sentence cut off mid-word

---

## 📊 The Numbers

| Metric | Value | Status |
|--------|-------|--------|
| **session.created** | ✅ Received | FIXED |
| **Format ACK** | ✅ Received | FIXED |
| **Responses Created** | 14 | Normal |
| **Responses Completed** | 0 | ❌ BROKEN |
| **speech_started Events** | 24+ | ❌ TOO MANY |
| **Barge-In Events** | 8 | ❌ TOO MANY |
| **Buffer Underflows** | 78 in 46s | ❌ 1.7/sec |
| **Audio Played** | 3.9s / 50s | ❌ Only 7.8% |
| **Audio Quality** | 65.5dB SNR | ✅ Perfect |

---

## 🔄 The Cancellation Flow (What's Happening)

```text
1. OpenAI starts response → response.created
2. Sends audio chunks → Agent speaking
3. Agent's audio echoes back to input
4. OpenAI VAD: "speech after 200ms silence!" 
5. OpenAI cancels response → response.created (new)
6. Meanwhile, engine detects audio energy
7. Engine: "BARGE-IN triggered!"
8. Engine stops playback, clears buffer
9. Buffer underflow occurs
10. Repeat from step 1
```

**Result**: Neither system allows response to complete!

---

## 🎯 Root Causes Identified

### Root Cause #1: YAML turn_detection Too Aggressive

**Configuration** (lines 276-279 of ai-agent.yaml):

```yaml
turn_detection:
  type: "server_vad"
  silence_duration_ms: 200  # ❌ CRITICAL: Too short!
  threshold: 0.5            # Default
```

**Why It's Wrong**:

- 200ms = 0.2 seconds of silence
- Agent natural pauses are ~300-500ms
- Any natural pause triggers "user speaking"
- OpenAI cancels response prematurely

**Comparison**:

- **Current**: 200ms silence
- **OpenAI Default**: 500ms silence
- **Recommended**: 1000-1500ms for telephony with echo

---

### Root Cause #2: Engine Barge-In Too Aggressive

**What's Happening**:

```log
"energy": 15034
"criteria_met": 1
"vad_speech": false  ← NO actual speech!
```

**The Problem**:

- Engine uses energy threshold for barge-in
- High background noise on caller side (SNR 41.1dB, not great)
- Constant audio energy from line noise
- Engine thinks "user speaking"
- Stops agent playback

**Evidence from Caller Audio**:

```text
caller_inbound.wav: SNR 41.2dB (low)
caller_to_provider.wav: SNR 41.1dB (low)
Mean RMS: 14283 (high noise floor)
```

---

## 📋 Timeline Analysis

### Typical Cutoff Sequence

```text
Time     | OpenAI Event              | Engine Event           | Result
---------|---------------------------|------------------------|------------------
00:00.0  | response.created #1       |                        | Agent starts
00:00.5  | Sending audio...          | Playing audio          | "Hello, how can..."
00:02.0  | speech_started            |                        | OpenAI detects
00:02.3  | response.created #2       |                        | Cancel #1, start #2
00:02.5  |                           | BARGE-IN triggered     | Engine stops
00:02.6  | Underflow                 | Buffer empty           | Silence
00:05.0  | speech_started            |                        | Cycle repeats
00:05.3  | response.created #3       |                        | Cancel #2
```

**User Experience**: "Hello, how can... [CUT]... today... [CUT]... help... [CUT]"

---

## 🔧 Required Fixes (No Implementation - Analysis Only)

### Fix #1: Remove or Adjust YAML turn_detection

#### Option A: Remove Entirely (Recommended)

```yaml
# Comment out or delete lines 276-279
# turn_detection:
#   type: "server_vad"
#   silence_duration_ms: 200
#   threshold: 0.5
```

**Why**: Let OpenAI use its optimized defaults

#### Option B: Make Much Less Sensitive

```yaml
turn_detection:
  type: "server_vad"
  silence_duration_ms: 1500  # 1.5 seconds (was 200ms)
  threshold: 0.8            # Higher = less sensitive (was 0.5)
  prefix_padding_ms: 500    # Wait 500ms before considering speech
```

**Why**: Prevents false detections from echo/noise

---

### Fix #2: Disable Engine Barge-In for OpenAI Realtime

**The Problem**:

- Engine's barge-in logic is for traditional TTS
- OpenAI Realtime handles its own interruption
- Double-cancellation occurring

**Solution** (code location analysis only):

```python
# In engine.py, around line 2383-2400
# When provider is openai_realtime:
if provider_name == "openai_realtime":
    # OpenAI handles interruption internally
    # Don't trigger barge-in at engine level
    pass  # Skip barge-in logic
else:
    # Traditional providers need engine-level barge-in
    trigger_barge_in()
```

**Why**: OpenAI's turn_detection already handles interruption

---

### Fix #3: Increase Barge-In Threshold (If Keeping It)

**Current Issue**:

```text
Energy threshold too low
High background noise (RMS 14283)
False positives on line noise
```

**If keeping engine barge-in**:

- Increase energy threshold
- Add VAD requirement (vad_speech must be true)
- Longer detection window
- Only for non-realtime providers

---

## 📊 Expected Improvements

| Metric | Current | After Fix #1 | After Fix #1+#2 |
|--------|---------|--------------|-----------------|
| **Responses Completed** | 0% | 60-70% | 95%+ |
| **Barge-In Events** | 8 in 50s | 8 in 50s | 0-1 in 50s |
| **Response Cancellations** | 14 in 50s | 2-3 in 50s | 0-1 in 50s |
| **Audio Time** | 7.8% | 30% | 45% |
| **Underflows** | 78 in 46s | 20 in 46s | <5 in 46s |

---

## 🔍 Evidence Summary

### ✅ What's Working

1. **Proper Handshake**

```text
   ✅ session.created received and logged
   ✅ session.update sent AFTER session.created
   ✅ Configuration honored by OpenAI
```

1. **Format Negotiation**

```text
   ✅ PCM16@24kHz requested and confirmed
   ✅ Audio quality excellent (65.5dB SNR)
   ✅ No format mismatches
```

1. **Basic Functionality**

```text
   ✅ Audio path working
   ✅ Two-way communication
   ✅ OpenAI generating responses
```

### ❌ What's Broken

1. **YAML Configuration**

```text
   ❌ turn_detection.silence_duration_ms: 200 (too short)
   ❌ Causes premature speech detection
   ❌ 14 response cancellations
```

1. **Engine Barge-In**

```text
   ❌ 8 false barge-in triggers
   ❌ Based on energy only, no speech detected
   ❌ Stops playback prematurely
```

1. **Double Cancellation**

```text
   ❌ OpenAI cancels due to VAD
   ❌ Engine cancels due to barge-in
   ❌ No response completes
```

---

## 💡 Key Insights

### 1. The Fix Changed the Problem

- **Before**: Configuration not applied (sent before session.created)
- **After**: Configuration IS applied, but it's TOO aggressive
- **Now**: Need to fix the configuration itself

### 2. YAML Config is the Culprit

- 200ms silence is impossibly short
- Any natural pause triggers detection
- This was hidden when config wasn't being applied

### 3. Double-Cancellation

- OpenAI's turn_detection + Engine's barge-in = chaos
- Both systems fighting each other
- Need to pick ONE system

### 4. OpenAI Should Handle It

- OpenAI Realtime designed for this
- Has internal turn handling
- Engine should trust it

---

## 📝 Recommended Action Plan

### Immediate (5 min)

1. Comment out turn_detection in YAML (lines 276-279)
1. Deploy and test
1. Expected: 70% improvement

### If Still Issues (15 min)

1. Disable engine barge-in for openai_realtime
1. Deploy and test
1. Expected: 95% improvement

### Polish (30 min)

1. Tune any remaining sensitivity
1. Add provider-specific barge-in logic
1. Document final configuration

---

## 📁 Evidence Files

**RCA Location**: `logs/remote/rca-20251025-225648/`

**Key Evidence**:

- session.created: ✅ Received at 22:54:47.182667Z
- turn_detection: ❌ threshold 0.5, silence 200ms (too aggressive)
- Responses: 14 created, 0 completed
- Barge-ins: 8 triggered, all false positives
- Underflows: 78 in 46 seconds
- Audio quality: 65.5dB SNR (excellent when playing)

**Config**: `logs/remote/rca-20251025-225648/config/ai-agent.yaml`

- Lines 276-279: turn_detection configuration (the problem)

---

## ✅ Success Criteria

After fixes, expect:

- [ ] turn_detection removed or adjusted in YAML
- [ ] No more 200ms silence detection
- [ ] Engine barge-in disabled for openai_realtime
- [ ] Responses complete (response.done events logged)
- [ ] <5 underflows per minute
- [ ] 40-50% audio time (vs current 7.8%)
- [ ] 95%+ response completion rate

---

*Generated: Oct 25, 2025*  
*Status: CRITICAL - Two independent cancellation systems fighting each other*  
*Action Required: Fix YAML config + disable engine barge-in for OpenAI*
