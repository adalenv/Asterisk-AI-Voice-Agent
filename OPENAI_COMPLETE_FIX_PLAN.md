# OpenAI Realtime - COMPLETE FIX PLAN
## Based on Comprehensive RCA | Date: Oct 26, 2025

---

## 🎯 **EXECUTIVE SUMMARY**

We have identified **FIVE CRITICAL ISSUES** that must all be fixed together:

1. ❌ **Session Initialization Bug** - audio_capture_enabled starts as False
2. ❌ **session.updated ACK Timeout** - 3-second delay blocks greeting
3. ❌ **Protection Window Timing** - Triggers before TTS starts
4. ❌ **AudioSocket Sample Rate Mismatch** - 16kHz sent to 8kHz channel
5. ❌ **Premature Segment Ending** - Only 80ms of 640ms plays

**Current Situation**: None of our previous fixes addressed root causes. We were treating symptoms, not the disease.

---

## 📊 **FIXES NEEDED**

### **FIX #1: Session Initialization**
**File**: `src/engine.py` lines 990, 1344
Change: `audio_capture_enabled=False` → `audio_capture_enabled=True`

### **FIX #2: Greeting Before ACK Wait**
**File**: `src/providers/openai_realtime.py` around line 362
Send greeting immediately, don't wait for session.updated ACK

### **FIX #3: Protection Only After TTS Starts**
**File**: `src/engine.py` line 2303
Add check: `and getattr(session, 'tts_started_ts', 0.0) > 0.0`

### **FIX #4: AudioSocket Sample Rate (CRITICAL)**
**File**: `src/core/streaming_playback_manager.py` lines 598-607
Force slin to 8kHz (don't use provider rate)

### **FIX #5: Investigate Segment Ending**
Check if Fix #4 resolves premature cutoff

---

See full document for complete details and implementation steps.
