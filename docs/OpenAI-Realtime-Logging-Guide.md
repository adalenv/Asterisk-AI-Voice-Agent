# OpenAI Realtime API Logging Instructions for Voice Agent Testing

## Overview

This guide provides comprehensive instructions for capturing and analyzing OpenAI Realtime API logs during voice agent testing. These logs will help you understand audio encoding/decoding, session configuration, and conversation flow for troubleshooting and optimization.

## Quick Start Summary

**Key Events to Log:**

- `session.created` - Initial configuration
- `session.updated` - Configuration changes
- `input_audio_buffer.append` - Incoming audio details
- `conversation.item.created` - User/assistant messages with audio format
- `response.audio.delta` - Outgoing audio chunks
- `conversation.item.input_audio_transcription.completed` - User transcript
- `response.audio_transcript.delta` - Assistant transcript

## Prerequisites

### Environment Setup

```bash
# Set environment variables
export OPENAI_API_KEY="your-api-key-here"
export OPENAI_LOG="debug"  # Enable debug logging

# Install required packages (Python)
pip install openai websocket-client

# OR for Node.js
npm install ws dotenv
```

### Required Imports

**Python:**

```python
import json
import logging
import os
from datetime import datetime
import websocket

# Configure logging
logging.basicConfig(
    filename=f'openai_realtime_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
```

**Node.js:**

```javascript
const WebSocket = require('ws');
const fs = require('fs');

const logFile = `openai_realtime_logs_${Date.now()}.json`;
const logStream = fs.createWriteStream(logFile, { flags: 'a' });
```

## Implementation: Comprehensive Logging System

### Step 1: Create Event Logger Class (Python)

```python
class RealtimeAPILogger:
    """Comprehensive logger for OpenAI Realtime API events"""
    
    def __init__(self, log_file_prefix="realtime_api"):
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = f"{log_file_prefix}_{self.timestamp}.jsonl"
        self.session_config = {}
        self.audio_stats = {
            "input_chunks": 0,
            "output_chunks": 0,
            "total_input_bytes": 0,
            "total_output_bytes": 0
        }
        
    def log_event(self, event_type, event_data):
        """Log event with metadata"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "event_data": event_data
        }
        
        # Write to JSONL file for easy parsing
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
        
        # Also log to console
        logger.info(f"Event: {event_type}")
        
        # Track specific metrics
        self._update_metrics(event_type, event_data)
    
    def _update_metrics(self, event_type, event_data):
        """Update tracking metrics"""
        if event_type == "session.created" or event_type == "session.updated":
            self.session_config = event_data.get("session", {})
            self._log_session_config()
        
        elif event_type == "input_audio_buffer.append":
            self.audio_stats["input_chunks"] += 1
            # Audio is base64 encoded, approximate bytes
            audio_data = event_data.get("audio", "")
            self.audio_stats["total_input_bytes"] += len(audio_data) * 3 // 4
        
        elif event_type == "response.audio.delta":
            self.audio_stats["output_chunks"] += 1
            audio_data = event_data.get("delta", "")
            self.audio_stats["total_output_bytes"] += len(audio_data) * 3 // 4
    
    def _log_session_config(self):
        """Extract and log critical session configuration"""
        config = self.session_config
        
        critical_config = {
            "model": config.get("model"),
            "voice": config.get("voice"),
            "input_audio_format": config.get("input_audio_format"),
            "output_audio_format": config.get("output_audio_format"),
            "input_audio_transcription": config.get("input_audio_transcription"),
            "turn_detection": config.get("turn_detection"),
            "modalities": config.get("modalities"),
            "temperature": config.get("temperature"),
            "max_response_output_tokens": config.get("max_response_output_tokens")
        }
        
        logger.info("=" * 80)
        logger.info("SESSION CONFIGURATION")
        logger.info("=" * 80)
        for key, value in critical_config.items():
            logger.info(f"{key}: {value}")
        logger.info("=" * 80)
    
    def get_summary(self):
        """Generate summary of logged session"""
        return {
            "log_file": self.log_file,
            "session_config": self.session_config,
            "audio_stats": self.audio_stats,
            "input_format": self.session_config.get("input_audio_format"),
            "output_format": self.session_config.get("output_audio_format"),
            "total_input_mb": self.audio_stats["total_input_bytes"] / (1024 * 1024),
            "total_output_mb": self.audio_stats["total_output_bytes"] / (1024 * 1024)
        }
```

### Step 2: WebSocket Connection with Logging

```python
import websocket
import json
import base64

def create_logged_realtime_connection(api_key, logger):
    """Create WebSocket connection with comprehensive logging"""
    
    url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
    
    def on_open(ws):
        logger.log_event("connection.opened", {"url": url})
        print("✓ Connected to OpenAI Realtime API")
        
        # Send session configuration
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": "You are a helpful voice assistant.",
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 200
                }
            }
        }
        ws.send(json.dumps(session_update))
        logger.log_event("session.update_sent", session_update)
    
    def on_message(ws, message):
        try:
            event = json.loads(message)
            event_type = event.get("type")
            
            # Log ALL events
            logger.log_event(event_type, event)
            
            # Special handling for critical events
            if event_type == "session.created":
                print("\n✓ Session created")
                print(f"  Model: {event['session'].get('model')}")
                print(f"  Input Format: {event['session'].get('input_audio_format')}")
                print(f"  Output Format: {event['session'].get('output_audio_format')}")
            
            elif event_type == "session.updated":
                print("\n✓ Session updated")
            
            elif event_type == "input_audio_buffer.speech_started":
                print(f"\n🎤 User started speaking (item_id: {event.get('item_id')})")
            
            elif event_type == "input_audio_buffer.speech_stopped":
                print(f"🎤 User stopped speaking")
            
            elif event_type == "conversation.item.created":
                item = event.get("item", {})
                role = item.get("role")
                content = item.get("content", [])
                print(f"\n💬 Conversation item: {role}")
                for c in content:
                    if c.get("type") == "input_audio":
                        print(f"   Audio transcript: {c.get('transcript', 'pending...')}")
                    elif c.get("type") == "text":
                        print(f"   Text: {c.get('text', '')[:100]}")
            
            elif event_type == "conversation.item.input_audio_transcription.completed":
                print(f"\n📝 User transcript: {event.get('transcript')}")
            
            elif event_type == "response.audio_transcript.delta":
                print(event.get("delta", ""), end="", flush=True)
            
            elif event_type == "response.audio_transcript.done":
                print("\n✓ Transcript complete")
            
            elif event_type == "response.audio.delta":
                # Don't print audio data, just track it
                logger.audio_stats["output_chunks"] += 1
            
            elif event_type == "error":
                print(f"\n❌ ERROR: {event.get('error', {})}")
        
        except Exception as e:
            logger.log_event("error.parsing", {"error": str(e), "message": message[:200]})
            print(f"Error parsing message: {e}")
    
    def on_error(ws, error):
        logger.log_event("connection.error", {"error": str(error)})
        print(f"❌ WebSocket error: {error}")
    
    def on_close(ws, close_status_code, close_msg):
        logger.log_event("connection.closed", {
            "status_code": close_status_code,
            "message": close_msg
        })
        print(f"\n✓ Connection closed (code: {close_status_code})")
        
        # Print summary
        summary = logger.get_summary()
        print("\n" + "=" * 80)
        print("SESSION SUMMARY")
        print("=" * 80)
        print(f"Log file: {summary['log_file']}")
        print(f"Input format: {summary['input_format']}")
        print(f"Output format: {summary['output_format']}")
        print(f"Input chunks: {summary['audio_stats']['input_chunks']}")
        print(f"Output chunks: {summary['audio_stats']['output_chunks']}")
        print(f"Total input: {summary['total_input_mb']:.2f} MB")
        print(f"Total output: {summary['total_output_mb']:.2f} MB")
        print("=" * 80)
    
    # Create WebSocket connection
    ws = websocket.WebSocketApp(
        url,
        header=[
            f"Authorization: Bearer {api_key}",
            "OpenAI-Beta: realtime=v1"
        ],
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    return ws, logger

#### Usage
if __name__ == "__main__":
    api_key = os.environ.get("OPENAI_API_KEY")
    logger = RealtimeAPILogger(log_file_prefix="test_call")
    
    ws, logger = create_logged_realtime_connection(api_key, logger)
    ws.run_forever()
```

### Step 3: Node.js Implementation

```javascript
const WebSocket = require('ws');
const fs = require('fs');

class RealtimeAPILogger {
    constructor(logFilePrefix = 'realtime_api') {
        this.timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        this.logFile = `${logFilePrefix}_${this.timestamp}.jsonl`;
        this.sessionConfig = {};
        this.audioStats = {
            inputChunks: 0,
            outputChunks: 0,
            totalInputBytes: 0,
            totalOutputBytes: 0
        };
    }

    logEvent(eventType, eventData) {
        const logEntry = {
            timestamp: new Date().toISOString(),
            eventType,
            eventData
        };

        // Write to JSONL file
        fs.appendFileSync(this.logFile, JSON.stringify(logEntry) + '\n');
        
        console.log(`[${eventType}]`);
        
        this.updateMetrics(eventType, eventData);
    }

    updateMetrics(eventType, eventData) {
        if (eventType === 'session.created' || eventType === 'session.updated') {
            this.sessionConfig = eventData.session || {};
            this.logSessionConfig();
        } else if (eventType === 'input_audio_buffer.append') {
            this.audioStats.inputChunks++;
            const audioData = eventData.audio || '';
            this.audioStats.totalInputBytes += (audioData.length * 3) / 4;
        } else if (eventType === 'response.audio.delta') {
            this.audioStats.outputChunks++;
            const audioData = eventData.delta || '';
            this.audioStats.totalOutputBytes += (audioData.length * 3) / 4;
        }
    }

    logSessionConfig() {
        const config = this.sessionConfig;
        console.log('='.repeat(80));
        console.log('SESSION CONFIGURATION');
        console.log('='.repeat(80));
        console.log(`Model: ${config.model}`);
        console.log(`Voice: ${config.voice}`);
        console.log(`Input Audio Format: ${config.input_audio_format}`);
        console.log(`Output Audio Format: ${config.output_audio_format}`);
        console.log(`Modalities: ${config.modalities}`);
        console.log('='.repeat(80));
    }

    getSummary() {
        return {
            logFile: this.logFile,
            sessionConfig: this.sessionConfig,
            audioStats: this.audioStats,
            inputFormat: this.sessionConfig.input_audio_format,
            outputFormat: this.sessionConfig.output_audio_format,
            totalInputMB: this.audioStats.totalInputBytes / (1024 * 1024),
            totalOutputMB: this.audioStats.totalOutputBytes / (1024 * 1024)
        };
    }
}

// Create logged connection
function createLoggedRealtimeConnection(apiKey, logger) {
    const url = 'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17';
    
    const ws = new WebSocket(url, {
        headers: {
            'Authorization': `Bearer ${apiKey}`,
            'OpenAI-Beta': 'realtime=v1'
        }
    });

    ws.on('open', () => {
        logger.logEvent('connection.opened', { url });
        console.log('✓ Connected to OpenAI Realtime API');

        // Send session configuration
        const sessionUpdate = {
            type: 'session.update',
            session: {
                modalities: ['text', 'audio'],
                instructions: 'You are a helpful voice assistant.',
                voice: 'alloy',
                input_audio_format: 'pcm16',
                output_audio_format: 'pcm16',
                input_audio_transcription: {
                    model: 'whisper-1'
                },
                turn_detection: {
                    type: 'server_vad',
                    threshold: 0.5,
                    prefix_padding_ms: 300,
                    silence_duration_ms: 200
                }
            }
        };
        ws.send(JSON.stringify(sessionUpdate));
        logger.logEvent('session.update_sent', sessionUpdate);
    });

    ws.on('message', (data) => {
        try {
            const event = JSON.parse(data.toString());
            const eventType = event.type;

            // Log ALL events
            logger.logEvent(eventType, event);

            // Console output for key events
            if (eventType === 'session.created') {
                console.log('\n✓ Session created');
                console.log(`  Model: ${event.session.model}`);
                console.log(`  Input Format: ${event.session.input_audio_format}`);
                console.log(`  Output Format: ${event.session.output_audio_format}`);
            } else if (eventType === 'conversation.item.input_audio_transcription.completed') {
                console.log(`\n📝 User transcript: ${event.transcript}`);
            } else if (eventType === 'response.audio_transcript.delta') {
                process.stdout.write(event.delta);
            } else if (eventType === 'error') {
                console.log(`\n❌ ERROR: ${JSON.stringify(event.error)}`);
            }
        } catch (e) {
            logger.logEvent('error.parsing', { error: e.message });
        }
    });

    ws.on('close', (code, reason) => {
        logger.logEvent('connection.closed', { code, reason: reason.toString() });
        console.log(`\n✓ Connection closed (code: ${code})`);

        // Print summary
        const summary = logger.getSummary();
        console.log('\n' + '='.repeat(80));
        console.log('SESSION SUMMARY');
        console.log('='.repeat(80));
        console.log(`Log file: ${summary.logFile}`);
        console.log(`Input format: ${summary.inputFormat}`);
        console.log(`Output format: ${summary.outputFormat}`);
        console.log(`Input chunks: ${summary.audioStats.inputChunks}`);
        console.log(`Output chunks: ${summary.audioStats.outputChunks}`);
        console.log(`Total input: ${summary.totalInputMB.toFixed(2)} MB`);
        console.log(`Total output: ${summary.totalOutputMB.toFixed(2)} MB`);
        console.log('='.repeat(80));
    });

    ws.on('error', (error) => {
        logger.logEvent('connection.error', { error: error.message });
        console.log(`❌ WebSocket error: ${error.message}`);
    });

    return ws;
}

// Usage
const apiKey = process.env.OPENAI_API_KEY;
const logger = new RealtimeAPILogger('test_call');
const ws = createLoggedRealtimeConnection(apiKey, logger);
```

## Step 4: Test Call Procedure

### Making a Test Call

1. **Start the logging script:**

   ```bash
   python realtime_logger.py
   # OR
   node realtime_logger.js
   ```

```text

2. **Wait for connection:**
   - Look for "✓ Connected to OpenAI Realtime API"
   - Verify session configuration is logged

3. **Speak into microphone:**
   - Send audio via `input_audio_buffer.append` events
   - Watch for speech detection events

4. **Observe responses:**
   - Monitor transcript deltas
   - Track audio output chunks

5. **End session:**
   - Close connection gracefully
   - Review summary statistics

## Step 5: Log Analysis Tools

### Extract Audio Configuration from Logs

```

```python
def analyze_session_logs(log_file):
    """Analyze logged session for configuration details"""

    with open(log_file, 'r') as f:
        events = [json.loads(line) for line in f]
    
    # Find session configuration
    session_events = [e for e in events if e['event_type'] in ['session.created', 'session.updated']]
    
    if session_events:
        config = session_events[-1]['event_data'].get('session', {})
        
        print("AUDIO CONFIGURATION ANALYSIS")
        print("=" * 80)
        print(f"Model: {config.get('model')}")
        print(f"Input Audio Format: {config.get('input_audio_format')}")
        print(f"Output Audio Format: {config.get('output_audio_format')}")
        print(f"Sample Rate: {get_sample_rate(config.get('input_audio_format'))}")
        print(f"Encoding: {get_encoding(config.get('input_audio_format'))}")
        print(f"Turn Detection: {config.get('turn_detection', {}).get('type')}")
        print(f"Transcription Model: {config.get('input_audio_transcription', {}).get('model')}")
        print("=" * 80)
    
    # Analyze conversation flow
    conversation_items = [e for e in events if e['event_type'] == 'conversation.item.created']
    print(f"\nTotal conversation items: {len(conversation_items)}")
    
    for item in conversation_items:
        data = item['event_data'].get('item', {})
        role = data.get('role')
        content = data.get('content', [])
        print(f"\n{role.upper()}:")
        for c in content:
            if c.get('type') == 'input_audio':
                print(f"  [Audio] Transcript: {c.get('transcript', 'N/A')}")
            elif c.get('type') == 'audio':
                print(f"  [Audio] Transcript: {c.get('transcript', 'N/A')}")
            elif c.get('type') == 'text':
                print(f"  [Text] {c.get('text', '')[:100]}")

def get_sample_rate(audio_format):
    """Determine sample rate from format"""
    if 'pcm16' in audio_format or 'g711' in audio_format:
        return '24kHz (default for pcm16)' if 'pcm16' in audio_format else '8kHz (G.711)'
    return 'Unknown'

def get_encoding(audio_format):
    """Determine encoding from format"""
    format_map = {
        'pcm16': '16-bit PCM, little-endian',
        'g711_ulaw': 'G.711 μ-law',
        'g711_alaw': 'G.711 A-law'
    }
    return format_map.get(audio_format, 'Unknown')
```

### Usage (Analyzer)

analyze_session_logs('realtime_api_20251025_120000.jsonl')

```text

### Generate Event Timeline

```python
def generate_event_timeline(log_file, output_file='timeline.txt'):
    """Generate human-readable timeline of events"""

    with open(log_file, 'r') as f:
        events = [json.loads(line) for line in f]
    
    with open(output_file, 'w') as out:
        out.write("OPENAI REALTIME API EVENT TIMELINE\n")
        out.write("=" * 80 + "\n\n")
        
        for event in events:
            timestamp = event['timestamp']
            event_type = event['event_type']
            
            # Format event description
            if event_type == 'session.created':
                config = event['event_data'].get('session', {})
                out.write(f"[{timestamp}] SESSION CREATED\n")
                out.write(f"  Model: {config.get('model')}\n")
                out.write(f"  Input: {config.get('input_audio_format')}\n")
                out.write(f"  Output: {config.get('output_audio_format')}\n\n")
            
            elif event_type == 'input_audio_buffer.speech_started':
                out.write(f"[{timestamp}] 🎤 User started speaking\n\n")
            
            elif event_type == 'input_audio_buffer.speech_stopped':
                out.write(f"[{timestamp}] 🎤 User stopped speaking\n\n")
            
            elif event_type == 'conversation.item.input_audio_transcription.completed':
                transcript = event['event_data'].get('transcript', '')
                out.write(f"[{timestamp}] 📝 USER: {transcript}\n\n")
            
            elif event_type == 'response.audio_transcript.done':
                transcript = event['event_data'].get('transcript', '')
                out.write(f"[{timestamp}] 🤖 ASSISTANT: {transcript}\n\n")
            
            elif event_type == 'error':
                error = event['event_data'].get('error', {})
                out.write(f"[{timestamp}] ❌ ERROR: {error}\n\n")
    
    print(f"Timeline generated: {output_file}")
```

### Usage (Timeline Generator)

generate_event_timeline('realtime_api_20251025_120000.jsonl')

## Key Events Reference

### Critical Events for Audio Analysis

| Event Type | Description | Key Fields |
|------------|-------------|------------|
| `session.created` | Initial session config | `input_audio_format`, `output_audio_format`, `voice` |
| `session.updated` | Config changes | Same as above |
| `input_audio_buffer.append` | Audio data sent | `audio` (base64) |
| `input_audio_buffer.speech_started` | VAD detected speech | `audio_start_ms`, `item_id` |
| `input_audio_buffer.speech_stopped` | VAD stopped detecting | `audio_end_ms` |
| `conversation.item.created` | New conversation item | `item.content.type`, `item.role` |
| `conversation.item.input_audio_transcription.completed` | User transcript ready | `transcript`, `item_id` |
| `response.audio.delta` | Audio output chunk | `delta` (base64) |
| `response.audio_transcript.delta` | Transcript streaming | `delta` (text) |
| `response.audio_transcript.done` | Complete transcript | `transcript` |

### Audio Format Values

- `pcm16` - 16-bit PCM, 24kHz, little-endian (default)
- `g711_ulaw` - G.711 μ-law, 8kHz
- `g711_alaw` - G.711 A-law, 8kHz

## Troubleshooting Common Issues

### Issue 1: Missing Transcripts

**Symptom:** `transcript` field is always `null`

**Solution:**

#### Ensure input_audio_transcription is configured

```python
session_update = {
    "type": "session.update",
    "session": {
        "input_audio_transcription": {
            "model": "whisper-1"  # Required!
        }
    }
}
```

### Issue 2: Audio Format Mismatches

**Symptom:** Distorted or "slow" audio

**Check logs for:**

- `input_audio_format` vs actual audio sent
- Sample rate consistency
- Endianness (PCM16 must be little-endian)

### Issue 3: VAD Not Triggering

**Check logs for:**

- `input_audio_buffer.speech_started` events
- VAD configuration: `threshold`, `silence_duration_ms`
- Audio volume levels

## Integration with Your Asterisk Project

### Adapting for AudioSocket → OpenAI Flow

```python
def log_audiosocket_to_openai(audiosocket_packet, realtime_logger):
    """Log AudioSocket → OpenAI conversion"""

    # Extract AudioSocket PCM16 data
    msg_type = audiosocket_packet[0]
    length = struct.unpack('>H', audiosocket_packet[1:3])[0]
    pcm16_data = audiosocket_packet[3:3+length]
    
    # Log conversion
    realtime_logger.log_event("audiosocket.received", {
        "message_type": f"0x{msg_type:02x}",
        "length": length,
        "sample_rate": "8kHz" if msg_type == 0x10 else "16kHz",
        "format": "PCM16 little-endian"
    })
    
    # Convert to base64 for OpenAI (if needed)
    import base64
    audio_base64 = base64.b64encode(pcm16_data).decode('utf-8')
    
    # Send to OpenAI
    openai_event = {
        "type": "input_audio_buffer.append",
        "audio": audio_base64
    }
    
    realtime_logger.log_event("openai.audio_sent", {
        "format": "pcm16",
        "base64_length": len(audio_base64),
        "original_bytes": length
    })
    
    return openai_event
```

## Post-Test Analysis Checklist

After making test calls, analyze logs for:

- [ ] **Session Configuration**
  - Input/output audio formats
  - Voice model used
  - Turn detection settings
  
- [ ] **Audio Flow**
  - Number of input/output chunks
  - Total data transferred
  - Sample rate consistency
  
- [ ] **Transcription**
  - User transcript accuracy
  - Assistant transcript timing
  - Transcription model used
  
- [ ] **Conversation Flow**
  - Turn detection latency
  - Interruption handling
  - Response generation time
  
- [ ] **Error Events**
  - Any error types
  - Error frequency
  - Error recovery

## Summary

This comprehensive logging system captures all critical OpenAI Realtime API events, with special attention to audio encoding/decoding configuration. The logs will help you:

1. **Verify audio format compatibility** between AudioSocket and OpenAI
2. **Diagnose quality issues** through format analysis
3. **Optimize latency** by analyzing event timing
4. **Debug transcription problems** with detailed event logging
5. **Track resource usage** with audio chunk statistics

All logs are saved in JSONL format for easy parsing and analysis, with human-readable console output during testing.

---

**Next Steps:**

1. Run test calls using the logging script
2. Analyze generated log files
3. Compare configurations against your Deepgram baseline
4. Optimize AudioSocket → OpenAI format conversions
