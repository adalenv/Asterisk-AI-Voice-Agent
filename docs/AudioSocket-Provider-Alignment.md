# AudioSocket Alignment with Different AI Providers

## Executive Summary

This document provides comprehensive codec/format alignment guidance for integrating AudioSocket with various AI providers in Asterisk-based voice agent systems. It builds upon the foundation established in the original AudioSocket technical summary and extends support to cover major AI providers including Deepgram, OpenAI Realtime, Google Cloud, Azure, ElevenLabs, AWS Polly, PlayHT, Cartesia, and others.

## CRITICAL CORRECTION: AudioSocket is a PCM-Only Protocol

After a thorough review of the official Asterisk AudioSocket specification and production logs, it is crucial to understand that **AudioSocket does NOT support compressed codecs like μ-law (ulaw) or A-law directly**. The protocol exclusively transports **PCM16** (signed linear, 16-bit) audio at various sample rates.

The common misconception about "μ-law support" arises from Asterisk's powerful and transparent transcoding capabilities. The typical audio flow is as follows:

1. **SIP Endpoint**: Uses a compressed codec like μ-law (G.711).
2. **Asterisk Bridge**: Automatically and transparently transcodes the μ-law audio from the SIP channel into PCM16 (`slin`).
3. **AudioSocket Channel**: Receives and sends **only PCM16** data, never μ-law.
4. **AI Engine**: Receives PCM16 from AudioSocket and may then convert it to another format (like `mulaw`) if required by the AI provider.

This corrected understanding is fundamental to designing a stable and efficient integration.

## AudioSocket Foundation

### Corrected AudioSocket Profiles (PCM16 Only)

| Profile | Message Type | Sample Rate | Encoding | Bit Depth | Channels | Byte Order |
|---|---|---|---|---|---|---|
| **Telephony** | `0x10` | 8 kHz | PCM16 (`slin`) | 16-bit | Mono | Little-endian |
| **Wideband** | `0x12` | 16 kHz | PCM16 (`slin16`) | 16-bit | Mono | Little-endian |
| **High Quality** | `0x13` | 24 kHz | PCM16 (`slin24`) | 16-bit | Mono | Little-endian |

### Official AudioSocket Message Types (All PCM16)

- **`0x10`**: PCM16, 8kHz, mono, signed linear, little-endian
- **`0x11`**: PCM16, 12kHz, mono, signed linear, little-endian
- **`0x12`**: PCM16, 16kHz, mono, signed linear, little-endian
- **`0x13`**: PCM16, 24kHz, mono, signed linear, little-endian
- **`0x14`**: PCM16, 32kHz, mono, signed linear, little-endian
- **`0x15`**: PCM16, 44.1kHz, mono, signed linear, little-endian
- **`0x16`**: PCM16, 48kHz, mono, signed linear, little-endian
- **`0x17`**: PCM16, 96kHz, mono, signed linear, little-endian
- **`0x18`**: PCM16, 192kHz, mono, signed linear, little-endian
- **`0x01`**: UUID handshake
- **`0x03`**: DTMF digit
- **`0x00`**: Terminate connection

## Provider-Specific Integration Strategies

### 1. Deepgram Integration

#### Speech-to-Text (STT)

- **Supported Formats**: linear16, mulaw, alaw, opus, ogg-opus, flac, wav, mp3 (100+ formats total)
- **Sample Rates**: 8 kHz, 16 kHz, 24 kHz, 48 kHz
- **Optimal AudioSocket Alignment (Production-Proven)**:
  - **`slin@8k` (PCM16) is REQUIRED** for the AudioSocket transport layer.
  - **Why?** The AudioSocket protocol's `0x10` message type for 8kHz audio strictly mandates a PCM16 payload. Using the `ulaw` profile is incompatible and will lead to errors.
  - The AI engine is responsible for the efficient transcoding bridge: `slin@8k` (from AudioSocket) ↔ `mulaw@8k` (for Deepgram).

#### Text-to-Speech (TTS)

- **Aura-2 Models**: Optimized for real-time enterprise use
- **Supported Formats**: linear16, mulaw, alaw, opus
- **Sample Rates**: Voice-dependent (some 8 kHz, others 24 kHz+)
- **Integration**: Request format compatible with AudioSocket profile

#### Voice Agent API

- **Input**: linear16@8k/16k/24k/48k, mulaw@8k
- **Output**: linear16, mulaw
- **Optimal**: `mulaw@8kHz` for both `input_encoding` and `output_encoding` when using the Deepgram Voice Agent. The AI engine handles the conversion from AudioSocket's `slin@8k` transport.
- **Configuration**: Validate via SettingsApplied ACK message.

```python
# Deepgram Voice Agent Configuration (Proven)
# NOTE: The AI Engine sends mulaw to Deepgram, but the underlying AudioSocket transport MUST be slin (PCM16).
config = {
  "type": "SettingsConfiguration",
  "audio": {
      "input": {
          "encoding": "mulaw",
          "sample_rate": 8000
      },
      "output": {
          "encoding": "mulaw",
          "sample_rate": 8000
      }
  },
  "agent": {
      "listen": {"model": "nova-3"},
      "think": {"provider": {"type": "open_ai"}, "model": "gpt-4o-mini"},
      "speak": {"model": "aura-2-thalia-en"}
  }
}
```

### 2. OpenAI Realtime API

#### Audio Format Requirements

- **Input/Output Formats**: pcm16, g711_ulaw, g711_alaw
- **Sample Rates**: 8 kHz, 16 kHz, 24 kHz (preferred)
- **Encoding**: 16-bit little-endian (pcm16)
- **Protocol**: WebSocket with base64-encoded audio chunks

#### AudioSocket Alignment Options

##### Option 1: Direct 8 kHz PCM

```python
# AudioSocket slin@8k → OpenAI pcm16@8k
openai_config = {
    "modalities": ["audio", "text"],
    "input_audio_format": "pcm16",
    "output_audio_format": "pcm16"
}
```

##### Option 2: μ-law Telephony

```python
# AudioSocket ulaw@8k → OpenAI g711_ulaw@8k
openai_config = {
    "modalities": ["audio", "text"],
    "input_audio_format": "g711_ulaw",
    "output_audio_format": "g711_ulaw"
}
```

##### Option 3: Quality-Optimized (24 kHz)

```python
# Requires resampling: AudioSocket slin@8k → pcm16@24k
def upsample_to_24khz(audio_8khz):
    from scipy import signal
    import numpy as np
    
    audio_array = np.frombuffer(audio_8khz, dtype=np.int16)
    upsampled = signal.resample(audio_array, len(audio_array) * 3)
    return upsampled.astype(np.int16).tobytes()
```

### 3. Google Cloud Speech & Text-to-Speech

#### Speech-to-Text (Google Cloud)

- **Supported Encodings**: LINEAR16, MULAW, FLAC, MP3, OGG_OPUS, WEBM_OPUS
- **Sample Rate Range**: 8-48 kHz (16 kHz minimum recommended)
- **AudioSocket Alignment**:
  - `ulaw@8k` → `MULAW` encoding
  - `slin@8k/16k` → `LINEAR16` encoding

#### Text-to-Speech (Google Cloud)

- **Output Formats**: LINEAR16, MP3, OGG_OPUS
- **Sample Rate**: Configurable via `sampleRateHertz` parameter
- **Default Rates**: 22-24 kHz (can request 8 kHz for telephony)

```python
# Google TTS Configuration for AudioSocket
tts_config = {
    "voice": {"language_code": "en-US", "name": "en-US-Wavenet-D"},
    "audio_config": {
        "audio_encoding": "LINEAR16",
        "sample_rate_hertz": 8000  # Match AudioSocket profile
    }
}
```

### 4. Azure Cognitive Services Speech

#### Speech-to-Text (Azure)

- **Primary Format**: WAV (16 kHz or 8 kHz, 16-bit, mono PCM)
- **Additional Support**: MP3, OGG_OPUS
- **AudioSocket Compatibility**:
  - `slin@8k` → 8 kHz WAV PCM
  - `slin16@16k` → 16 kHz WAV PCM

#### Text-to-Speech (Azure)

- **Format Examples**:
  - `riff-24khz-16bit-mono-pcm`
  - `audio-16khz-32kbitrate-mono-mp3`
  - `raw-16khz-16bit-mono-pcm`
- **Sample Rates**: 8, 16, 24, 48 kHz

### 5. ElevenLabs

#### Audio Format Support

- **Formats**: MP3, PCM, μ-law, A-law, Opus
- **Sample Rates**:
  - MP3: 22.05 kHz, 44.1 kHz (32-192 kbps)
  - PCM: 8, 16, 22.05, 24, 44.1 kHz (44.1 requires Pro), 48 kHz
  - μ-law/A-law: 8 kHz
  - Opus: 48 kHz

#### AudioSocket Integration

- **Telephony Option**: μ-law@8k → ElevenLabs μ-law@8k
- **Quality Option**: slin16@16k → PCM@16k (with tier requirements)

```python
# ElevenLabs API Configuration
elevenlabs_config = {
    "voice_id": "voice_id_here",
    "output_format": "pcm_16000",  # or "ulaw_8000"
    "model_id": "eleven_multilingual_v2"
}
```

### 6. AWS Polly

#### Audio Specifications

- **Formats**: MP3, OGG_VORBIS, PCM
- **Sample Rates**: 8, 16, 22.05 (default), 24 kHz
- **Default**: 22.05 kHz for standard voices

#### Integration Strategy

```python
# AWS Polly for AudioSocket
polly_config = {
    "OutputFormat": "pcm",
    "SampleRate": "8000",  # Match AudioSocket
    "VoiceId": "Joanna",
    "Engine": "neural"  # or "standard"
}
```

### 7. PlayHT

#### Comprehensive Format Support

- **Formats**: WAV, MP3, MULAW, FLAC, OGG, RAW
- **Sample Rates**: 8, 16, 24, 44.1, 48 kHz
- **Voice Engines**: PlayHT 2.0, PlayHT 1.0, Standard

#### AudioSocket Alignment

```python
# PlayHT Configuration
playht_config = {
    "voice": "voice_manifest_url",
    "format": "FORMAT_MULAW",  # Direct ulaw compatibility
    "sample_rate": 8000,
    "speed": 1.0
}
```

### 8. Cartesia

#### Optimized Configuration

- **STT Encoding**: pcm_s16le (recommended)
- **Sample Rate**: 16 kHz (recommended), 8 kHz supported
- **TTS Formats**: WAV, MP3, PCM
- **Specialty**: Real-time streaming with low latency

```python
# Cartesia WebSocket STT
cartesia_stt = {
    "model": "ink-whisper",
    "language": "en",
    "encoding": "pcm_s16le",
    "sample_rate": 16000  # Use slin16 AudioSocket profile
}
```

### 9. Voice AI Platform Alignment

#### Vapi AI

- **Supported Audio Formats**: pcm_s16le (default), mulaw
- **Configuration**: 16-bit PCM signed little-endian, μ-Law
- **AudioSocket Compatibility**: Direct slin/slin16 and ulaw support

#### Retell AI

- **Phone Calls**: Automatically handles codec differences
- **Web Calls**: PCM format internally
- **AudioSocket Integration**: Transparent codec handling

#### Bland AI

- **Audio Processing**: Custom SNAC tokenizer approach
- **Format Support**: Multiple formats with automatic optimization
- **Integration**: LLM-based approach to audio generation

## Corrected Provider Compatibility Matrix

| Provider | AudioSocket Input | AI Engine Conversion | Provider Format | Complexity |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI Realtime** | PCM16@8k | None needed | `pcm16@8k` | ✅ Direct |
| **Google Cloud** | PCM16@8k | None needed | `LINEAR16@8k` | ✅ Direct |
| **Deepgram Voice**| PCM16@8k | PCM16 → μ-law | `mulaw@8k` | 🔄 Convert |
| **Azure Speech** | PCM16@8k | None needed | `WAV/PCM@8k` | ✅ Direct |
| **ElevenLabs** | PCM16@8k | None needed | `PCM@8k` | ✅ Direct |
| **AWS Polly** | PCM16@8k | None needed | `PCM@8k` | ✅ Direct |

## Corrected Implementation Patterns

### Pattern 1: Direct PCM16 Passthrough (Optimal)

Use for providers that directly accept the PCM16 format received from AudioSocket. This is the most efficient path.

```python
# For OpenAI (pcm16), Google (LINEAR16), Azure, ElevenLabs, Polly
def direct_pcm16_path(audiosocket_packet):
    # AudioSocket data is already PCM16 - just strip the 3-byte TLV header
    pcm16_payload = audiosocket_packet[3:]
    return pcm16_payload
```

### Pattern 2: PCM16 to Compressed Codec (e.g., for Deepgram)

Use for providers that require a compressed format like μ-law. The AI Engine must perform this conversion.

```python
import audioop

def convert_pcm16_to_mulaw(audiosocket_packet):
    pcm16_payload = audiosocket_packet[3:]
    return audioop.lin2ulaw(pcm16_payload, 2) # 2 = 16-bit width
```

### Pattern 3: PCM16 Sample Rate Conversion

Use for providers that require a different sample rate than what is configured on the AudioSocket channel.

```python
from scipy import signal
import numpy as np

def resample_pcm16(audiosocket_packet, source_rate, target_rate):
    pcm16_payload = audiosocket_packet[3:]
    audio_array = np.frombuffer(pcm16_payload, dtype=np.int16)
    
    num_samples = int(len(audio_array) * target_rate / source_rate)
    resampled = signal.resample(audio_array, num_samples)
    
    return resampled.astype(np.int16).tobytes()
```

## Optimal Configurations by Use Case

### Telephony-Optimized (Low Latency - Proven Method)

- **AudioSocket**: `slin@8k` (Required PCM16 Transport)
- **Primary Providers**: Deepgram (via `mulaw` transcoding bridge), OpenAI Realtime (`pcm16`)
- **Advantage**: The `slin` <> `mulaw` transcoding is highly efficient (~0.5ms), providing a reliable low-latency path that is compatible with the AudioSocket protocol.
- **Trade-off**: Minor CPU overhead for transcoding, but this is negligible in practice.

### Quality-Optimized (Medium Latency)

- **AudioSocket**: `slin16@16k`
- **Primary Providers**: Deepgram (linear16@16k), Cartesia (pcm_s16le@16k)
- **Advantage**: Better audio quality
- **Trade-off**: Slight latency increase, more bandwidth

### Enterprise-Grade (Scalable)

- **AudioSocket**: `slin@8k`
- **Primary Providers**: Google Cloud (LINEAR16@8k), Azure (WAV@8k)
- **Advantage**: Wide provider compatibility
- **Configuration**: Flexible resampling as needed

## Common Misconceptions (Corrected)

- **❌ Misconception 1: "AudioSocket supports μ-law directly."**
  - **✅ Reality**: The AudioSocket protocol **only supports PCM16 formats** (message types `0x10-0x18`). Asterisk's internal transcoding engine automatically handles the conversion from SIP codecs (like μ-law) to PCM16 before the audio ever reaches the AudioSocket channel.

- **❌ Misconception 2: "`format=ulaw` is a valid AudioSocket configuration."**
  - **✅ Reality**: This is an invalid configuration that will cause errors. Only PCM format variants are valid (e.g., `slin`, `slin16`, `slin24`). The `ulaw` codec from a SIP trunk is handled by Asterisk's transcoding, not by the AudioSocket protocol itself.

- **❌ Misconception 3: "μ-law audio packets go directly through the AudioSocket."**
  - **✅ Reality**: The data path is always `SIP Codec (μ-law) → Asterisk Transcoding → PCM16 → AudioSocket Channel → AI Engine`. The AI engine never receives raw μ-law directly from AudioSocket.

## Testing and Validation Framework

### Audio Quality Validation

```python
def validate_audio_path(audiosocket_format, provider_format):
    """Test audio round-trip quality"""
    test_patterns = {
        "sine_wave_440hz": generate_sine_wave(440, duration=1.0),
        "speech_sample": load_test_speech(),
        "dtmf_tones": generate_dtmf_sequence("123")
    }
    
    for pattern_name, audio in test_patterns.items():
        # Send through AudioSocket → Provider → AudioSocket
        result = round_trip_test(audio, audiosocket_format, provider_format)
        quality_score = calculate_snr(audio, result)
        
        if quality_score < minimum_threshold:
            log_warning(f"Quality degradation in {pattern_name}: {quality_score}dB")
```

### Latency Measurement

```python
def measure_total_latency():
    """End-to-end latency measurement"""
    timestamp_start = time.time()
    
    # AudioSocket receive
    audio_received = timestamp_start + 0.020  # ~20ms for audio chunk
    
    # Provider processing
    provider_response = measure_provider_latency()
    
    # AudioSocket transmit
    audio_transmitted = provider_response + 0.010  # ~10ms buffer
    
    total_latency = audio_transmitted - timestamp_start
    return total_latency
```

## Troubleshooting Guide

### Common Issues and Solutions

#### 1. Audio Distortion/Quality Loss

- **Symptom**: Garbled, robotic, or "slow-motion" audio
- **Causes**: Sample rate mismatch, incorrect endianness, codec incompatibility
- **Solutions**:
  - Verify AudioSocket message type matches provider expectations
  - Check endianness alignment (AudioSocket PCM is little-endian)
  - Ensure sample rate consistency across pipeline

#### 2. High Latency

- **Symptom**: Delayed responses, conversation flow disruption
- **Causes**: Unnecessary resampling, buffering mismatches, provider processing
- **Solutions**:
  - Use direct codec alignment when possible
  - Optimize buffer sizes for streaming
  - Choose providers with proven low-latency performance

#### 3. Connection Failures

- **Symptom**: WebSocket drops, authentication errors, format rejection
- **Causes**: Unsupported format combinations, API configuration errors
- **Solutions**:
  - Validate provider format support before connection
  - Implement format fallback strategies
  - Monitor provider-specific error messages

### Diagnostic Commands

```bash
# Test AudioSocket connectivity
asterisk -rx "audiosocket list"

# Verify codec modules loaded
asterisk -rx "core show codecs"

# Monitor real-time audio flow
asterisk -rx "core set debug 1 audiosocket"
```

## Security Considerations

### Audio Data Protection

- **Encryption**: Use TLS/SRTP for audio transport when supported
- **API Security**: Rotate API keys regularly, use IAM roles where possible
- **Compliance**: Ensure HIPAA/PCI compliance for sensitive voice applications

### Privacy Controls

```python
# Audio data handling best practices
def process_audio_securely(audio_data):
    # 1. Encrypt in transit
    encrypted_audio = encrypt_audio_stream(audio_data)
    
    # 2. Process with secure providers
    result = secure_provider_api.process(encrypted_audio)
    
    # 3. Clear sensitive data
    del audio_data, encrypted_audio
    
    return result
```

## Performance Optimization

### Memory Management

- **AudioSocket Buffers**: Size buffers appropriately for chosen sample rates
- **Provider Buffers**: Align buffer sizes with provider requirements
- **Garbage Collection**: Clear audio buffers promptly to prevent memory leaks

### CPU Optimization

- **Codec Selection**: Prefer native formats to avoid transcoding overhead
- **Threading**: Process audio on dedicated threads for real-time performance
- **Caching**: Cache provider connections and authentication tokens

### Bandwidth Considerations

| Format | Sample Rate | Bandwidth (kbps) | Use Case |
|--------|-------------|------------------|----------|
| μ-law | 8 kHz | 64 | Telephony (lowest) |
| PCM 8k | 8 kHz | 128 | Quality telephony |
| PCM 16k | 16 kHz | 256 | Wideband (highest) |

## Deployment Recommendations

### Production Checklist

- [ ] Audio format compatibility tested with target providers
- [ ] Latency measurements under expected load
- [ ] Error handling for provider failures implemented
- [ ] Monitoring and alerting configured
- [ ] Security audit completed
- [ ] Scaling parameters tuned for concurrent calls

### Monitoring Metrics

```python
# Key metrics to track
metrics_to_monitor = {
    "audio_quality_score": "SNR of audio round-trips",
    "end_to_end_latency": "Total response time",
    "provider_availability": "Uptime percentage",
    "transcoding_cpu_usage": "CPU overhead for format conversion",
    "concurrent_sessions": "Active AudioSocket connections"
}
```

## Future Considerations

### Emerging Standards

- **WebRTC Integration**: Growing support for WebRTC-compatible formats
- **Enhanced Codecs**: Opus, Enhanced Voice Services (EVS) adoption
- **Edge Computing**: Local processing to reduce latency

### Provider Ecosystem Evolution

- **Multi-Provider Strategies**: Fallback chains for reliability
- **Specialized Models**: Domain-specific voice models (medical, legal, etc.)
- **Cost Optimization**: Dynamic provider selection based on usage patterns

## Conclusion

Successful AudioSocket integration with AI providers requires careful consideration of codec alignment, latency optimization, and quality preservation. By following the patterns and configurations outlined in this document, developers can build robust voice agent systems that leverage the strengths of different providers while maintaining consistent performance.

The key to success is thorough testing of the complete audio pipeline, from AudioSocket ingress through provider processing to AudioSocket egress, ensuring that each component maintains audio fidelity while minimizing latency and resource usage.

---

*This document should be updated as new providers emerge and existing providers evolve their audio format support. Regular testing and validation of provider configurations ensures continued compatibility and optimal performance.*
