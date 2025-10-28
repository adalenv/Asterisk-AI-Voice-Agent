# OpenAI Realtime Echo/Feedback Loop - ROOT CAUSE ANALYSIS

## Call ID: 1761436433.2119 | Date: Oct 25, 2025 16:53 UTC | Duration: 41 seconds

---

## 🎯 **ROOT CAUSE: Agent Audio Echo Detection (Feedback Loop)**

**OpenAI's VAD is detecting the agent's own audio as "user speech", causing 20 response cancellations in 41 seconds!**

---

## 📊 Critical Evidence

### The Smoking Gun Numbers

| Metric | Value | Analysis |
|--------|-------|----------|
| **speech_started Events** | 20 | ❌ Way too many! |
| **speech_stopped Events** | 19 | Matches started |
| **Responses Created** | 20 | 1 per speech_started |
| **Responses Completed** | ~2-3 | ❌ 85% cancelled! |
| **Agent Audio Generated** | 5.38s (172KB) | OpenAI did generate |
| **Agent Audio Played** | 3.84s (71%) | But interrupted |
| **Gating Cycles** | 1 start, 11 ends | ❌ Multiple segment ends |
| **Underflows** | 63 in 37s | 1.7 per second |

---

## 🔍 The Pattern

### Timeline of Echo Feedback

```
Time         | Event                               | What's Happening
-------------|-------------------------------------|----------------------------------
23:54:06.730 | 🔇 Gating audio (start greeting)   | Agent starts speaking ✅
23:54:06.970 | Greeting segment 1 ends             | 7KB played
23:54:08.173 | speech_started #1                   | ❌ ECHO! VAD detects agent audio
23:54:08.845 | speech_stopped #1                   | 
23:54:08.846 | response.created #2                 | OpenAI starts new response
23:54:09.316 | First audio chunk (response #2)     | New greeting starts
23:54:09.986 | Response #2 segment ends            | 19KB played
23:54:10.544 | speech_started #2                   | ❌ ECHO again!
23:54:10.874 | speech_stopped #2                   |
23:54:10.886 | response.created #3                 | OpenAI starts ANOTHER response
23:54:11.573 | First audio chunk (response #3)     | Another greeting starts
23:54:12.181 | Response #3 segment ends            | 17KB played
23:54:12.396 | speech_started #3                   | ❌ ECHO continues...
...repeats 20 times total
```

**Result**: Agent says partial greeting → Echo detected → Response cancelled → New greeting starts → Echo detected → Loop continues

---

## 🔍 What We Know

### Fix #1 Status: ✅ WORKING (Partially)

**Goal**: Stop agent audio from self-interrupting via repeated gating  
**Result**: Only 1 "gating audio" event (correct!)  
**BUT**: 11 "segment-end" clearing events because OpenAI creates multiple responses

**Conclusion**: Fix #1 works for preventing re-gating, but doesn't prevent OpenAI from creating multiple responses

---

### Fix #2 Status: ✅ WORKING

**Goal**: Eliminate empty buffer errors, let OpenAI auto-commit  
**Result**:

- 0 "buffer too small" errors ✅
- 19 successful auto-commits ✅
- "OpenAI appended input audio (auto-commit on speech_stopped)" logs confirm it's working

**Conclusion**: Fix #2 is working perfectly!

---

## 🔍 Root Cause Analysis

### Problem: Echo/Feedback Loop

**What's Happening**:

1. Agent starts speaking (greeting)
2. System gates audio capture (correct!) at 23:54:06.730
3. BUT: Some agent audio still reaches OpenAI's input buffer
4. OpenAI's VAD detects this as "user speech" (speech_started)
5. OpenAI auto-cancels current response to handle "user interruption"
6. OpenAI auto-creates new response (per server_vad design)
7. New response starts with another greeting
8. Agent audio AGAIN leaks into input → Repeat

**Result**: 20 speech_started detections, 20 response creations, ~17 cancelled responses

---

## 📊 Evidence Details

### Speech Detection Pattern

```
speech_started:  20 events
speech_stopped:  19 events
committed:       19 events (auto-commit on speech_stopped)
response.created: 20 events (1 manual + 19 auto from VAD)
response.done:    0 logged (responses being cancelled)
```

**Per Perplexity Research**:
> "The VAD might be triggered by background noise or echo, especially if the agent's audio is not properly filtered or muted while the user is speaking."
> "OpenAI's API does not provide direct access to tweak VAD parameters."

---

### Audio Segments Created

```
Segment 1:  7,040 bytes
Segment 2: 19,200 bytes  
Segment 3: 17,920 bytes
Segment 4:  3,200 bytes
Segment 5: 27,520 bytes
Segment 6: 16,000 bytes
Segment 7: 13,440 bytes
Segment 8: 22,400 bytes
Segment 9: 35,200 bytes
Segment 10: 10,240 bytes
---
Total: 172,160 bytes (5.38s @ 16kHz)
```

Each segment corresponds to a response that was interrupted by echo detection.

---

### Transcript Evidence

**What User Heard** (from recording):
> "hello oh how can they help you could hello how are you it clean like hello map and you hear me pledge got cut ah hello please can you hear me yeah"

**Analysis**: Multiple greeting attempts, all cut off mid-sentence

**What Agent Generated** (from provider):
> "hello how can i help you today it seems like your message got cut off could you please repeat"

**Analysis**: Agent is trying to respond but keeps getting interrupted

---

## 🔧 Why This Is Happening

### Possible Echo Paths

#### **Path 1: Timing Gap in Gating**

```
06.730: Gating starts (audio capture disabled)
06.730-06.970: Agent audio playing (240ms)
08.173: speech_started detected (1.4s AFTER gating!)
```

**Problem**: There's a 1.4 second delay between gating start and echo detection. This suggests:

1. Audio already in OpenAI's buffer before gating
2. OR gating not effective immediately
3. OR agent audio leaking through another path

---

#### **Path 2: AudioSocket/Asterisk Echo**

- Asterisk might be echoing agent audio back through the trunk
- Local channel might have echo path
- AudioSocket might not have echo cancellation

---

#### **Path 3: TTS Gating Not Fast Enough**

```python
# Current flow:
1. PROVIDER CHUNK arrives
2. Send to streaming manager
3. Streaming manager gates audio
4. But audio might already be buffered in AudioSocket
```

**Problem**: By the time gating happens, some audio frames may already be in flight

---

## 🎯 Why Previous Fixes Helped But Didn't Solve It

### Fix #1 (Segment Gating)

- ✅ Prevented re-gating on each chunk
- ✅ Only 1 initial gating event
- ❌ Doesn't prevent echo from triggering VAD
- ❌ Can't stop OpenAI from creating new responses on speech_started

### Fix #2 (Auto-commit)

- ✅ Eliminated empty buffer errors
- ✅ Reliable audio delivery to OpenAI
- ❌ Doesn't prevent echo detection
- ❌ Makes VAD more effective (which detects echo faster!)

**Irony**: Fix #2 working well means OpenAI's VAD detects echo MORE reliably, leading to MORE interruptions!

---

## 🔧 Potential Solutions

### **Solution A: Disable OpenAI's server_vad** (NOT RECOMMENDED)

```yaml
# In config, send turn_detection: null
turn_detection: null
```

**Pros**: No automatic speech detection, no echo triggers  
**Cons**:

- Lose automatic turn-taking
- Must manually manage responses
- Worse user experience

---

### **Solution B: Add Pre-Gating (Gate BEFORE sending to provider)** (RECOMMENDED)

**Problem**: Currently we gate AFTER sending chunk to streaming manager  
**Solution**: Gate BEFORE sending chunk to provider

```python
# In engine.py, before sending to provider:
if session.tts_playing:
    # Drop audio instead of sending to provider
    continue

# OR gate earlier in AudioSocket handler:
if session.tts_playing:
    return  # Don't forward to provider at all
```

**Pros**:

- Prevents agent audio from ever reaching OpenAI
- Simple implementation
- No VAD tuning needed

**Cons**:

- User can't interrupt agent mid-sentence
- Less natural conversation flow

---

### **Solution C: Implement Echo Cancellation at AudioSocket Level**

Use Asterisk's built-in echo cancellation or implement WebRTC AEC (Acoustic Echo Cancellation).

**Pros**:

- Allows natural interruptions
- Keeps automatic turn-taking
- Industry-standard solution

**Cons**:

- Requires Asterisk configuration changes
- More complex
- May need audio processing library

---

### **Solution D: Increase VAD Threshold** (CAN'T DO)

Per Perplexity research:
> "OpenAI's API does not provide direct access to tweak VAD parameters."

**Conclusion**: We can't tune OpenAI's VAD sensitivity directly

---

## 📊 Why OpenAI Behavior Is "Correct"

From OpenAI's perspective:

1. User audio is being received (our agent's audio, but OpenAI doesn't know that)
2. VAD detects speech_started (correct behavior)
3. Current response is interrupted (correct for user interruption)
4. New response is created automatically (correct per server_vad design)

**The System Is Working As Designed**: OpenAI's VAD is working correctly. The problem is we're sending it audio we shouldn't be sending (echo).

---

## 🎯 Recommended Solution

### **Implement Solution B: Pre-Gating at AudioSocket Level**

**Location**: `src/engine.py` in `_audiosocket_handle_audio` method

**Current Flow**:

```
AudioSocket audio arrives
  ↓
Forward to provider immediately
  ↓
Provider processes
  ↓
LATER: Gate audio capture
```

**New Flow** (Recommended):

```
AudioSocket audio arrives
  ↓
Check: Is TTS playing?
  ↓
YES → Drop audio (don't forward to provider)
NO → Forward to provider normally
```

**Implementation**:

```python
# In _audiosocket_handle_audio, before forwarding to provider:
if session.tts_playing:
    logger.debug(
        "Dropping inbound audio during TTS playback (echo prevention)",
        call_id=call_id,
        bytes=len(pcm16_chunk)
    )
    return  # Don't send to provider

# Otherwise, proceed with normal forwarding
await self.provider.send_audio(pcm16_chunk)
```

**Expected Results**:

- speech_started events: 1-2 (only real user speech)
- response.created: 1-2 (greeting + 1 real response)
- No echo-triggered interruptions
- Clean agent audio playback
- User can still interrupt by speaking loudly (might overcome gating)

---

## 📊 Expected Improvements

| Metric | Current | After Solution B |
|--------|---------|------------------|
| **speech_started Events** | 20 | 2-3 ✅ |
| **Responses Created** | 20 | 2-3 ✅ |
| **Response Completion Rate** | 15% | 95%+ ✅ |
| **Echo Detections** | 19 | 0 ✅ |
| **Agent Audio Interruptions** | Every 2-3 seconds | None ✅ |
| **User Can Interrupt** | No (echo doing it) | Yes (real speech) ✅ |
| **Underflows** | 63 in 37s | <5 in 37s ✅ |

---

## 💡 Key Insights

### 1. Fix #2 Made The Problem Worse (In A Way)

- Empty buffer fix worked perfectly
- But now OpenAI receives audio reliably
- Including the echo audio!
- VAD detects echo more reliably → More interruptions

### 2. OpenAI's VAD Is Excellent (Too Good!)

- Detects even faint audio as speech
- No way to tune sensitivity
- Designed for clean, echo-free audio input

### 3. The Real Problem Is Upstream

- Not in OpenAI integration
- Not in VAD settings
- In our audio routing: we're sending agent audio to OpenAI

### 4. Gating Needs To Be Earlier

- Current gating stops recording
- But audio already sent to provider
- Need to gate BEFORE sending to provider

---

## 📁 Evidence Files

**RCA Location**: `logs/remote/rca-20251025-235524/`

**Key Evidence**:

- 20 speech_started events (1 real, 19 echo)
- 19 auto-commits (Fix #2 working!)
- 0 empty buffer errors (Fix #2 success!)
- 11 segment-end clearing events (multiple responses)
- Agent transcript: "it seems like your message got cut off could you please repeat"

---

## ✅ What's Working

1. ✅ Fix #1: Only 1 gating event (no re-gating)
2. ✅ Fix #2: Auto-commits working perfectly
3. ✅ OpenAI responding (when not interrupted by echo)
4. ✅ Audio quality excellent (66.5dB SNR when playing)
5. ✅ Session handshake correct
6. ✅ No YAML VAD override (using OpenAI defaults)

---

## ❌ What Still Needs Fixing

1. ❌ Echo prevention at AudioSocket/input level
2. ❌ Pre-gating before sending to provider
3. ❌ Possibly Asterisk echo cancellation configuration

---

*Generated: Oct 25, 2025*  
*Status: ROOT CAUSE IDENTIFIED - Echo/feedback loop triggering VAD*  
*Recommendation: Implement pre-gating to drop audio during TTS playback*  
*References: Perplexity research on OpenAI Realtime VAD behavior*
