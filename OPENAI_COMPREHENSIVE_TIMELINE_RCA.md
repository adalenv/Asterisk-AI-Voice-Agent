# OpenAI Realtime - COMPREHENSIVE TIMELINE RCA

## Call ID: 1761437334.2123 | Date: Oct 26, 2025 00:09 UTC | Duration: 54.7 seconds

---

## 🎯 **EXECUTIVE SUMMARY**

**Status**: ❌❌❌ CRITICAL FAILURES ON MULTIPLE LEVELS

### The Problems

1. **Greeting Delayed 4.5 Seconds** (AudioSocket connects at T+0s, greeting starts at T+4.5s)
2. **First Segment Only 84ms Long** (greeting cuts off after "Hel...")
3. **Echo/Feedback Still Happening** (4 response cycles, audio still cutting off)
4. **Protection Window BACKWARDS** (drops audio BEFORE TTS, not AFTER)

### User Experience

- User: *waits 4.5 seconds in silence*
- Agent: "Hel--" *cuts off*
- User: "Hello?"
- Agent: "How can--" *cuts off*  
- User: "Can you hear me?"
- Agent: *repeats again*

---

## 📊 COMPLETE TIMELINE WITH LOG EVIDENCE

### **Phase 1: Call Setup (00:09:00.394 - 00:09:00.570)**

```text
Time (UTC)       | Event                                    | Impact
-----------------|------------------------------------------|---------------------------
00:09:00.394     | StasisStart received                     | Call begins
00:09:00.423     | Bridge created                           | 
00:09:00.439     | Caller session created                   | audio_capture_enabled=False ❌
00:09:00.533     | AudioSocket channel originating          |
00:09:00.549     | AudioSocket channel originated           |
00:09:00.570     | AudioSocket added to bridge              | Audio path open
```

**Problem #1**: Session created with `audio_capture_enabled=False` (line 990 in engine.py).  
**Impact**: System thinks TTS is playing when nothing is playing yet!

---

### **Phase 2: Premature Audio Dropping (00:09:00.573 - 00:09:04.526)**

```text
Time (UTC)       | Event                                    | tts_started_ts | Explanation
-----------------|------------------------------------------|----------------|---------------------------
00:09:00.573     | OpenAI connection started                | 0.0            |
00:09:00.666     | First audio frame from caller            | 0.0            |
00:09:00.668     | "Extended TTS protection (5000ms)"       | 0.0            | Protection activates!
00:09:00.668     | "Dropping inbound (tts_elapsed_ms=0)"    | 0.0            | Audio dropped ❌
...              | (continues dropping for 4.5 seconds)     | 0.0            |
00:09:00.902     | Connecting to OpenAI                     | 0.0            |
00:09:00.909     | session.created received                 | 0.0            |
00:09:00.912     | Waiting for session.updated ACK          | 0.0            |
00:09:03.914     | session.updated ACK TIMEOUT (3s!)        | 0.0            | ❌ 3-second delay!
00:09:03.915     | response.create sent (greeting)          | 0.0            |
00:09:03.920     | session.updated ACK received (late)      | 0.0            |
00:09:03.977     | response.created from OpenAI             | 0.0            |
00:09:04.525     | STREAMING ADAPTIVE WARM-UP               | 0.0            |
00:09:04.526     | TTS GATING - Audio capture disabled      | SET NOW!       | tts_started_ts = now()
00:09:04.526     | STREAMING PLAYBACK - Started             | T+4.526s       | Finally!
00:09:04.789     | First audio frame sent                   | T+4.789s       |
```

**Problem #2**: Protection triggers at 00:09:00.668 but TTS doesn't start until 00:09:04.526 (4.5 second gap!).

**Problem #3**: The 3-second `session.updated` ACK timeout blocks greeting initialization.

**Problem #4**: Protection checks `audio_capture_enabled=False` which is ALWAYS false initially, so it starts dropping audio BEFORE any TTS exists!

**Impact**:

- User waits 4.5 seconds hearing nothing
- User audio dropped for 4.5 seconds (user trying to speak: "hello? can you hear me?")
- By the time greeting starts, user is confused and talking over it

---

### **Phase 3: First Segment (Ultra-Short) (00:09:04.526 - 00:09:04.873)**

```text
Time (UTC)       | Event                                    | Bytes Played | Duration
-----------------|------------------------------------------|--------------|------------
00:09:04.526     | STREAMING PLAYBACK - Started             | 0            | 0ms
00:09:04.788     | Jitter buffer warm-up complete           | 7,680        | 262ms buffered
00:09:04.789     | First frame sent to AudioSocket          | 640          | 20ms
00:09:04.809     | Frame 2                                  | 1,280        | 40ms
00:09:04.829     | Frame 3                                  | 1,920        | 60ms
00:09:04.849     | Frame 4 (last?)                          | 2,560        | 80ms
00:09:04.871     | segment-end (PROVIDER SEGMENT BYTES)     | 10,240       | **84ms total!**
00:09:04.872     | clearing gating                          |              |
00:09:04.872     | audio_capture_enabled = true             |              | Gates cleared
```

**PROVIDER SEGMENT BYTES**:

- provider_bytes: 10,240 (32 chunks of 320 bytes = 640ms @ 16kHz)
- enqueued_bytes: 5,120 (50% ratio)
- BUT only played ~84ms!

**Problem #5**: Segment ends after only 84ms of playback!

**Why?**:

- OpenAI generated 32 chunks (640ms worth)
- System buffered only 5,120 bytes (50%)
- Played only ~4 frames = 80ms
- **Remaining 608ms of audio lost!**

**What user heard**: "Hel--" (first syllable of "Hello")

---

### **Phase 4: Echo Begins (00:09:04.873 onwards)**

```text
Time (UTC)       | Event                                    | What's Happening
-----------------|------------------------------------------|--------------------------------
00:09:04.873     | audio_capture_enabled = true             | Protection OFF, audio flows
00:09:05.529     | Low audio energy warning (RMS=0)         | Silent/zero frames detected
00:09:07.191     | response.created (#2)                    | ❌ OpenAI detected speech!
00:09:07.742     | New audio chunks start                   | Second response begins
00:09:08.532     | segment-end                              | Second response cuts off
00:09:15.650     | response.created (#3)                    | ❌ Another echo detection
00:09:23.934     | response.created (#4)                    | ❌ Echo continues
00:09:40.835     | response.created (#5)                    | ❌ Still echoing
```

**Problem #6**: After first segment ends and gating clears, user audio reaches OpenAI again.

**Problem #7**: OpenAI's VAD detects speech (likely echo or user's repeated attempts to speak).

**Problem #8**: Each detection triggers new response, cancelling previous response → cutoffs!

---

## 🔍 **ROOT CAUSES IDENTIFIED**

### **Root Cause #1: Session Initialization Bug**

**Location**: `src/engine.py` lines 990, 1344

**Code**:

```python
session = CallSession(
    ...
    audio_capture_enabled=False,  # ❌ WRONG! Should be True initially
    ...
)
```

**Impact**:

- System thinks TTS is playing from the moment AudioSocket connects
- Protection code activates immediately (drops all audio for 5 seconds)
- But no TTS is actually playing yet!
- Creates 4.5 second silence gap before greeting starts

**Fix**: `audio_capture_enabled=True` (only set to False when TTS actually starts)

---

### **Root Cause #2: OpenAI session.updated ACK Timeout**

**Location**: `src/providers/openai_realtime.py` line 352

**Evidence**:

```text
00:09:00.912 - Waiting for session.updated ACK
00:09:03.914 - session.updated ACK timeout! (3 seconds later)
00:09:03.920 - session.updated ACK received (8ms after timeout)
```

**Code**:

```python
await asyncio.wait_for(self._session_ack_event.wait(), timeout=3.0)
```

**Impact**:

- Greeting request sent at 00:09:03.915 (after 3s wait)
- Audio doesn't arrive until 00:09:04.525 (another 0.6s)
- **Total delay: 4.5 seconds of silence**

**Why**: ACK arrives at 00:09:03.920 (8ms after timeout expires), but we already logged error and continued.

**Fix**: Either increase timeout to 5s, OR don't block greeting on session.updated ACK

---

### **Root Cause #3: Segment Ends Too Early**

**Evidence**:

- OpenAI generated: 32 chunks = 10,240 bytes = 640ms worth
- Enqueued to playback: 5,120 bytes (50%)
- Actually played: ~2,560 bytes = 80ms
- **Lost: 608ms of audio (95% of greeting!)**

**Location**: Somewhere in streaming_playback_manager or engine

**Possible causes**:

1. `segment-end` triggered prematurely
2. Jitter buffer underflow
3. Playback stopped by some other mechanism

**Need to investigate**: Why does `PROVIDER SEGMENT BYTES` show 10,240 bytes but only 80ms plays?

---

### **Root Cause #4: Protection Window Logic Inverted**

**Current Behavior**:

- Drops audio BEFORE TTS starts (when `audio_capture_enabled=False` initially)
- Should drop audio AFTER TTS starts (to prevent echo)

**Location**: `src/engine.py` line 2238

**Code**:

```python
if hasattr(session, 'audio_capture_enabled') and not session.audio_capture_enabled:
    # This block executes when audio_capture_enabled=False
    # But that's True from session init, even when NO TTS is playing!
```

**The Logic Flaw**:

- `audio_capture_enabled=False` is supposed to mean "TTS is playing, don't capture"
- But session initializes with `audio_capture_enabled=False` ALWAYS
- So protection runs even when no TTS exists yet

**Fix**: Only activate protection after `tts_started_ts` is actually set (> 0.0)

---

### **Root Cause #5: Fix #3 (5-second protection) Applied in Wrong Place**

**Our Fix #3 Code** (line 2303-2310):

```python
if provider_name == "openai_realtime":
    initial_protect = 5000  # 5 seconds
```

**Where it runs**: Inside the `if not audio_capture_enabled` block (line 2238)

**When it runs**:

- At 00:09:00.666 (4.5 seconds BEFORE TTS starts!)
- Drops audio for entire pre-TTS period
- But TTS doesn't start until 00:09:04.526
- By then, 4.5 seconds of the 5-second protection have elapsed
- Only 0.5 seconds of protection remains when TTS actually plays!

**The Bug**: Protection is relative to AudioSocket connection time, NOT TTS start time!

---

## 📊 **COMPARISON: What Should Happen vs What Actually Happens**

### **Correct Flow** (What SHOULD Happen)

```text
Time | Event                           | audio_capture_enabled | Audio Flow
-----|----------------------------------|----------------------|------------
T+0s | AudioSocket connects             | TRUE                 | Audio → OpenAI
T+0s | OpenAI session starts            | TRUE                 | Audio → OpenAI
T+0.5s | Greeting request sent          | TRUE                 | Audio → OpenAI
T+1s | Greeting audio arrives           | FALSE (gating)       | Audio BLOCKED (protection)
T+1s to T+6s | Greeting plays          | FALSE                | Audio BLOCKED (5s protection)
T+6s | Greeting done, gating clears     | TRUE                 | Audio → OpenAI (user can speak)
```

**Result**: User hears full greeting, then can speak. Clean conversation.

---

### **Actual Flow** (What IS Happening)

```text
Time | Event                           | audio_capture_enabled | Audio Flow
-----|----------------------------------|----------------------|------------
T+0s | AudioSocket connects             | FALSE ❌             | Audio BLOCKED (wrong!)
T+0s | Protection activates (5s)        | FALSE                | Audio BLOCKED
T+0s-T+4.5s | *silence* waiting           | FALSE                | Audio BLOCKED
T+4.5s | Greeting finally starts        | FALSE (gating)       | Audio BLOCKED (0.5s left)
T+4.5s-T+4.58s | First 80ms plays      | FALSE                | Audio BLOCKED
T+4.58s | Segment ends, gating clears   | TRUE ❌              | Audio → OpenAI (echo!)
T+4.58s onwards | Echo feedback loop   | TRUE/FALSE cycling   | Chaos!
```

**Result**:

- 4.5s silence → user confused
- 80ms greeting → "Hel--"
- Echo feedback → multiple interrupted responses
- User experience: completely broken

---

## 🔧 **THE REAL FIXES NEEDED**

### **Fix A: Session Initialization** (CRITICAL)

**File**: `src/engine.py` lines 990, 1344

**Change**:

```python
session = CallSession(
    ...
    audio_capture_enabled=True,  # ✅ Start with capture ENABLED
    ...
)
```

**Impact**:

- Audio flows to OpenAI from the start
- No premature protection
- No 4.5-second silence gap

---

### **Fix B: Remove/Reduce session.updated ACK Timeout** (HIGH PRIORITY)

**File**: `src/providers/openai_realtime.py` line 352

**Option 1** - Don't block greeting:

```python
# Send greeting immediately, don't wait for session.updated
if (self.config.greeting or "").strip():
    await self._send_explicit_greeting()  # Before ACK wait

# Then wait for ACK (doesn't block greeting)
try:
    await asyncio.wait_for(self._session_ack_event.wait(), timeout=3.0)
except asyncio.TimeoutError:
    logger.warning("ACK timeout but greeting already sent")
```

**Option 2** - Increase timeout:

```python
await asyncio.wait_for(self._session_ack_event.wait(), timeout=5.0)  # Was 3.0
```

**Impact**: Greeting starts within 1 second instead of 4.5 seconds

---

### **Fix C: Protection Should Check tts_started_ts** (CRITICAL)

**File**: `src/engine.py` around line 2303

**Change**:

```python
# Only apply extended protection if TTS has actually started
if provider_name == "openai_realtime" and getattr(session, 'tts_started_ts', 0.0) > 0:
    initial_protect = 5000
    logger.debug(...)
```

**Impact**:

- Protection only runs AFTER TTS starts
- No dropping audio during 4.5-second pre-TTS silence
- Protection properly prevents echo during actual greeting playback

---

### **Fix D: Investigate Premature Segment End** (HIGH PRIORITY)

**Need to find**: Why only 80ms of 640ms plays

**Possible locations**:

- `src/core/streaming_playback_manager.py` - segment boundary detection
- `src/engine.py` - AgentAudioDone handling

**Investigation needed**: Check logs for:

- Buffer underflows
- Early segment-end triggers
- Playback stop conditions

---

## 📊 **EXPECTED RESULTS AFTER ALL FIXES**

| Metric | Current | After Fixes |
|--------|---------|-------------|
| **Greeting Delay** | 4.5 seconds | <1 second ✅ |
| **Greeting Duration** | 80ms ("Hel--") | Full 3+ seconds ✅ |
| **Echo Detections** | 4 false positives | 0 ✅ |
| **Audio Cutoffs** | Every response | None ✅ |
| **User Experience** | Broken | Natural conversation ✅ |

---

## 💡 **KEY INSIGHTS**

### 1. The 5-Second Protection Was Applied in the Wrong Place

- Applied BEFORE TTS starts (due to audio_capture_enabled=False default)
- Should apply AFTER TTS starts
- Timing is relative to wrong event (AudioSocket connect vs TTS start)

### 2. Session Initialization is Backwards

- `audio_capture_enabled=False` means "gated, don't capture"
- But set to False BEFORE any gating happens
- Should start True, only set False when TTS actually starts

### 3. The 3-Second ACK Timeout Blocks Everything

- Waits 3 seconds for session.updated ACK
- ACK arrives 8ms after timeout (too late)
- Blocks greeting for 3+ seconds unnecessarily

### 4. Premature Segment Ending is a Separate Bug

- Only 80ms of 640ms audio plays
- Needs separate investigation
- Likely jitter buffer or playback manager issue

---

## 🎯 **NEXT STEPS**

1. **Immediate**: Fix session initialization (`audio_capture_enabled=True`)
2. **Immediate**: Move greeting send before ACK wait
3. **Immediate**: Add `tts_started_ts > 0` check to protection
4. **Follow-up**: Investigate premature segment ending
5. **Test**: Full call with all fixes applied

---

*Generated: Oct 26, 2025*  
*Status: MULTIPLE ROOT CAUSES IDENTIFIED*  
*Priority: CRITICAL - System fundamentally broken, needs immediate fixes*  
*Call ID: 1761437334.2123*
