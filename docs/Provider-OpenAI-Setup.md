# OpenAI Realtime Provider Setup Guide

## Overview

OpenAI Realtime API provides low-latency bidirectional streaming conversational AI powered by GPT-4o-realtime. Ideal for natural voice interactions with built-in speech-to-speech capabilities and native tool execution.

**Performance**: 0.5-1.5 second response latency | Full duplex | Server-side echo cancellation

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` → "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Get OpenAI API Key

1. Sign up at [OpenAI Platform](https://platform.openai.com/)
2. Navigate to **API Keys**
3. Create a new API key
4. Copy your API key

**Note**: OpenAI Realtime API requires a paid account with Realtime API access enabled.

### 2. Configure API Key

Add your OpenAI API key to `.env`:

```bash
# OpenAI (required for openai_realtime provider and local_hybrid pipeline)
OPENAI_API_KEY=your_api_key_here
```

**Test API Key**:
```bash
curl -X GET "https://api.openai.com/v1/models" \
  -H "Authorization: Bearer ${OPENAI_API_KEY}"
```

### 3. Configure Provider

The OpenAI Realtime provider is configured in `config/ai-agent.yaml`:

```yaml
providers:
  openai_realtime:
    # API key is injected from `OPENAI_API_KEY` in `.env` (env-only; do not commit keys to YAML)
    enabled: true
    greeting: "Hi {caller_name}, I'm your AI assistant. How can I help you today?"
    
    # API Version: "ga" (recommended) or "beta" (legacy, uses OpenAI-Beta header)
    api_version: ga
    # Model Configuration
    model: gpt-4o-realtime-preview-2024-12-17  # Works with GA API; gpt-realtime may not be available to all accounts
    temperature: 0.6                          # Creativity (0.0-1.0)
    max_response_output_tokens: 4096          # Max output length
    
    # Voice Configuration
    voice: alloy                              # Example voice (see OpenAI docs for availability)
    
    # Audio Configuration
    # Inbound from Asterisk/transport (telephony defaults)
    input_encoding: ulaw
    input_sample_rate_hz: 8000
    # Format sent to OpenAI (PCM16 @ 24kHz — GA minimum)
    provider_input_encoding: linear16
    provider_input_sample_rate_hz: 24000      # GA requires >= 24000 Hz
    # Provider output (PCM16 @ 24kHz from OpenAI, engine transcodes downstream)
    output_encoding: mulaw
    output_sample_rate_hz: 24000              # GA outputs at 24kHz; engine transcodes to 8kHz mulaw
    target_encoding: mulaw
    target_sample_rate_hz: 8000
    
    # Modalities
    response_modalities: ["audio", "text"]
    
    # Turn Detection (VAD)
    turn_detection:
      type: server_vad
      threshold: 0.5
      silence_duration_ms: 1000
      prefix_padding_ms: 300
      create_response: true
```

**Key Settings**:
- `api_version`: `ga` (default, recommended) or `beta` (legacy). GA removes the `OpenAI-Beta` header and uses nested audio schema
- `model`: `gpt-4o-realtime-preview-2024-12-17` works with GA API; `gpt-realtime` may not be available to all accounts
- `provider_input_sample_rate_hz`: must be `24000` for GA (minimum enforced by API)
- `output_sample_rate_hz`: `24000` — OpenAI outputs PCM16 @ 24kHz; engine transcodes to mulaw @ 8kHz downstream
- `turn_detection.type`: use `server_vad` for turn-taking (nested under `audio.input` in GA)

### 4. Critical Turn Detection Configuration ⚠️

**REQUIRED FOR PRODUCTION**: Configure server-side VAD for proper turn detection.

In `config/ai-agent.yaml`:

```yaml
providers:
  openai_realtime:
    turn_detection:
      type: server_vad
      threshold: 0.5              # Standard sensitivity
      silence_duration_ms: 1000   # 1 second before responding
      prefix_padding_ms: 300      # Capture speech before VAD trigger
      create_response: true       # Auto-create response after speech
```

**Why This Matters**:
- OpenAI's server-side VAD handles speech detection
- `threshold: 0.5` balances sensitivity (too high blocks user speech)
- `silence_duration_ms: 1000` waits 1 second after speech stops before responding
- VAD is disabled during greeting playback and re-enabled after completion

**VAD Fallback Timer** (Added Dec 2025):
- 5-second fallback timer ensures VAD is re-enabled even if greeting detection fails
- Guarantees two-way conversation can proceed

**Known Limitation** ⚠️:
OpenAI Realtime API has an intermittent **modalities bug** where responses may be text-only:
- Some responses return without audio (transcript only)
- Farewell messages occasionally don't have audio
- This is an OpenAI API issue, not a configuration problem
- Workaround: 5-second hangup timeout ensures call ends even without farewell audio

**See**: `docs/case-studies/OpenAI-Realtime-Golden-Baseline.md` for validated configuration

### 5. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-openai]
exten => s,1,NoOp(AI Voice Agent - OpenAI Realtime)
exten => s,n,Set(AI_CONTEXT=demo_openai)
exten => s,n,Set(AI_PROVIDER=openai_realtime)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**Recommended**: Set `AI_CONTEXT` and `AI_PROVIDER` when you want an explicit per-extension override:
- `AI_CONTEXT` selects the context (greeting, prompt, profile, tools)
- `AI_PROVIDER=openai_realtime` forces this provider for the call

If you omit these, the engine will select a context/provider using the precedence rules in `docs/Configuration-Reference.md`.

### 6. Reload Asterisk

```bash
asterisk -rx "dialplan reload"
```

### 7. Create FreePBX Custom Destination

1. Navigate to **Admin → Custom Destinations**
2. Click **Add Custom Destination**
3. Set:
   - **Target**: `from-ai-agent-openai,s,1`
   - **Description**: `OpenAI Realtime AI Agent`
4. Save and Apply Config

### 8. Test Call

Route a test call to the custom destination and verify:
- ✅ Greeting plays within 1 second
- ✅ AI responds naturally to questions
- ✅ Can interrupt AI mid-sentence (barge-in)
- ✅ No echo or self-interruption
- ✅ Tools execute if configured

## Context Configuration

Define your AI's behavior in `config/ai-agent.yaml`:

```yaml
contexts:
  demo_openai:
    greeting: "Hi {caller_name}, I'm your AI assistant. How can I help you today?"
    # Use a profile that exists in `config/ai-agent.yaml` (example: wideband internal processing for OpenAI Realtime).
    profile: openai_realtime_24k
    prompt: |
      You are a helpful AI assistant for {company_name}.
      
      Your role is to assist callers professionally and efficiently.
      
      CONVERSATION STYLE:
      - Be warm, professional, and concise
      - Use natural language without robotic phrases
      - Answer questions directly and clearly
      - Confirm important actions before executing
      
      CALL ENDING PROTOCOL:
      1. When user says goodbye → ask "Is there anything else I can help with?"
      2. If user confirms done → give brief farewell + IMMEDIATELY call hangup_call tool
      3. NEVER leave silence - always explicitly end the call
      
      TOOL USAGE:
      - Use transfer tool to route calls to appropriate departments
      - Use email tools when caller requests information sent to them
      - Always confirm before executing tools that affect the call
```

**Template Variables**:
- `{caller_name}` - Caller ID name
- `{caller_number}` - Caller phone number
- `{company_name}` - Your company name (set in config)

## Tool Configuration

Enable tools for OpenAI Realtime in `config/ai-agent.yaml`:

```yaml
providers:
  openai_realtime:
    tools:
      - transfer              # Transfer calls to extensions/queues
      - cancel_transfer       # Cancel an active transfer
      - hangup_call           # End call with farewell
      - leave_voicemail       # Send caller to voicemail
      - send_email_summary    # Auto-send call summary
      - request_transcript    # Email transcript on request
```

**Tool Execution**: OpenAI Realtime natively supports function calling. Tools are executed automatically when the AI decides to use them based on conversation context.

## Troubleshooting

### Issue: "Echo / Self-Interruption"

**Cause**: VAD aggressiveness set too low

**Fix**:
```yaml
vad:
  webrtc_aggressiveness: 1  # MUST be 1, not 0
```

**Verification**: Check logs for gate closures - should be 1-2 per call, not 50+

### Issue: "Tools Not Working"

**Cause**: Schema format mismatch (post-AAVA-85 regression)

**Fix**: Verify you're on latest version. Tool registry now uses `to_openai_realtime_schema()` (flat format), not `to_openai_schema()` (nested format).

**Logs to Check**:
- ✅ "OpenAI session configured with N tools"
- ❌ "Missing required parameter: 'session.tools[0].name'"

**See**: `docs/contributing/COMMON_PITFALLS.md#tool-execution-issues`

### Issue: "No Audio" or "Silence"

**Cause**: Modalities not set correctly

**Fix**:
```yaml
providers:
  openai_realtime:
    response_modalities: ["audio", "text"]
```

### Issue: "High Latency" (>2 seconds)

**Cause**: Network latency or model selection

**Fix**:
1. Check network: `ping api.openai.com`
2. Verify using latest realtime model
3. Check OpenAI status: https://status.openai.com/

### Issue: "AI Doesn't Respond"

**Cause**: VAD not detecting speech or turn detection issues

**Fix**:
```yaml
providers:
  openai_realtime:
    turn_detection:
      type: server_vad
      threshold: 0.5
      silence_duration_ms: 1000
```

## Production Considerations

### API Key Management
- Rotate keys every 90 days
- Use separate keys for dev/staging/production
- Monitor usage in OpenAI Dashboard
- Set spending limits to prevent overages

### Cost Optimization
- OpenAI Realtime charges per audio minute + token usage
- Monitor concurrent calls to manage costs
- Consider usage limits for high-volume scenarios
- Audio: ~$0.06/minute input, ~$0.24/minute output
- Tokens: Additional LLM costs for text processing

### Monitoring
- Track response latency in logs
- Monitor OpenAI API status: https://status.openai.com/
- Set up alerts for API errors or high latency
- Watch for rate limiting (500 requests/day default)

### Rate Limits
- Realtime API has lower rate limits than standard API
- Default: 500 requests/day, 100 concurrent sessions
- Request increase through OpenAI if needed
- Implement queuing for high-volume deployments

## See Also

- **Implementation Details**: `docs/contributing/references/Provider-OpenAI-Implementation.md`
- **Golden Baseline**: `docs/case-studies/OpenAI-Realtime-Golden-Baseline.md`
- **Common Pitfalls**: `docs/contributing/COMMON_PITFALLS.md`
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`
- **VAD Configuration**: Critical setting documented in golden baseline

---

**OpenAI Realtime Provider Setup - Complete** ✅

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/Asterisk-AI-Voice-Agent).
