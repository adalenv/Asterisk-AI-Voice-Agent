# External Media Pipeline Test Guide

## Overview

This test script verifies the complete audio pipeline using External Media channels instead of AudioSocket. It tests:

- ARI connection and Stasis app registration
- External Media channel creation
- Audio capture simulation
- Local AI Server integration (STT→LLM→TTS)
- Audio playback via ARI

## Prerequisites

1. **Asterisk running** with ARI enabled
2. **Local AI Server running** on port 8765
3. **Dialplan context** `from-audio-test` configured:

   ```asterisk
   [from-audio-test]
   exten => s,1,NoOp(Starting Audio Test External Media)
   same => n,Answer()
   same => n,Stasis(audio-test)
   same => n,Hangup()
   ```

## Usage

### Basic Test (Call Flow)

```bash
python3 scripts/test_externalmedia_call.py
```

### Deployment Sanity (RTP + ARI wiring)

```bash
python3 scripts/test_externalmedia_deployment.py
```

### With Custom Health Endpoint (non-default URL)

```bash
python3 scripts/test_externalmedia_call.py --url http://127.0.0.1:15000/health
```

## What the Test Does

1. **Connects to ARI** - Establishes WebSocket connection for event handling
2. **Connects to Local AI Server** - WebSocket connection for STT→LLM→TTS processing
3. **Waits for StasisStart** - Listens for calls to the `audio-test` Stasis app
4. **Creates External Media Channel** - Sets up RTP channel for audio capture
5. **Plays Greeting** - Plays "demo-congrats" sound file
6. **Simulates Audio Capture** - Sends test messages to Local AI Server every 15 seconds
7. **Handles AI Responses** - Receives and plays TTS audio back to caller
8. **Tracks Statistics** - Monitors events, errors, and performance

## Debug Features

### Detailed Logging

- Function names and line numbers in log messages
- Audio data hex dumps for troubleshooting
- Full ARI event details
- WebSocket connection states
- Error type identification

### Statistics Tracking

- Events received count
- Channels created count
- Audio messages sent/received
- Error count
- Active call details

### Periodic Reporting

- Statistics printed every 30 seconds in debug mode
- Final statistics on exit
- Real-time monitoring of system state

## Expected Output

### Successful Test

```
🚀 Starting External Media Pipeline Test
============================================================
📞 This test will work with your 'from-audio-test' dialplan
📞 Dial the extension that routes to 'from-audio-test' context
📞 You should hear a greeting and then be able to speak
📞 Audio will be captured and processed by Local AI Server
============================================================
✅ Connected to Local AI Server
🔌 Starting WebSocket listener: ws://127.0.0.1:8088/ari/events?app=audio-test&api_key=asterisk:asterisk
✅ WebSocket connected successfully
🎯 Ready for test call!
```

### During Call

```
🚀 StasisStart received for channel: 1757899326.70
   Channel Name: SIP/callcentricB15-00000014
   Channel State: Up
✅ Channel 1757899326.70 answered successfully
🔗 Creating External Media channel for 1757899326.70
✅ External Media channel created: 1757899326.71
🎵 Playing greeting on channel 1757899326.70
🎤 Starting audio capture simulation for channel: 1757899326.71
```

## Troubleshooting

### Common Issues

1. **"Connection refused to ARI WebSocket"**
   - Check if Asterisk is running
   - Verify ARI is enabled in `http.conf` and `ari.conf`
   - Check ARI credentials

2. **"Connection refused to Local AI Server"**
   - Ensure Local AI Server is running on port 8765
   - Check if the service is accessible

3. **"Failed to create External Media channel"**
   - Verify Asterisk has `res_rtp_asterisk` module loaded
   - Check if External Media is supported in your Asterisk version

4. **"No active call found for audio playback"**
   - Call may have ended before audio processing completed
   - Check channel lifecycle management

### Debug Mode Benefits

- Set `DEBUG_MODE=true` for maximum logging detail
- Monitor statistics to identify bottlenecks
- Check audio data hex dumps for format issues
- Track WebSocket connection states

## File Locations

- **Test Scripts**: `scripts/test_externalmedia_call.py`, `scripts/test_externalmedia_deployment.py`
- **In-Container Paths**: `/app/scripts/test_externalmedia_call.py`, `/app/scripts/test_externalmedia_deployment.py`
- **Logs**: `docker-compose logs -f ai-engine`

## Next Steps

Once these tests pass, your ExternalMedia + RTP path is validated end-to-end (ARI, RTP server, and playback lifecycle) for the current configuration.
