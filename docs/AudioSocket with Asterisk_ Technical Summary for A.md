# AudioSocket with Asterisk: Technical Summary for AI Voice Agents

## AudioSocket Transport Profiles & Packet Framing

- Packet framing uses a TLV header: 1‑byte `type`, 2‑byte `length` in network byte order (big‑endian), then payload. The common audio payload is 16‑bit PCM. [^2]

- In practice, the wire format is selected by the dialplan media argument to `AudioSocket(...)`, and Asterisk will transcode between the SIP leg and the AudioSocket leg. Common, proven profiles:

| Profile | Encoding | Sample Rate | Channels | Byte Order (payload) |
| :-- | :-- | :-- | :-- | :-- |
| μ‑law telephony | ulaw | 8 kHz | mono | n/a (8‑bit companded) |
| PCM telephony | slin | 8 kHz | mono | little‑endian (LE) |
| PCM wideband | slin16 | 16 kHz | mono | little‑endian (LE) |

Notes:

- Per the official Asterisk documentation, AudioSocket audio message types (0x10–0x18) carry signed 16‑bit PCM in little‑endian byte order. The TLV header's 2‑byte length is big‑endian, but the audio payload is always little‑endian. Do not confuse header endianness with payload endianness. [^asterisk-doc]

### Message Types and Endianness (from Asterisk)

| Type | Meaning |
| :--- | :------ |
| `0x00` | Terminate the connection |
| `0x01` | UUID handshake (16‑byte binary UUID) |
| `0x03` | DTMF digit (payload: 1 ASCII byte) |
| `0x10` | PCM16 mono, 8 kHz, signed little‑endian |
| `0x11` | PCM16 mono, 12 kHz, signed little‑endian |
| `0x12` | PCM16 mono, 16 kHz, signed little‑endian |
| `0x13` | PCM16 mono, 24 kHz, signed little‑endian |
| `0x14` | PCM16 mono, 32 kHz, signed little‑endian |
| `0x15` | PCM16 mono, 44.1 kHz, signed little‑endian |
| `0x16` | PCM16 mono, 48 kHz, signed little‑endian |
| `0x17` | PCM16 mono, 96 kHz, signed little‑endian |
| `0x18` | PCM16 mono, 192 kHz, signed little‑endian |
| `0xFF` | Error (payload may contain an app‑specific error code) |

Framing reminder: each packet begins with a 1‑byte type and a 2‑byte big‑endian payload length, followed by the payload. Audio payloads for `0x10`–`0x18` are always 16‑bit little‑endian PCM samples. [^asterisk-doc]

***

## Transcoding Behavior in Asterisk

When a call uses a different codec (e.g., G.711 μ‑law, A‑law, GSM, Opus), Asterisk will **automatically transcode** between the SIP leg and the AudioSocket leg:

```text
SIP Call (ulaw/alaw/other) → Asterisk Transcoding → AudioSocket (ulaw|slin@8k|slin16@16k)
```

- **Incoming to AudioSocket**: Asterisk converts the SIP leg to the selected AudioSocket profile (e.g., μ‑law@8k or PCM16@8k/16k) before sending to the engine.
- **Outgoing from AudioSocket**: The engine streams in the selected profile and Asterisk transcodes to the endpoint’s negotiated codec (e.g., μ‑law@8k) for the SIP leg.
- **No configuration needed**: Transcoding happens automatically if codec modules are loaded.

***

## Using AudioSocket with OpenAI Realtime API

### OpenAI Realtime Requirements

- **Supported formats**: `pcm16` (16-bit PCM) or `g711_ulaw`/`g711_alaw`
- **Sample rates**: 24 kHz (preferred), 16 kHz, or 8 kHz
- **Channels**: Mono

### Integration Strategy

#### Option 1: Direct 8 kHz PCM (Simplest)

```python
# AudioSocket outputs 8 kHz 16-bit PCM
# OpenAI Realtime accepts this directly

openai_config = {
    "modalities": ["audio", "text"],
    "input_audio_format": "pcm16",
    "output_audio_format": "pcm16",
    "input_audio_transcription": {
        "model": "whisper-1"
    }
}

# Strip AudioSocket 3-byte header, send payload directly
async def forward_to_openai(audiosocket_packet):
    if packet_type == 0x10:  # Audio
        audio_payload = packet[3:]  # Remove header
        await openai_ws.send(audio_payload)  # Send raw PCM
```

#### Option 2: Upsample to 24 kHz (Better Quality)

```python
from scipy import signal
import numpy as np

def upsample_8khz_to_24khz(audio_8khz):
    """Upsample AudioSocket's 8 kHz to OpenAI's preferred 24 kHz"""
    audio_array = np.frombuffer(audio_8khz, dtype=np.int16)
    upsampled = signal.resample(audio_array, len(audio_array) * 3)
    return upsampled.astype(np.int16).tobytes()

# Use in pipeline
audio_payload = packet[3:]
upsampled_audio = upsample_8khz_to_24khz(audio_payload)
await openai_ws.send(upsampled_audio)
```

**Critical**: OpenAI Realtime expects **raw PCM samples** without WAV headers. AudioSocket provides exactly this after header stripping. Always resample/byte‑swap as required by the provider session before send. [^2]

***

## Using AudioSocket with Deepgram Voice Agent

### Deepgram Voice Agent Requirements

- **Supported formats**: `linear16` (16-bit PCM), `mulaw`, `alaw`
- **Sample rates**: 8 kHz, 16 kHz, 24 kHz, 48 kHz
- **Channels**: Mono or stereo (mono recommended)

### Deepgram Integration Strategy

#### Direct 8 kHz PCM (Recommended when the selected voice supports it)

```python
# Deepgram Voice Agent WebSocket configuration
deepgram_url = "wss://agent.deepgram.com/agent"

config = {
    "type": "SettingsConfiguration",
    "audio": {
        "input": {
            "encoding": "linear16",      # Matches AudioSocket
            "sample_rate": 8000,         # CRITICAL: Must match AudioSocket
            "channels": 1
        },
        "output": {
            "format": {
                "encoding": "linear16",  # Voice output format
                "sample_rate": 8000       # Request 8 kHz when supported
            }
        }
    },
    "agent": {
        "listen": {
            "model": "nova-2-phonecall"  # Optimized for 8 kHz telephony
        },
        "think": {
            "provider": {"type": "open_ai"},
            "model": "gpt-4"
        },
        "speak": {
            "model": "aura-asteria-en"
        }
    }
}

# Forward AudioSocket audio to Deepgram
async def forward_to_deepgram(packet):
    packet_type = packet[0]
    payload_length = struct.unpack('>H', packet[1:3])[0]
    
    if packet_type == 0x10:  # Audio
        audio_payload = packet[3:3+payload_length]
        await deepgram_ws.send(audio_payload)  # Send raw PCM
```

If the selected voice cannot synthesize linear16@8k, request μ‑law@8k instead (commonly supported by telephony voices), or accept linear16@24k and resample to your transport rate in the engine. Validate the final format using the server’s `SettingsApplied` ACK. [^3][^4]

**Why 8 kHz Works Best**:

- Deepgram's `nova-2-phonecall` model is specifically trained on telephony audio (8 kHz)[^3]
- No resampling needed = lower latency
- Matches AudioSocket's native format perfectly[^2][^asterisk-doc]

***

### Complete AudioSocket Packet Parsing (TLV header)

```python
import struct
import asyncio

class AudioSocketParser:
    def __init__(self):
        self.buffer = bytearray()
    
    async def process_data(self, data):
        """Parse AudioSocket packets and extract audio payloads"""
        self.buffer.extend(data)
        packets = []
        
        while len(self.buffer) >= 3:
            # Parse header
            packet_type = self.buffer[0]
            payload_length = struct.unpack('>H', bytes(self.buffer[1:3]))[0]
            
            # Wait for complete packet
            if len(self.buffer) < 3 + payload_length:
                break
            
            # Extract payload
            payload = bytes(self.buffer[3:3 + payload_length])
            self.buffer = self.buffer[3 + payload_length:]
            
            # Handle packet types
            if packet_type == 0x00:  # Terminate
                return None
            elif packet_type == 0x01:  # UUID
                uuid = payload.hex()
                print(f"Call UUID: {uuid}")
            elif packet_type == 0x10:  # Audio (THIS IS WHAT YOU SEND TO AI)
                # For PCM16 profiles, probe endianness once and byteswap if needed
                packets.append(payload)
            elif packet_type == 0x03:  # DTMF
                dtmf = payload.decode('ascii')
                print(f"DTMF: {dtmf}")
        
        return packets
```

***

### Summary: Exact Technical Requirements

| Component | Format | Sample Rate | Bit Depth | Channels | Endianness |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **AudioSocket (telephony)** | ulaw | 8 kHz | 8‑bit companded | Mono | n/a |
| **AudioSocket (PCM)** | slin | 8 kHz | 16‑bit | Mono | little‑endian |
| **AudioSocket (PCM wideband)** | slin16 | 16 kHz | 16‑bit | Mono | probe; auto‑swap if needed |
| **OpenAI Realtime Input** | pcm16 | 8/16/24 kHz | 16‑bit | Mono | little‑endian |
| **Deepgram Voice Agent Input** | linear16/μ‑law | 8 kHz (telephony), 16/24 kHz (PCM voices) | 16‑bit | Mono | little‑endian |

**Critical Rules**:

1. **Strip the 3‑byte TLV header** before sending to AI providers. [^2]
2. **Probe PCM16 endianness** on the first frame; auto‑swap if `RMS(swapped) ≫ RMS(native)`. This prevents greeting “static” on systems that deliver big‑endian PCM over `slin16`.
3. **Align provider sessions**: request formats the voice supports (e.g., μ‑law@8k or linear16@24k) and resample at the engine edge as needed. Confirm with `SettingsApplied` ACK. [^3][^4]
4. **Never send WAV headers**—providers expect raw frames.
5. **Two supported transport profiles**: ulaw@8k or PCM (slin@8k/slin16@16k). Pick one and keep the pipeline consistent.

**Common Mistakes**:

- Misreading header endianness: TLV length is big‑endian, but audio payload for PCM types is always little‑endian per Asterisk. Do not swap bytes for AudioSocket PCM types.
- Forcing 8 kHz linear PCM on a voice that only supports 24 kHz PCM or 8 kHz μ‑law. Request a supported format and resample at the transport boundary. [^3][^4]

This configuration ensures **low latency** and **reliable real‑time streaming** for AI voice agents using Asterisk AudioSocket, while acknowledging real‑world profiles (8/16 kHz PCM and μ‑law) and provider constraints. [^2][^3][^4]

***

[^2]: K3XEC — "Overview of the AudioSocket protocol" (TLV header, header big‑endian; audio payload int16 LE @ 8 kHz). <https://k3xec.com/audio-socket/>
[^3]: Deepgram Voice Agent v1 Reference — Settings/ACK schema and audio format nesting. <https://developers.deepgram.com/reference/voice-agent/agent-v-1>
[^4]: Deepgram — Supported audio formats. <https://developers.deepgram.com/docs/supported-audio-formats>

[^asterisk-doc]: Asterisk — AudioSocket channel driver documentation (official). Message types, payload format (PCM16 little‑endian), and TLV framing. <https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/>
