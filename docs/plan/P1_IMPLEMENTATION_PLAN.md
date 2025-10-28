# P1 Implementation Plan - Multi-Provider Support

**Goal**: Enable seamless switching between AI providers (Deepgram, OpenAI Realtime) with automatic format negotiation

**Timeline**: 3-5 days  
**Priority**: High (required for GA)  
**Status**: Ready to start (P0 complete)

---

## Overview

P1 implements a **Transport Orchestrator** that:

1. Resolves audio format/rate per call based on provider + profile + channel vars
2. Negotiates with provider during handshake (e.g., OpenAI `session.updated`)
3. Provides per-call overrides via Asterisk channel variables
4. Maintains backward compatibility with current Deepgram-only setup

---

## Core Components

### 1. Audio Profiles (Configuration)

**File**: `config/ai-agent.yaml`

Add `profiles.*` block:

```yaml
profiles:
  default: telephony_ulaw_8k
  
  telephony_ulaw_8k:
    internal_rate_hz: 8000
    transport_out:
      encoding: slin          # AudioSocket wire format
      sample_rate_hz: 8000
    provider_pref:
      input_encoding: ulaw    # Prefer μ-law for telephony
      input_sample_rate_hz: 8000
      output_encoding: ulaw
      output_sample_rate_hz: 8000
    chunk_ms: auto            # 20ms default
    idle_cutoff_ms: 1200
    
  wideband_pcm_16k:
    internal_rate_hz: 16000
    transport_out:
      encoding: slin16         # AudioSocket wire format
      sample_rate_hz: 16000
    provider_pref:
      input_encoding: linear16
      input_sample_rate_hz: 16000
      output_encoding: linear16
      output_sample_rate_hz: 16000
    chunk_ms: auto
    idle_cutoff_ms: 1200
    
  openai_realtime_24k:
    internal_rate_hz: 24000
    transport_out:
      encoding: slin16         # Downsample to 16k for AudioSocket
      sample_rate_hz: 16000
    provider_pref:
      input_encoding: linear16
      input_sample_rate_hz: 24000   # OpenAI native
      output_encoding: linear16
      output_sample_rate_hz: 24000
    chunk_ms: auto
    idle_cutoff_ms: 1200
```

### 2. Context Mapping (Configuration)

Add `contexts.*` block for semantic routing:

```yaml
contexts:
  default:
    prompt: "You are a helpful AI assistant."
    greeting: "Hello, how can I help you today?"
    profile: telephony_ulaw_8k  # Default profile
    
  sales:
    prompt: "You are an enthusiastic sales assistant. Be upbeat and helpful."
    greeting: "Thanks for calling! How can I help you find what you need today?"
    profile: wideband_pcm_16k   # Better quality for sales
    provider: deepgram          # Optional provider override
    
  support:
    prompt: "You are technical support. Be concise and precise."
    greeting: "Technical support, how can we assist?"
    profile: telephony_ulaw_8k
    
  premium:
    prompt: "You are a premium concierge assistant."
    greeting: "Welcome to premium service. How may I assist you?"
    profile: openai_realtime_24k
    provider: openai_realtime
```

### 3. Per-Call Channel Variables

**Dialplan** (`extensions_custom.conf`):

```
[from-ai-agent-deepgram]
exten => s,1,NoOp(AI Agent with Channel Var Overrides)
  same => n,Set(AI_PROVIDER=deepgram)          ; Optional: override provider
  same => n,Set(AI_AUDIO_PROFILE=telephony_ulaw_8k)  ; Optional: override profile
  same => n,Set(AI_CONTEXT=sales)              ; Optional: semantic context
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()

[from-ai-agent-openai]
exten => s,1,NoOp(AI Agent - OpenAI Realtime)
  same => n,Set(AI_PROVIDER=openai_realtime)
  same => n,Set(AI_AUDIO_PROFILE=openai_realtime_24k)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()
```

**Precedence** (highest to lowest):

1. `AI_PROVIDER` channel var → overrides everything
2. `AI_CONTEXT` → maps to context config (includes profile + provider)
3. `AI_AUDIO_PROFILE` → overrides profile only
4. YAML `profiles.default` → fallback

---

## Implementation Tasks

### Task 1: Add TransportOrchestrator Class

**File**: `src/core/transport_orchestrator.py` (new file)

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

@dataclass
class AudioProfile:
    """User-defined audio profile from YAML."""
    name: str
    internal_rate_hz: int
    transport_out: Dict[str, Any]
    provider_pref: Dict[str, Any]
    chunk_ms: str  # "auto" or int
    idle_cutoff_ms: int

@dataclass
class ProviderCapabilities:
    """Provider's supported formats (from config or ACK)."""
    supported_input_encodings: list[str]
    supported_output_encodings: list[str]
    supported_sample_rates: list[int]
    preferred_chunk_ms: int = 20
    can_negotiate: bool = True

@dataclass
class TransportProfile:
    """Resolved transport settings for a call."""
    profile_name: str
    wire_encoding: str
    wire_sample_rate: int
    provider_input_encoding: str
    provider_input_sample_rate: int
    provider_output_encoding: str
    provider_output_sample_rate: int
    internal_rate: int
    chunk_ms: int
    idle_cutoff_ms: int
    remediation: Optional[str] = None

class TransportOrchestrator:
    def __init__(self, config: Dict[str, Any]):
        self.profiles = self._load_profiles(config)
        self.contexts = self._load_contexts(config)
        self.default_profile = config.get('profiles', {}).get('default', 'telephony_ulaw_8k')
    
    def resolve_transport(
        self,
        provider_name: str,
        provider_caps: ProviderCapabilities,
        channel_vars: Dict[str, str],
    ) -> TransportProfile:
        """
        Resolve transport profile for a call.
        
        Precedence:
        1. AI_PROVIDER channel var
        2. AI_CONTEXT (maps to context config)
        3. AI_AUDIO_PROFILE channel var
        4. YAML profiles.default
        """
        # Step 1: Determine profile name
        profile_name = self._resolve_profile_name(channel_vars)
        profile = self.profiles.get(profile_name)
        if not profile:
            raise ValueError(f"Profile '{profile_name}' not found")
        
        # Step 2: Negotiate with provider capabilities
        transport = self._negotiate_formats(profile, provider_caps)
        
        # Step 3: Validate and add remediation if needed
        transport = self._validate_and_remediate(transport, provider_caps)
        
        return transport
    
    def _resolve_profile_name(self, channel_vars: Dict[str, str]) -> str:
        """Resolve profile name from channel vars with precedence."""
        # Check AI_AUDIO_PROFILE first
        if 'AI_AUDIO_PROFILE' in channel_vars:
            return channel_vars['AI_AUDIO_PROFILE']
        
        # Check AI_CONTEXT
        context_name = channel_vars.get('AI_CONTEXT')
        if context_name and context_name in self.contexts:
            context = self.contexts[context_name]
            if 'profile' in context:
                return context['profile']
        
        # Fallback to default
        return self.default_profile
    
    def _negotiate_formats(
        self,
        profile: AudioProfile,
        provider_caps: ProviderCapabilities,
    ) -> TransportProfile:
        """Negotiate formats between profile preferences and provider capabilities."""
        # Wire format (AudioSocket) always from profile.transport_out
        wire_enc = profile.transport_out['encoding']
        wire_rate = profile.transport_out['sample_rate_hz']
        
        # Provider format: try profile preference, fallback to provider's first supported
        pref_in_enc = profile.provider_pref.get('input_encoding', 'linear16')
        pref_out_enc = profile.provider_pref.get('output_encoding', 'linear16')
        pref_rate = profile.provider_pref.get('input_sample_rate_hz', 16000)
        
        # Check if provider supports preferences
        provider_in_enc = pref_in_enc if pref_in_enc in provider_caps.supported_input_encodings else provider_caps.supported_input_encodings[0]
        provider_out_enc = pref_out_enc if pref_out_enc in provider_caps.supported_output_encodings else provider_caps.supported_output_encodings[0]
        provider_rate = pref_rate if pref_rate in provider_caps.supported_sample_rates else provider_caps.supported_sample_rates[0]
        
        # Resolve chunk_ms
        chunk_ms = 20 if profile.chunk_ms == 'auto' else int(profile.chunk_ms)
        
        return TransportProfile(
            profile_name=profile.name,
            wire_encoding=wire_enc,
            wire_sample_rate=wire_rate,
            provider_input_encoding=provider_in_enc,
            provider_input_sample_rate=provider_rate,
            provider_output_encoding=provider_out_enc,
            provider_output_sample_rate=provider_rate,
            internal_rate=profile.internal_rate_hz,
            chunk_ms=chunk_ms,
            idle_cutoff_ms=profile.idle_cutoff_ms,
        )
    
    def _validate_and_remediate(
        self,
        transport: TransportProfile,
        provider_caps: ProviderCapabilities,
    ) -> TransportProfile:
        """Validate transport profile and add remediation if needed."""
        issues = []
        
        # Check if provider actually supports negotiated formats
        if transport.provider_input_encoding not in provider_caps.supported_input_encodings:
            issues.append(f"Provider doesn't support input {transport.provider_input_encoding}")
        
        # Add remediation message
        if issues:
            transport.remediation = "; ".join(issues)
        
        return transport
```

**Tests**: `tests/unit/test_transport_orchestrator.py`

**Estimated Time**: 6-8 hours

---

### Task 2: Provider Capability Interface

**File**: `src/providers/base.py`

Add to `AIProviderInterface`:

```python
@dataclass
class ProviderCapabilities:
    supported_input_encodings: list[str]
    supported_output_encodings: list[str]
    supported_sample_rates: list[int]
    preferred_chunk_ms: int = 20
    can_negotiate: bool = True

class AIProviderInterface(ABC):
    # ... existing methods ...
    
    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Return provider's supported formats."""
        pass
    
    @abstractmethod
    def parse_ack(self, event_data: Dict[str, Any]) -> Optional[ProviderCapabilities]:
        """Parse provider ACK to extract accepted formats."""
        pass
```

**Estimated Time**: 2 hours

---

### Task 3: Implement Deepgram Capabilities

**File**: `src/providers/deepgram.py`

```python
def get_capabilities(self) -> ProviderCapabilities:
    """Deepgram Voice Agent supports μ-law and linear16."""
    return ProviderCapabilities(
        supported_input_encodings=["mulaw", "linear16"],
        supported_output_encodings=["mulaw", "linear16"],
        supported_sample_rates=[8000, 16000],
        preferred_chunk_ms=20,
        can_negotiate=True,  # Can negotiate via Settings message
    )

def parse_ack(self, event_data: Dict[str, Any]) -> Optional[ProviderCapabilities]:
    """Parse SettingsApplied event."""
    if event_data.get('type') != 'SettingsApplied':
        return None
    
    settings = event_data.get('settings', {})
    audio = settings.get('audio', {})
    input_audio = audio.get('input', {})
    output_audio = audio.get('output', {})
    
    return ProviderCapabilities(
        supported_input_encodings=[input_audio.get('encoding', 'mulaw')],
        supported_output_encodings=[output_audio.get('encoding', 'mulaw')],
        supported_sample_rates=[input_audio.get('sample_rate', 8000)],
        preferred_chunk_ms=20,
        can_negotiate=False,  # Already negotiated
    )
```

**Estimated Time**: 2 hours

---

### Task 4: Implement OpenAI Realtime Capabilities

**File**: `src/providers/openai_realtime.py` (new or update existing)

```python
def get_capabilities(self) -> ProviderCapabilities:
    """OpenAI Realtime API supports PCM16 @ 24kHz."""
    return ProviderCapabilities(
        supported_input_encodings=["linear16", "g711_ulaw"],  # Per OpenAI docs
        supported_output_encodings=["linear16", "g711_ulaw", "g711_alaw"],
        supported_sample_rates=[24000],  # Only 24kHz
        preferred_chunk_ms=20,
        can_negotiate=True,
    )

def parse_ack(self, event_data: Dict[str, Any]) -> Optional[ProviderCapabilities]:
    """Parse session.updated event."""
    if event_data.get('type') != 'session.updated':
        return None
    
    session = event_data.get('session', {})
    
    return ProviderCapabilities(
        supported_input_encodings=[session.get('input_audio_format', 'pcm16')],
        supported_output_encodings=[session.get('output_audio_format', 'pcm16')],
        supported_sample_rates=[24000],
        preferred_chunk_ms=20,
        can_negotiate=False,
    )
```

**Estimated Time**: 4 hours (includes OpenAI integration if not already present)

---

### Task 5: Integrate Orchestrator into Engine

**File**: `src/engine.py`

```python
# In __init__
self.transport_orchestrator = TransportOrchestrator(self.config)

# In _handle_stasis_start (after session creation)
async def _handle_stasis_start(self, event):
    # ... existing code ...
    
    # Get channel variables
    channel_vars = {
        'AI_PROVIDER': await self._get_channel_var(channel_id, 'AI_PROVIDER'),
        'AI_AUDIO_PROFILE': await self._get_channel_var(channel_id, 'AI_AUDIO_PROFILE'),
        'AI_CONTEXT': await self._get_channel_var(channel_id, 'AI_CONTEXT'),
    }
    
    # Resolve provider (precedence: channel var > context > config default)
    provider_name = self._resolve_provider_name(channel_vars, session)
    provider = self.providers.get(provider_name)
    if not provider:
        raise ValueError(f"Provider '{provider_name}' not available")
    
    # Get provider capabilities
    provider_caps = provider.get_capabilities()
    
    # Resolve transport profile
    transport = self.transport_orchestrator.resolve_transport(
        provider_name=provider_name,
        provider_caps=provider_caps,
        channel_vars=channel_vars,
    )
    
    # Store in session
    session.transport_profile = transport
    session.provider_name = provider_name
    session.provider = provider
    
    # Log Transport Card
    self._emit_transport_card(session.call_id, transport)
    
    # Apply to streaming manager
    self.streaming_playback_manager.audiosocket_format = transport.wire_encoding
    self.streaming_playback_manager.sample_rate = transport.wire_sample_rate
    self.streaming_playback_manager.chunk_size_ms = transport.chunk_ms
    self.streaming_playback_manager.idle_cutoff_ms = transport.idle_cutoff_ms
    
    # ... continue with provider initialization ...

def _emit_transport_card(self, call_id: str, transport: TransportProfile):
    """Emit one-shot TransportCard log."""
    logger.info(
        "TransportCard",
        call_id=call_id,
        profile=transport.profile_name,
        wire_encoding=transport.wire_encoding,
        wire_sample_rate=transport.wire_sample_rate,
        provider_input_encoding=transport.provider_input_encoding,
        provider_input_sample_rate=transport.provider_input_sample_rate,
        provider_output_encoding=transport.provider_output_encoding,
        provider_output_sample_rate=transport.provider_output_sample_rate,
        internal_rate=transport.internal_rate,
        chunk_ms=transport.chunk_ms,
        idle_cutoff_ms=transport.idle_cutoff_ms,
        remediation=transport.remediation,
    )
```

**Estimated Time**: 6-8 hours

---

### Task 6: Update Configuration Schema

**File**: `src/config.py`

```python
class AudioProfileConfig(BaseModel):
    internal_rate_hz: int
    transport_out: Dict[str, Any]
    provider_pref: Dict[str, Any]
    chunk_ms: Union[str, int] = "auto"
    idle_cutoff_ms: int = 1200

class ContextConfig(BaseModel):
    prompt: Optional[str] = None
    greeting: Optional[str] = None
    profile: Optional[str] = None
    provider: Optional[str] = None

class Config(BaseModel):
    # ... existing fields ...
    profiles: Dict[str, AudioProfileConfig] = {}
    contexts: Dict[str, ContextConfig] = {}
```

**Estimated Time**: 2 hours

---

### Task 7: Backward Compatibility

**File**: `src/core/transport_orchestrator.py`

```python
def _synthesize_legacy_profile(self, config: Dict[str, Any]) -> AudioProfile:
    """Synthesize profile from legacy config when profiles.* not present."""
    audiosocket_format = config.get('audiosocket', {}).get('format', 'slin')
    streaming_rate = config.get('streaming', {}).get('sample_rate', 8000)
    
    # Map to encoding names
    encoding_map = {'slin': 'linear16', 'slin16': 'linear16', 'ulaw': 'mulaw'}
    
    return AudioProfile(
        name='legacy_compat',
        internal_rate_hz=streaming_rate,
        transport_out={
            'encoding': audiosocket_format,
            'sample_rate_hz': streaming_rate,
        },
        provider_pref={
            'input_encoding': encoding_map.get(audiosocket_format, 'linear16'),
            'input_sample_rate_hz': streaming_rate,
            'output_encoding': encoding_map.get(audiosocket_format, 'linear16'),
            'output_sample_rate_hz': streaming_rate,
        },
        chunk_ms='auto',
        idle_cutoff_ms=1200,
    )
```

**Estimated Time**: 2 hours

---

### Task 8: Documentation

**Files**:

- `docs/Architecture.md` - Add "Transport Orchestrator" section
- `docs/MULTI_PROVIDER_GUIDE.md` - New user guide
- `docs/providers/openai_realtime.md` - OpenAI integration guide

**Content**:

- How to configure multiple providers
- Audio profile selection guide
- Channel variable reference
- Context mapping examples
- Migration from single-provider setup

**Estimated Time**: 4 hours

---

### Task 9: Testing

**Test Files**:

- `tests/unit/test_transport_orchestrator.py`
- `tests/unit/test_provider_capabilities.py`
- `tests/integration/test_multi_provider_switching.py`

**Test Scenarios**:

1. Default profile resolution
2. Channel var overrides (AI_PROVIDER, AI_AUDIO_PROFILE, AI_CONTEXT)
3. Context mapping with profile inheritance
4. Provider capability negotiation
5. Backward compatibility (no profiles.* block)
6. Late ACK handling (log warning, don't renegotiate)
7. Provider format mismatch (apply remediation)

**Estimated Time**: 8 hours

---

## Testing Plan

### Phase 1: Unit Tests (Day 1-2)

- TransportOrchestrator resolution logic
- ProviderCapabilities parsing
- Profile/context precedence
- Backward compatibility

### Phase 2: Integration Tests (Day 2-3)

- Deepgram with telephony_ulaw_8k profile
- Deepgram with wideband_pcm_16k profile
- OpenAI Realtime with openai_realtime_24k profile
- Provider switching via AI_PROVIDER channel var
- Context mapping (sales/support contexts)

### Phase 3: Regression Tests (Day 3-4)

- Run golden baseline call with Deepgram
- Verify metrics match P0 validation
- Test OpenAI Realtime end-to-end
- Verify no regressions in existing Deepgram setup

### Phase 4: User Acceptance (Day 4-5)

- Multi-turn conversation with Deepgram
- Multi-turn conversation with OpenAI Realtime
- Switch providers mid-deployment (different DID routes)
- Verify audio quality across all profiles

---

## Acceptance Criteria

### ✅ P1 Complete When

1. **Multi-Provider Support**:
   - ✅ Can route calls to Deepgram or OpenAI via `AI_PROVIDER` channel var
   - ✅ Each provider works with appropriate audio profiles
   - ✅ No code changes needed to switch providers

2. **Audio Profile System**:
   - ✅ At least 3 profiles defined: telephony_ulaw_8k, wideband_pcm_16k, openai_realtime_24k
   - ✅ Profile selection via channel vars or context mapping
   - ✅ TransportCard logged at call start

3. **Provider Negotiation**:
   - ✅ Providers report capabilities
   - ✅ Orchestrator negotiates formats
   - ✅ Remediation logged when mismatch occurs
   - ✅ Late ACK ignored with warning (no mid-call renegotiation)

4. **Context Mapping**:
   - ✅ Semantic contexts (sales, support, premium) map to profiles + prompts
   - ✅ `AI_CONTEXT` channel var selects context
   - ✅ Context overrides work as expected

5. **Backward Compatibility**:
   - ✅ Existing config (no profiles.*) still works
   - ✅ Legacy synthesis creates implicit profile
   - ✅ No breaking changes for current Deepgram users

6. **Quality**:
   - ✅ All regression tests pass
   - ✅ Golden baseline metrics maintained
   - ✅ OpenAI Realtime delivers clean audio
   - ✅ No garbled audio, underflows, or pacing issues

---

## Timeline

### Day 1 (8 hours)

- ✅ Task 1: TransportOrchestrator class (6h)
- ✅ Task 2: Provider capability interface (2h)

### Day 2 (8 hours)

- ✅ Task 3: Deepgram capabilities (2h)
- ✅ Task 4: OpenAI Realtime capabilities (4h)
- ✅ Unit tests (2h)

### Day 3 (8 hours)

- ✅ Task 5: Integrate orchestrator into engine (6h)
- ✅ Task 6: Configuration schema (2h)

### Day 4 (8 hours)

- ✅ Task 7: Backward compatibility (2h)
- ✅ Task 9: Integration tests (4h)
- ✅ Regression testing (2h)

### Day 5 (8 hours)

- ✅ Task 8: Documentation (4h)
- ✅ User acceptance testing (3h)
- ✅ Final validation + tag (1h)

**Total**: 40 hours (5 days @ 8 hours/day)

---

## Risks & Mitigation

### Risk 1: OpenAI Realtime API Changes

**Mitigation**: Reference official OpenAI docs; implement capability parsing to adapt to ACK format

### Risk 2: Format Negotiation Complexity

**Mitigation**: Start with static profiles; add dynamic negotiation incrementally

### Risk 3: Backward Compatibility Breaks

**Mitigation**: Extensive testing with existing config; legacy synthesis as fallback

### Risk 4: Performance Regression

**Mitigation**: Run golden baseline regression after each major change

---

## Success Metrics

**P1 is successful if**:

1. ✅ Deepgram calls work identically to P0 (no regression)
2. ✅ OpenAI Realtime calls deliver clean audio
3. ✅ Provider switching works via channel vars
4. ✅ All acceptance criteria met
5. ✅ Documentation complete
6. ✅ Zero breaking changes for existing users

**Tag**: `v1.0-p1-multi-provider`

**Next**: P2 (Config Cleanup + CLI Tools) or GA preparation

---

## Notes

- **Do NOT implement mid-call renegotiation** in P1 (future enhancement)
- **Do NOT change inbound path** in P1 (proven stable from P0)
- **Keep diagnostic taps working** throughout changes
- **Maintain golden baseline metrics** at every step

---

**Created**: Oct 25, 2025  
**Status**: Ready to start  
**Dependencies**: P0 complete ✅
