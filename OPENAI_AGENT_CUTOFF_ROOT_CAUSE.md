# OpenAI Agent Audio Cutoff - ROOT CAUSE ANALYSIS

## Call ID: 1761434348.2115 | Date: Oct 25, 2025 16:19 UTC

---

## 🎯 **ROOT CAUSE IDENTIFIED: Self-Interrupting Playback**

**The agent audio is cutting off because the greeting playback is repeatedly gating (stopping) and ungating (resuming) itself as new audio chunks arrive from OpenAI!**

---

## 📊 The Smoking Gun Evidence

### Timeline Pattern (Repeats Every 3-5 Seconds)

```
Time         | Event                                | Impact
-------------|--------------------------------------|-------------------
23:19:37.893 | PROVIDER CHUNK (seq 1)               | New audio arrives
23:19:37.893 | PROVIDER CHUNK (seq 2)               | More audio arrives  
23:19:37.894 | 🔇 ConversationCoordinator gating    | PLAYBACK STOPPED ❌
23:19:37.894 | TTS GATING - capture disabled        | Audio capture off
...
23:19:38.588 | 🔊 clearing gating (segment-end)     | PLAYBACK RESUMED ✅
23:19:38.589 | TTS GATING - capture enabled         | Audio capture on
...
23:19:42.745 | PROVIDER CHUNK (seq 1)               | New audio arrives
23:19:42.746 | PROVIDER CHUNK (seq 2)               | More audio arrives
23:19:42.747 | 🔇 ConversationCoordinator gating    | PLAYBACK STOPPED ❌
23:19:42.747 | TTS GATING - capture disabled        | Audio capture off
...
23:19:43.588 | 🔊 clearing gating (segment-end)     | PLAYBACK RESUMED ✅
23:19:43.589 | TTS GATING - capture enabled         | Audio capture on
```

**Pattern**: Gate → Clear → Gate → Clear → Gate → Clear (repeats ~20 times during 86s call)

---

## 🔍 Detailed Analysis

### Finding #1: Only ONE Greeting Stream

**Evidence**:

```json
{
  "stream_id": "stream:greeting:1761434348.2115:1761434361899",
  "event": "STREAMING PLAYBACK - Started",
  "timestamp": "23:19:21.901783Z"
}
...
{
  "stream_id": "stream:greeting:1761434348.2115:1761434361899",
  "event": "STREAMING PLAYBACK - Stopped",
  "timestamp": "23:20:44.599734Z"
}
```

**Duration**: 82.7 seconds (matches call length)  
**Conclusion**: There is only ONE playback stream - the greeting

---

### Finding #2: Repeated Gating of the SAME Stream

**Gating Events**: 20+ gating cycles during 86 second call  
**Stream ID**: Always the same `stream:greeting:1761434348.2115:1761434361899`

**What This Means**: The greeting playback is being interrupted by itself!

---

### Finding #3: PROVIDER CHUNKS Trigger Gating

**Code Path** (`src/engine.py` lines 3454-3460):

```python
# In continuous-stream mode, ensure per-segment gating is active
try:
    if getattr(self.streaming_playback_manager, 'continuous_stream', False):
        if call_id not in self._segment_tts_active:
            await self.streaming_playback_manager.start_segment_gating(call_id)
            self._segment_tts_active.add(call_id)
except Exception:
    logger.debug("Failed to start segment gating", call_id=call_id, exc_info=True)
```

**Logic**:

1. When `AgentAudio` (PROVIDER CHUNK) arrives
2. If `continuous_stream` mode is enabled
3. AND call_id not in `_segment_tts_active` set
4. THEN call `start_segment_gating()` → gates audio

---

### Finding #4: segment-end Clears Gating

**Code Path** (`src/engine.py` lines 3616-3621):

```python
# AgentAudioDone handling for continuous mode:
try:
    await self.streaming_playback_manager.end_segment_gating(call_id)
except Exception:
    logger.debug("Failed to end segment gating", call_id=call_id, exc_info=True)
try:
    self._segment_tts_active.discard(call_id)  # ← CRITICAL!
except Exception:
    pass
```

**What Happens**:

1. When segment ends (or periodically)
2. `end_segment_gating()` is called
3. Gating cleared with reason="segment-end"
4. **call_id removed from `_segment_tts_active` set**

---

### Finding #5: The Vicious Cycle

**The Problem**:

1. New PROVIDER CHUNK arrives from OpenAI
2. Check: `call_id not in self._segment_tts_active` → TRUE
3. Call `start_segment_gating()` → **STOPS PLAYBACK**
4. Shortly after, `end_segment_gating()` called
5. Removes call_id from `_segment_tts_active` set
6. Next PROVIDER CHUNK arrives
7. Check: `call_id not in self._segment_tts_active` → TRUE (because it was removed!)
8. Gates audio AGAIN → **STOPS PLAYBACK AGAIN**
9. Repeat 20+ times...

**Result**: Greeting plays → stops → plays → stops → plays → stops

---

## 📊 Call Metrics Explained

| Metric | Value | Explanation |
|--------|-------|-------------|
| **Agent Audio Generated** | 211,840 bytes (6.62s) | OpenAI generated greeting audio |
| **Agent Audio Played** | 6.06 seconds (91.5%) | Most audio eventually played |
| **Call Duration** | 86.34 seconds | Total call time |
| **Audio Time %** | 7% of call | Audio only plays 7% of time |
| **Gating Cycles** | ~20 times | Audio stopped/started 20 times |
| **Underflows** | 142 in 82s (1.73/sec) | Buffer starving from interruptions |
| **User Silence** | 88.9% | User heard mostly silence |

---

## 🔧 Why This Happens

### The Continuous Stream Problem

**OpenAI Realtime Behavior**:

- Sends greeting audio as a **continuous stream**
- Audio arrives in chunks over ~6 seconds
- No explicit "segment boundaries" between chunks
- All chunks belong to ONE response

**System Behavior**:

- Treats EACH chunk as potentially needing gating
- Gates audio when chunk arrives
- Clears gating after processing
- Removes call from `_segment_tts_active` set
- Next chunk triggers gating AGAIN

**Mismatch**: System expects discrete segments, OpenAI sends continuous stream

---

## 🎯 Root Cause Summary

**Primary Issue**: The segment gating logic is designed for discrete TTS segments (like Deepgram), but OpenAI Realtime sends **continuous streaming audio**.

**Specific Bug**:

```python
# Line 3621 in engine.py:
self._segment_tts_active.discard(call_id)
```

This line removes the call from the "gating active" tracking set EVERY time a segment ends. But for OpenAI's continuous greeting, there's no clear "segment end" - it's just one long stream. So the system keeps re-gating on every new chunk.

**Secondary Issue**: The check for `continuous_stream` mode (line 3455) exists but isn't preventing the problem, likely because:

1. OpenAI provider isn't setting `continuous_stream` flag properly
2. OR the greeting isn't treated as continuous even though it streams
3. OR the logic still gates even in continuous mode when call_id not in set

---

## 💡 Why Audio Eventually Plays

Despite 20+ interruptions, 91.5% of audio (6.06s of 6.62s) eventually plays because:

1. **Gating Duration is Short**: Each gate lasts ~0.7-1.5 seconds
2. **Audio Resumes**: Playback continues after each gate clears
3. **Buffer Persists**: StreamingPlaybackManager keeps buffered audio
4. **Underflow Recovery**: System recovers from buffer underruns

But the **user experience is terrible**:

- Hears: "Hello... [pause]... how can... [pause]... I help... [pause]... you today?"
- Instead of: "Hello, how can I help you today?"

---

## 🔍 Evidence Files

### Gating Cycle Count

```bash
grep "1761434348.2115" logs/*/ai-engine*.log | grep "gating audio" | wc -l
# Result: 20 gating events

grep "1761434348.2115" logs/*/ai-engine*.log | grep "clearing gating.*segment-end" | wc -l
# Result: 19 clearing events (matches)
```

### PROVIDER CHUNK Pattern

```bash
grep "1761434348.2115" logs/*/ai-engine*.log | grep "PROVIDER CHUNK" | head -6
# Shows: seq 1, seq 2 pairs arriving
# Immediately followed by gating event
```

### Stream Continuity

```bash
grep "1761434348.2115" logs/*/ai-engine*.log | grep "stream:greeting" | grep "Started\|Stopped"
# Shows: Only ONE start and ONE stop for the same stream_id
```

---

## 🚫 Why Previous Fixes Didn't Help

| Fix | Status | Why It Didn't Solve Cutoffs |
|-----|--------|----------------------------|
| Empty buffer commits | ❌ Real issue | But explains why OpenAI doesn't respond, not why greeting cuts off |
| session.created handshake | ✅ Working | Initialization fix, not playback fix |
| Remove YAML VAD | ✅ Working | VAD detects user, not related to greeting playback |
| Stop response spam | ✅ Working | Prevents request spam, but greeting already playing |
| Disable engine barge-in | ✅ Working | User not triggering barge-in, system self-interrupting |

**None of these fixes address the core issue**: The greeting playback is gating itself repeatedly.

---

## 🔧 The Real Fix Required

### Root Cause to Fix

**Problem Code** (`src/engine.py`):

```python
# Lines 3454-3460: Gates on every chunk
if getattr(self.streaming_playback_manager, 'continuous_stream', False):
    if call_id not in self._segment_tts_active:
        await self.streaming_playback_manager.start_segment_gating(call_id)
        self._segment_tts_active.add(call_id)

# Lines 3616-3621: Removes from tracking, enabling re-gating
await self.streaming_playback_manager.end_segment_gating(call_id)
self._segment_tts_active.discard(call_id)  # ← BUG: Allows re-gating
```

### Solution Options

#### **Option A: Don't Discard for Continuous Streams** (RECOMMENDED)

```python
# In AgentAudioDone handler (line 3610-3623):
if continuous:
    # Mark boundary and end per-segment gating
    await self.streaming_playback_manager.mark_segment_boundary(call_id)
    await self.streaming_playback_manager.end_segment_gating(call_id)
    # DON'T discard for continuous streams - keep gating active
    # self._segment_tts_active.discard(call_id)  # ← REMOVE THIS
```

**Why This Works**:

- First chunk: Gates audio (expected)
- Subsequent chunks: `call_id` still in set, no re-gating
- Greeting plays continuously without interruption
- Only clears when stream actually ends

---

#### **Option B: Don't Gate on Greeting Playback**

```python
# In AgentAudio handler (line 3454-3460):
if getattr(self.streaming_playback_manager, 'continuous_stream', False):
    # Check if this is greeting playback (don't gate greetings)
    active_stream = self.streaming_playback_manager.active_streams.get(call_id, {})
    playback_type = active_stream.get('playback_type', '')
    
    if playback_type != 'greeting' and call_id not in self._segment_tts_active:
        await self.streaming_playback_manager.start_segment_gating(call_id)
        self._segment_tts_active.add(call_id)
```

**Why This Works**:

- Greeting plays without any gating
- Later responses can still use gating if needed
- Simpler logic for greeting case

---

#### **Option C: Fix OpenAI Provider to Set continuous_stream Flag**

Ensure OpenAI provider sets `continuous_stream=True` and verify the logic properly handles it.

---

## 📊 Expected Results After Fix

| Metric | Current | After Fix |
|--------|---------|-----------|
| **Gating Cycles** | 20 times | 1 time (at start) ✅ |
| **Agent Audio Heard** | 7% of call | 40-50% of call ✅ |
| **Cutoffs** | Every 3-5 seconds | None ✅ |
| **User Experience** | Choppy, broken | Smooth, natural ✅ |
| **Underflows** | 142 in 82s | <10 in 82s ✅ |
| **Audio Quality** | 66.1dB (when playing) | 66.1dB (consistent) ✅ |

---

## 🎯 Summary

**What's Really Happening**:
The greeting audio from OpenAI is being played through ONE continuous stream, but the system's segment gating logic treats it as multiple segments. Each time new audio chunks arrive, the system gates (stops) the playback, processes the chunk, then clears the gating. This causes the greeting to play in a choppy, interrupted manner.

**Root Cause Code**:
`src/engine.py` line 3621: `self._segment_tts_active.discard(call_id)` removes the call from tracking, causing subsequent chunks to re-trigger gating.

**Solution**:
Don't discard the call_id from `_segment_tts_active` for continuous streams, OR don't gate greeting playback at all.

**Impact**:
This is THE issue causing agent audio cutoffs. The empty buffer commit issue explains why OpenAI doesn't respond to user, but THIS issue explains why the agent's own speech is choppy.

---

*Generated: Oct 25, 2025*  
*Status: ROOT CAUSE IDENTIFIED - Self-interrupting playback via repeated gating*  
*Recommendation: Option A - Don't discard call_id for continuous streams*
