# Google Cloud AI Integration Guide

## Overview

Google Cloud AI integration provides enterprise-grade speech recognition (Chirp 3), natural language understanding (Gemini), and text-to-speech (Neural2 voices) for the Asterisk AI Voice Agent.

## Quick Start

### 1. Enable Google Cloud APIs

In your Google Cloud Console, enable these APIs:

1. **Cloud Speech-to-Text API**: https://console.cloud.google.com/apis/library/speech.googleapis.com
2. **Cloud Text-to-Speech API**: https://console.cloud.google.com/apis/library/texttospeech.googleapis.com
3. **Generative Language API**: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com

### 2. Configure API Key

Add your Google API key to `.env`:

```bash
# Google Cloud AI (required for google_cloud_* pipelines)
GOOGLE_API_KEY=your_api_key_here
```

**OR** use a service account (recommended for production):

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

### 3. Configure Asterisk Dialplan

Add to your dialplan (e.g., `extensions.conf`):

```ini
[from-ai-agent]
exten => s,1,NoOp(Incoming AI Voice Agent Call)
exten => s,n,Set(AI_CONTEXT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)  ; REQUIRED for pipeline selection
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**CRITICAL**: Both `AI_CONTEXT` and `AI_PROVIDER` must be set:
- `AI_CONTEXT` - Selects the context (greeting, prompt, profile)
- `AI_PROVIDER` - Selects the pipeline (google_cloud_full, google_cloud_cost_optimized, etc.)

### 4. Restart Asterisk

```bash
asterisk -rx "dialplan reload"
```

## Available Pipelines

### google_cloud_full (Recommended)
**Best quality and features**

- **STT**: Google Chirp 3 (latest_long model, 16kHz)
- **LLM**: Gemini 2.0 Flash (fast, intelligent)
- **TTS**: Neural2-A (natural female voice)
- **Cost**: ~$0.0024/min
- **Use Cases**: Customer service, demos, quality-focused deployments

**Dialplan:**
```ini
exten => s,n,Set(AI_CONTEXT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)
```

---

### google_cloud_cost_optimized
**Budget-friendly option**

- **STT**: Google Standard model (8kHz telephony)
- **LLM**: Gemini 1.5 Flash
- **TTS**: Standard-C voice
- **Cost**: ~$0.0015/min (38% lower than full)
- **Use Cases**: High-volume, cost-sensitive deployments

**Dialplan:**
```ini
exten => s,n,Set(AI_CONTEXT=demo_google_cost)
exten => s,n,Set(AI_PROVIDER=google_cloud_cost_optimized)
```

---

### google_hybrid_openai
**Best LLM quality**

- **STT**: Google Chirp 3
- **LLM**: OpenAI GPT-4o-mini (superior reasoning)
- **TTS**: Google Neural2-A
- **Cost**: ~$0.003/min
- **Use Cases**: Complex conversations, reasoning tasks

**Dialplan:**
```ini
exten => s,n,Set(AI_CONTEXT=demo_google_hybrid)
exten => s,n,Set(AI_PROVIDER=google_hybrid_openai)
```

**Note**: Requires both `GOOGLE_API_KEY` and `OPENAI_API_KEY`.

## Cost Comparison

| Pipeline | STT | LLM | TTS | Est. Cost/min |
|----------|-----|-----|-----|---------------|
| google_cloud_full | Chirp 3 | Gemini 2.0 | Neural2 | $0.0024 |
| google_cloud_cost_optimized | Standard | Gemini 1.5 | Standard | $0.0015 |
| google_hybrid_openai | Chirp 3 | GPT-4o-mini | Neural2 | $0.003 |
| deepgram (reference) | Nova-2 | GPT-4o-mini | Aura | $0.0043 |

*Estimates based on typical 3-minute call with 60% talk time*

## Troubleshooting

### Issue: Greeting plays but no responses

**Cause**: Google Cloud APIs not enabled or API key lacks permissions.

**Solution**: 
1. Enable all three APIs in Google Cloud Console (see Quick Start)
2. Verify API key has permissions for Speech-to-Text, Text-to-Speech, and Generative Language
3. Restart ai-engine container

### Issue: Falls back to local_hybrid pipeline

**Cause**: Missing `AI_PROVIDER` channel variable in dialplan.

**Solution**: Add both variables to dialplan:
```ini
exten => s,n,Set(AI_CONTEXT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)
```

### Issue: "Pipeline not found" error

**Cause**: Typo in pipeline name or pipelines not validating on startup.

**Solution**: 
1. Check `docker logs ai_engine` for pipeline validation results
2. Verify spelling: `google_cloud_full`, `google_cloud_cost_optimized`, `google_hybrid_openai`
3. Ensure all three should show "Pipeline validation SUCCESS"

### Verify Configuration

Check engine logs:
```bash
docker logs ai_engine 2>&1 | grep -E "google|Pipeline validation|Engine started"
```

Expected output:
```
Pipeline validation SUCCESS ... pipeline=google_cloud_full
Pipeline validation SUCCESS ... pipeline=google_cloud_cost_optimized
Pipeline validation SUCCESS ... pipeline=google_hybrid_openai
Engine started and listening for calls
```

## Advanced Configuration

### Custom Voices

Edit `config/ai-agent.yaml` to change TTS voices:

```yaml
pipelines:
  google_cloud_full:
    options:
      tts:
        voice_name: "en-US-Neural2-C"  # Male voice
        # Other options: Neural2-A (female), Neural2-D (male), Neural2-F (female)
```

### Multi-Language Support

Change STT language in pipeline options:

```yaml
pipelines:
  google_cloud_full:
    options:
      stt:
        language_code: "es-ES"  # Spanish
        # Chirp 3 supports 100+ languages
```

### Adjust Response Speed

Modify speaking rate:

```yaml
pipelines:
  google_cloud_full:
    options:
      tts:
        speaking_rate: 1.1  # 10% faster (range: 0.25 to 4.0)
```

## IAM Roles Required

If using service account authentication, grant these roles:

- **Cloud Speech-to-Text User** (`roles/speech.user`)
- **Cloud Text-to-Speech User** (`roles/texttospeech.user`)
- **Generative AI User** (`roles/aiplatform.user`)

## Support

- **Issues**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues
- **Docs**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/tree/main/docs
- **Linear**: Task AAVA-75 (Google Cloud Integration)
