# ExternalMedia + RTP Deployment Guide

## Overview

This guide covers the deployment and testing of the ExternalMedia + RTP implementation for the Asterisk AI Voice Agent. This is the primary audio transport method for reliable audio capture.

## Architecture

- **Upstream**: Caller audio → ARI ExternalMedia → RTP → Engine → Provider
- **Downstream**: Provider TTS → File-based playback → ARI → Caller
- **Transport**: UDP RTP on the configured port or dynamic range (default `18080:18099`)
- **Codec**: µ-law (8kHz) with automatic resampling to PCM16k (16kHz)

## Configuration

### YAML Configuration

The configuration is automatically set in `config/ai-agent.yaml`:

```yaml
audio_transport: "externalmedia"

external_media:
  rtp_host: "0.0.0.0"        # bind inside container
  rtp_port: 18080            # fixed port for simplicity
  port_range: "18080:18099"  # optional range for per-call allocation
  codec: "ulaw"              # ulaw (8k) or slin16 (8k)
  direction: "both"          # sendrecv | sendonly | recvonly
  jitter_buffer_ms: 20       # target frame size
```

### Environment Variables

Ensure these are set in your `.env` file:

```bash
ASTERISK_HOST=127.0.0.1
ASTERISK_ARI_USERNAME=your_ari_username
ASTERISK_ARI_PASSWORD=your_ari_password
```

## Deployment Steps

### 1. Pre-Deployment Validation

Run the validation script to ensure everything is ready:

```bash
python3 scripts/validate_externalmedia_config.py
```

Expected output:

```
🎉 All validations passed! Ready for deployment.
```

### 2. Deploy to Server

Use the Makefile for deployment:

```bash
# For code changes (recommended)
make deploy

# For dependency changes or cache issues
make deploy-force
```

### 3. Verify Deployment

Check that the engine starts successfully:

```bash
make server-logs
```

Expected logs:

```
✅ Successfully connected to ARI HTTP endpoint
✅ Successfully connected to ARI WebSocket
✅ RTP server ready for ExternalMedia transport (port range 18080-18099)
✅ Engine started and listening for calls
```

## Testing

### Test Call Checklist

When placing a test call, monitor these logs:

#### 1. Call Initiation

- ✅ "StasisStart event received"
- ✅ "Caller channel entered Stasis"
- ✅ "Caller channel answered"

#### 2. Bridge Creation

- ✅ "Bridge created"
- ✅ "Caller added to bridge"

#### 3. ExternalMedia Channel

- ✅ "Creating ExternalMedia channel"
- ✅ "ExternalMedia channel created successfully"
- ✅ "ExternalMedia channel added to bridge"

#### 4. Provider Session

- ✅ "Provider session started for ExternalMedia"
- ✅ "Greeting playback started for ExternalMedia"

#### 5. RTP Audio Capture

- ✅ "RTP audio received" (when you speak)
- ✅ "RTP audio sent to provider"

#### 6. Provider Response

- ✅ "Audio playback initiated successfully"

### Troubleshooting

#### No RTP Received

- **Check**: Asterisk can reach the configured RTP endpoint (default `127.0.0.1:18080`)
- **Verify**: Host networking is enabled in docker-compose.yml
- **Confirm**: RTP server is listening on the correct port

#### Garbled Audio

- **Check**: Codec consistency (`ulaw` vs `slin16`)
- **Verify**: Only one resample step (8k→16k)
- **Confirm**: Audio format matches between Asterisk and RTP server

#### ExternalMedia Channel Creation Fails

- **Check**: ARI credentials are correct
- **Verify**: Asterisk has ExternalMedia support
- **Confirm**: Network connectivity between containers

#### No Voice Capture After Greeting

- **Check**: `audio_capture_enabled` flag is set after greeting
- **Verify**: RTP packets are being received
- **Confirm**: Provider is processing audio correctly

## Monitoring

### Health Endpoint

The engine exposes a health endpoint at `http://localhost:15000/health`:

```json
{
  "status": "healthy",
  "ari_connected": true,
  "external_media_listening": true,
  "rtp_server_running": true,
  "active_calls": 0,
  "providers": {
    "local": "ready"
  }
}
```

### RTP Server Stats

Access RTP server statistics via the engine logs or health endpoint:

```json
{
  "running": true,
  "host": "0.0.0.0",
  "port_range": [
    18080,
    18099
  ],
  "codec": "ulaw",
  "total_sessions": 1,
  "active_sessions": 1,
  "total_frames_received": 150,
  "total_frames_processed": 150,
  "total_packet_loss": 0,
  "ssrc_mappings": 1
}
```

## Fallback Strategy

If ExternalMedia fails, check the following:

1. RTP server is listening on the configured port
2. ExternalMedia channels are being created successfully
3. Bridge operations are working correctly

ExternalMedia is the primary and only supported transport method.

## Performance Expectations

- **Latency**: P95 < 2 seconds end-to-end
- **Audio Quality**: Clear voice capture and playback
- **Reliability**: Robust error handling and recovery
- **Scalability**: Support for multiple concurrent calls

## Next Steps

### Phase 6: Operations (Future)

- Health monitoring dashboard
- Metrics collection and alerting
- Automated testing and validation
- Performance optimization

### Future Enhancements

- Downstream RTP streaming (optional)
- Barge-in support during playback
- Advanced jitter buffering
- Telemetry and analytics

## Support

For issues or questions:

1. Check the logs using `make server-logs`
2. Run validation script: `python3 scripts/validate_externalmedia_config.py`
3. Verify configuration matches this guide
4. Check Asterisk logs for ExternalMedia errors

## Success Criteria

A successful deployment should show:

1. ✅ Engine starts without errors
2. ✅ RTP server ready on the configured port range (default `18080:18099`)
3. ✅ ExternalMedia channels created successfully
4. ✅ Voice capture works after greeting
5. ✅ Provider responses are heard clearly
6. ✅ No audio quality issues
7. ✅ Clean call termination

If all criteria are met, the ExternalMedia + RTP implementation is working correctly!
