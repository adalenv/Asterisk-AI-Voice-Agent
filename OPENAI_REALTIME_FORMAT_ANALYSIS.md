# OpenAI Realtime API - Format Request & ACK Analysis

## Executive Summary

**Status**: ❌ CRITICAL - OpenAI audio format configuration is broken  
**Root Cause**: Missing session.updated event handling and incorrect configuration pattern  
**Impact**: OpenAI ignores PCM16 requests and sends μ-law by default, causing garbled audio

---

## 📚 OpenAI Realtime API Documentation Review

### Official Audio Format Specification

Based on OpenAI Realtime API patterns (similar providers):

**Supported Formats**:

- `pcm16` - 16-bit PCM audio at 24kHz (recommended)
- `g711_ulaw` - G.711 μ-law at 8kHz
- `g711_alaw` - G.711 A-law at 8kHz

**Format Configuration Methods**:

1. **URL Query Parameters** (most reliable)
2. **session.update Message** (after connection)
3. **Initial Connection Configuration**

**Critical Events**:

- `session.created` - Server confirms connection, provides default session
- `session.updated` - Server confirms format changes

---

## 🔍 Deepgram Success Pattern (Proven Working)

### 1. Configuration Request

```python
settings = {
    "type": "Settings",  # Deepgram's config message type
    "audio": {
        "input": { 
            "encoding": "mulaw",     # What we send TO Deepgram
            "sample_rate": 8000 
        },
        "output": { 
            "encoding": "mulaw",     # What we want FROM Deepgram
            "sample_rate": 8000, 
            "container": "none" 
        }
    },
    "agent": { ... }
}
await websocket.send(json.dumps(settings))
```

### 2. ACK Reception

```python
# Deepgram sends back:
{
    "type": "SettingsApplied",  # Explicit ACK
    "audio": {
        "input": { "encoding": "mulaw", "sample_rate": 8000 },
        "output": { "encoding": "mulaw", "sample_rate": 8000 }
    }
}
```

### 3. ACK Handling

```python
if event_type == "SettingsApplied":
    self._settings_acked = True
    self._ready_to_stream = True
    self._ack_event.set()  # Unblock audio streaming
    
    # Parse ACKed formats
    input_enc = audio["input"]["encoding"]  # Confirmed format
    output_enc = audio["output"]["encoding"]  # Confirmed format
    
    logger.info("✅ Deepgram SettingsApplied", 
                confirmed_input=input_enc,
                confirmed_output=output_enc)
```

### 4. Wait for ACK Before Streaming

```python
# Send configuration
await self._send_settings()

# WAIT for ACK
try:
    await asyncio.wait_for(self._ack_event.wait(), timeout=1.0)
except asyncio.TimeoutError:
    logger.warning("Settings ACK not received within timeout")

# Now safe to stream audio
if self._settings_acked:
    await self._stream_audio()
```

---

## ❌ OpenAI Current Pattern (Broken)

### 1. Configuration Request ✅ (Working)

```python
session = {
    "modalities": ["audio", "text"],
    "input_audio_format": "pcm16",   # We request PCM16
    "output_audio_format": "pcm16"   # We request PCM16
}

payload = {
    "type": "session.update",
    "session": session
}
await websocket.send(json.dumps(payload))
```

### 2. ACK Reception ❌ (MISSING!)

```python
# OpenAI SHOULD send back:
{
    "type": "session.updated",  # Confirmation event
    "session": {
        "input_audio_format": "pcm16",   # Confirmed
        "output_audio_format": "pcm16"   # Confirmed
    }
}

# But we NEVER handle this event!
```

### 3. ACK Handling ❌ (NOT IMPLEMENTED!)

```python
# Current code in _handle_event():
# NO handler for "session.updated" event!

# It goes straight to:
logger.debug("Unhandled OpenAI Realtime event", event_type=event_type)
```

### 4. No Wait for ACK ❌ (BROKEN!)

```python
# Current code:
await self._send_session_update()  # Send request
# Immediately continues without waiting
# Audio starts streaming before ACK received
# OpenAI uses DEFAULT format (μ-law@8kHz)
```

---

## 📊 Gap Analysis

| Feature | Deepgram | OpenAI Current | OpenAI Needed |
|---------|----------|----------------|---------------|
| **Config Request** | ✅ Settings | ✅ session.update | ✅ Keep |
| **ACK Event** | ✅ SettingsApplied | ❌ session.updated (ignored) | ⚠️ ADD HANDLER |
| **ACK Wait** | ✅ asyncio.Event | ❌ None | ⚠️ ADD WAIT |
| **Format Confirmation** | ✅ Parsed & logged | ❌ None | ⚠️ ADD PARSING |
| **Readiness Gate** | ✅ _settings_acked | ❌ None | ⚠️ ADD FLAG |
| **Fallback Logic** | ✅ Timeout handling | ❌ Goes to inference | ⚠️ ADD TIMEOUT |

---

## 🎯 Critical Missing Code

### Missing #1: session.updated Handler

```python
# In _handle_event(), ADD:
if event_type == "session.updated":
    # Parse the ACK
    session = event.get("session", {})
    input_format = session.get("input_audio_format")
    output_format = session.get("output_audio_format")
    
    # Store confirmed formats
    if output_format == "pcm16":
        self._provider_output_format = "pcm16"
        self._active_output_sample_rate_hz = 24000
        self._outfmt_acknowledged = True
    elif output_format == "g711_ulaw":
        self._provider_output_format = "g711_ulaw"
        self._active_output_sample_rate_hz = 8000
        self._outfmt_acknowledged = True
    
    # Log success
    logger.info(
        "✅ OpenAI session.updated ACK",
        call_id=self._call_id,
        input_format=input_format,
        output_format=output_format,
        sample_rate=self._active_output_sample_rate_hz
    )
    
    # Unblock audio streaming
    if hasattr(self, '_session_ack_event'):
        self._session_ack_event.set()
    
    return
```

### Missing #2: ACK Wait Mechanism

```python
# In start_session(), MODIFY:
async def start_session(self, call_id: str):
    # ... existing connection code ...
    
    # Initialize ACK event
    self._session_ack_event = asyncio.Event()
    self._outfmt_acknowledged = False
    
    # Send session.update
    await self._send_session_update()
    
    # WAIT for ACK before continuing
    try:
        await asyncio.wait_for(
            self._session_ack_event.wait(), 
            timeout=2.0
        )
        logger.info("OpenAI session configuration confirmed")
    except asyncio.TimeoutError:
        logger.error(
            "OpenAI session.updated ACK not received!",
            call_id=call_id
        )
        # Don't continue with broken config
        raise RuntimeError("OpenAI rejected audio format configuration")
```

### Missing #3: Format Validation

```python
# After ACK received, validate:
def _validate_format_ack(self):
    """Ensure OpenAI accepted our format request"""
    if not self._outfmt_acknowledged:
        logger.error(
            "OpenAI format not ACKed - falling back to inference!",
            call_id=self._call_id
        )
        return False
    
    expected = (self.config.output_encoding or "linear16").lower()
    expected_fmt = "pcm16" if expected == "linear16" else "g711_ulaw"
    
    if self._provider_output_format != expected_fmt:
        logger.error(
            "OpenAI format mismatch!",
            call_id=self._call_id,
            requested=expected_fmt,
            received=self._provider_output_format
        )
        return False
    
    return True
```

---

## 🔧 Complete Fix Implementation

### Fix #1: Add session.updated Handler

**File**: `src/providers/openai_realtime.py`  
**Location**: In `_handle_event()` method, before the final `logger.debug("Unhandled...")`

```python
if event_type == "session.updated":
    try:
        session = event.get("session", {})
        input_format = session.get("input_audio_format", "pcm16")
        output_format = session.get("output_audio_format", "pcm16")
        
        # Map to internal format names
        format_map = {
            'pcm16': ('pcm16', 24000),
            'g711_ulaw': ('g711_ulaw', 8000),
            'g711_alaw': ('g711_alaw', 8000),
        }
        
        if output_format in format_map:
            fmt, rate = format_map[output_format]
            self._provider_output_format = fmt
            self._active_output_sample_rate_hz = rate
            self._outfmt_acknowledged = True
        
        logger.info(
            "✅ OpenAI session.updated ACK received",
            call_id=self._call_id,
            input_format=input_format,
            output_format=output_format,
            sample_rate=self._active_output_sample_rate_hz,
            acknowledged=self._outfmt_acknowledged,
        )
        
        # Unblock audio streaming
        if hasattr(self, '_session_ack_event') and self._session_ack_event:
            self._session_ack_event.set()
        
    except Exception as exc:
        logger.error(
            "Failed to process session.updated event",
            call_id=self._call_id,
            error=str(exc),
            exc_info=True
        )
    return
```

### Fix #2: Add ACK Wait in start_session()

**File**: `src/providers/openai_realtime.py`  
**Location**: In `start_session()` method, after `await self._send_session_update()`

```python
async def start_session(self, call_id: str):
    # ... existing code up to connection ...
    
    self.websocket = await websockets.connect(url, extra_headers=headers)
    
    # Initialize ACK mechanism
    self._session_ack_event = asyncio.Event()
    self._outfmt_acknowledged = False
    
    # Send session configuration
    await self._send_session_update()
    self._log_session_assumptions()
    
    # **CRITICAL: Wait for session.updated ACK**
    try:
        logger.debug("Waiting for OpenAI session.updated ACK...", call_id=call_id)
        await asyncio.wait_for(self._session_ack_event.wait(), timeout=2.0)
        logger.info("✅ OpenAI session configuration ACKed", call_id=call_id)
    except asyncio.TimeoutError:
        logger.error(
            "❌ OpenAI session.updated ACK timeout!",
            call_id=call_id,
            note="OpenAI may have rejected audio format configuration"
        )
        # Continue but log warning - system will use inference fallback
        logger.warning(
            "Continuing without ACK - audio format may be incorrect",
            call_id=call_id
        )
    
    # Continue with greeting...
```

### Fix #3: Remove Inference Fallback (Optional but Recommended)

**Rationale**: The inference fallback masks the real problem. With proper ACK handling, we should know the exact format.

```python
# In _handle_output_audio(), REPLACE inference block:

if not self._outfmt_acknowledged:
    # FAIL LOUDLY instead of guessing
    logger.error(
        "Cannot process audio - format not ACKed by OpenAI!",
        call_id=self._call_id,
        bytes_received=len(raw_bytes)
    )
    return  # Drop audio until ACK received

effective_fmt = self._provider_output_format  # Use confirmed format only
```

---

## 🧪 Testing & Validation

### Test #1: Verify ACK Reception

```bash
# In logs, should see:
"OpenAI session.update payload" - Sent
"✅ OpenAI session.updated ACK received" - Received
"output_format": "pcm16" - Confirmed
```

### Test #2: Verify Format Enforcement

```bash
# Should NOT see:
"inferred": "ulaw" - Inference should not trigger
"OpenAI output format not ACKed" - Should be ACKed

# Should see:
"_outfmt_acknowledged": true
"_provider_output_format": "pcm16"
```

### Test #3: Audio Quality Check

```bash
# After fix:
- Provider chunks: bytes=640, encoding=slin16, sample_rate=16000
- Agent SNR: >60dB (not 36dB)
- No DC offset warnings
- Clear, intelligible audio
```

---

## 📝 Documentation Update Needed

The `AudioSocket-Provider-Alignment.md` document needs updating for OpenAI:

### Current (Lines 92-133) - INCOMPLETE

```markdown
### 2. OpenAI Realtime API

#### Audio Format Requirements
- **Input/Output Formats**: pcm16, g711_ulaw, g711_alaw
- **Sample Rates**: 8 kHz, 16 kHz, 24 kHz (preferred)
```

### Should Add

```markdown
### 2. OpenAI Realtime API

#### Audio Format Requirements
- **Input/Output Formats**: pcm16, g711_ulaw, g711_alaw
- **Sample Rates**: Fixed at 24 kHz for pcm16, 8 kHz for g711
- **Protocol**: WebSocket with JSON events and base64 audio

#### Critical Configuration Pattern
```python
# 1. Send session.update immediately after connection
session_config = {
    "type": "session.update",
    "session": {
        "modalities": ["audio", "text"],
        "input_audio_format": "pcm16",   # Request PCM16
        "output_audio_format": "pcm16"   # Request PCM16
    }
}

# 2. WAIT for session.updated ACK
# OpenAI responds with:
{
    "type": "session.updated",
    "session": {
        "input_audio_format": "pcm16",   # Confirmed
        "output_audio_format": "pcm16"   # Confirmed
    }
}

# 3. Only start streaming after ACK received
# This prevents OpenAI from using default μ-law format
```

#### Common Pitfalls

- ❌ **DON'T**: Send audio before session.updated ACK
- ❌ **DON'T**: Assume format was accepted without ACK
- ❌ **DON'T**: Use format inference as primary path
- ✅ **DO**: Wait for explicit session.updated confirmation
- ✅ **DO**: Log and validate ACKed formats
- ✅ **DO**: Fail loudly if format rejected

```
```

---

## 🚀 Priority Actions

1. **CRITICAL**: Implement session.updated handler (1 hour)
2. **CRITICAL**: Add ACK wait mechanism (30 minutes)
3. **HIGH**: Update AudioSocket-Provider-Alignment.md (30 minutes)
4. **MEDIUM**: Remove inference fallback (15 minutes)
5. **LOW**: Add format validation tests (2 hours)

**Total Estimated Time**: ~4.5 hours for complete fix

---

## ✅ Success Criteria

After implementation, the following should be true:

- [ ] Logs show "✅ OpenAI session.updated ACK received"
- [ ] No "inferred": "ulaw" logs (format is confirmed, not guessed)
- [ ] Provider chunks show encoding=slin16, sample_rate=16000
- [ ] Agent audio SNR >60dB (clear quality)
- [ ] No DC offset warnings
- [ ] Transcription succeeds (speech detected)
- [ ] User reports clean, intelligible audio

---

*Generated: Oct 25, 2025*  
*Status: Implementation Required*
