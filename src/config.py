"""
Shared Configuration System for Asterisk AI Voice Agent v2.0

This module provides centralized configuration management for all microservices
using Pydantic v2 for validation and type safety.
"""

import os
import yaml
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List

# Determine the absolute path to the project root from this file's location
# This makes the config loading independent of the current working directory.
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AsteriskConfig(BaseModel):
    host: str
    port: int = Field(default=8088)
    username: str
    password: str
    app_name: str = Field(default="ai-voice-agent")

class ExternalMediaConfig(BaseModel):
    rtp_host: str = Field(default="0.0.0.0")
    rtp_port: int = Field(default=18080)
    codec: str = Field(default="ulaw")  # ulaw or slin16
    direction: str = Field(default="both")  # both, sendonly, recvonly
    jitter_buffer_ms: int = Field(default=20)


class AudioSocketConfig(BaseModel):
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8090)
    format: str = Field(default="ulaw")  # 'ulaw' or 'slin16'


class LocalProviderConfig(BaseModel):
    enabled: bool = Field(default=True)
    ws_url: Optional[str] = Field(default="ws://127.0.0.1:8765")
    connect_timeout_sec: float = Field(default=5.0)
    response_timeout_sec: float = Field(default=5.0)
    chunk_ms: int = Field(default=200)
    stt_model: Optional[str] = None
    llm_model: Optional[str] = None
    tts_voice: Optional[str] = None
    temperature: float = Field(default=0.8)
    max_tokens: int = Field(default=150)


class DeepgramProviderConfig(BaseModel):
    api_key: Optional[str] = None
    model: str = Field(default="nova-2-general")
    tts_model: str = Field(default="aura-asteria-en")
    greeting: Optional[str] = None
    instructions: Optional[str] = None
    input_encoding: str = Field(default="linear16")
    input_sample_rate_hz: int = Field(default=16000)
    continuous_input: bool = Field(default=True)
    base_url: str = Field(default="https://api.deepgram.com")
    tts_voice: Optional[str] = None
    stt_language: str = Field(default="en-US")


class OpenAIProviderConfig(BaseModel):
    """# Milestone7: Canonical defaults for OpenAI pipeline adapters."""
    api_key: Optional[str] = None
    organization: Optional[str] = None
    project: Optional[str] = None
    realtime_base_url: str = Field(default="wss://api.openai.com/v1/realtime")
    chat_base_url: str = Field(default="https://api.openai.com/v1")
    tts_base_url: str = Field(default="https://api.openai.com/v1/audio/speech")
    realtime_model: str = Field(default="gpt-4o-realtime-preview-2024-12-17")
    chat_model: str = Field(default="gpt-4o-mini")
    tts_model: str = Field(default="gpt-4o-mini-tts")
    voice: str = Field(default="alloy")
    default_modalities: List[str] = Field(default_factory=lambda: ["text"])
    input_encoding: str = Field(default="linear16")
    input_sample_rate_hz: int = Field(default=16000)
    target_encoding: str = Field(default="mulaw")
    target_sample_rate_hz: int = Field(default=8000)
    chunk_size_ms: int = Field(default=20)
    response_timeout_sec: float = Field(default=5.0)


class GoogleProviderConfig(BaseModel):
    api_key: Optional[str] = None
    project_id: Optional[str] = None
    stt_base_url: str = Field(default="https://speech.googleapis.com/v1")
    tts_base_url: str = Field(default="https://texttospeech.googleapis.com/v1")
    llm_base_url: str = Field(default="https://generativelanguage.googleapis.com/v1")
    stt_language_code: str = Field(default="en-US")
    tts_voice_name: str = Field(default="en-US-Neural2-C")
    tts_audio_encoding: str = Field(default="MULAW")
    tts_sample_rate_hz: int = Field(default=8000)
    llm_model: str = Field(default="models/gemini-1.5-pro-latest")


class OpenAIRealtimeProviderConfig(BaseModel):
    enabled: bool = Field(default=True)
    api_key: Optional[str] = None
    model: str = Field(default="gpt-4o-realtime-preview-2024-12-17")
    voice: str = Field(default="alloy")
    base_url: str = Field(default="wss://api.openai.com/v1/realtime")
    instructions: Optional[str] = None
    organization: Optional[str] = None
    input_encoding: str = Field(default="slin16")  # AudioSocket inbound default (8 kHz PCM16)
    input_sample_rate_hz: int = Field(default=8000)  # AudioSocket source sample rate
    provider_input_encoding: str = Field(default="linear16")  # Provider expects PCM16 LE
    provider_input_sample_rate_hz: int = Field(default=16000)  # OpenAI Realtime input sample rate
    output_encoding: str = Field(default="linear16")  # Provider emits PCM16 frames
    output_sample_rate_hz: int = Field(default=24000)
    target_encoding: str = Field(default="ulaw")  # Downstream AudioSocket expectations
    target_sample_rate_hz: int = Field(default=8000)
    response_modalities: List[str] = Field(default_factory=lambda: ["text", "audio"])
    # Optional explicit greeting to speak immediately on connect
    greeting: Optional[str] = None
    # Optional server-side turn detection configuration
    # If provided, will be sent in session.update
    class TurnDetectionConfig(BaseModel):
        type: str = Field(default="server_vad")
        silence_duration_ms: int = Field(default=200)
        threshold: float = Field(default=0.5)
        prefix_padding_ms: int = Field(default=200)

    turn_detection: Optional[TurnDetectionConfig] = None

class BargeInConfig(BaseModel):
    enabled: bool = Field(default=True)
    initial_protection_ms: int = Field(default=200)
    min_ms: int = Field(default=250)
    energy_threshold: int = Field(default=1000)
    cooldown_ms: int = Field(default=500)
    # New: short guard window after TTS ends to avoid self-echo re-capture
    post_tts_end_protection_ms: int = Field(default=250)
    # Extra protection during the first greeting turn
    greeting_protection_ms: int = Field(default=0)


class LLMConfig(BaseModel):
    initial_greeting: str = "Hello, I am an AI Assistant for Jugaar LLC. How can I help you today."
    prompt: str = "You are a helpful AI assistant."
    model: str = "gpt-4o"
    api_key: Optional[str] = None


class VADConfig(BaseModel):
    enhanced_enabled: bool = Field(default=False)
    # WebRTC VAD settings - optimized for real-time conversation
    webrtc_aggressiveness: int = 1
    webrtc_start_frames: int = 2
    webrtc_end_silence_frames: int = 15
    # Enhanced VAD thresholds
    energy_threshold: int = 1500
    confidence_threshold: float = 0.6
    adaptive_threshold_enabled: bool = True
    noise_adaptation_rate: float = 0.1
    
    # Utterance settings - optimized for real-time conversation
    min_utterance_duration_ms: int = 800
    max_utterance_duration_ms: int = 8000
    utterance_padding_ms: int = 100
    
    # Fallback settings
    fallback_enabled: bool = True
    fallback_interval_ms: int = 1500
    fallback_buffer_size: int = 128000


class StreamingConfig(BaseModel):
    sample_rate: int = Field(default=8000)
    jitter_buffer_ms: int = Field(default=50)
    keepalive_interval_ms: int = Field(default=5000)
    connection_timeout_ms: int = Field(default=10000)
    fallback_timeout_ms: int = Field(default=4000)
    chunk_size_ms: int = Field(default=20)
    min_start_ms: int = Field(default=120)
    low_watermark_ms: int = Field(default=80)
    provider_grace_ms: int = Field(default=500)
    logging_level: str = Field(default="info")
    # Smaller warm-up only for the initial greeting to get first audio out sooner
    greeting_min_start_ms: int = Field(default=0)


class LoggingConfig(BaseModel):
    """Top-level logging configuration for the ai-engine service."""
    level: str = Field(default="info")  # debug|info|warning|error|critical


class PipelineEntry(BaseModel):
    stt: str
    llm: str
    tts: str
    options: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


# Milestone7: Compose canonical component names for provider-backed pipelines.
def _compose_provider_components(provider: str) -> Dict[str, Any]:
    return {
        "stt": f"{provider}_stt",
        "llm": f"{provider}_llm",
        "tts": f"{provider}_tts",
        "options": {}
    }


# Milestone7: Normalize pipeline definitions into the PipelineEntry schema.
def _normalize_pipelines(config_data: Dict[str, Any]) -> None:
    default_provider = config_data.get("default_provider", "openai_realtime")
    pipelines_cfg = config_data.get("pipelines")

    if not pipelines_cfg:
        _generate_default_pipeline(config_data)
        return

    normalized: Dict[str, Dict[str, Any]] = {}

    for pipeline_name, raw_entry in pipelines_cfg.items():
        if raw_entry is None:
            normalized[pipeline_name] = _compose_provider_components(default_provider)
            continue

        if isinstance(raw_entry, str):
            normalized[pipeline_name] = _compose_provider_components(raw_entry)
            continue

        if isinstance(raw_entry, dict):
            provider_hint = raw_entry.get("provider")
            provider_for_defaults = provider_hint or default_provider
            components = _compose_provider_components(provider_for_defaults)

            options_block = raw_entry.get("options") or {}
            if not isinstance(options_block, dict):
                raise TypeError(
                    f"Unsupported pipeline options type for '{pipeline_name}': {type(options_block).__name__}"
                )

            normalized_entry = {
                "stt": raw_entry.get("stt", components["stt"]),
                "llm": raw_entry.get("llm", components["llm"]),
                "tts": raw_entry.get("tts", components["tts"]),
                "options": options_block,
            }

            normalized[pipeline_name] = normalized_entry
            continue

        raise TypeError(f"Unsupported pipeline definition for '{pipeline_name}': {type(raw_entry).__name__}")

    config_data["pipelines"] = normalized
    config_data.setdefault("active_pipeline", next(iter(normalized.keys())))


class AppConfig(BaseModel):
    default_provider: str
    providers: Dict[str, Any]
    asterisk: AsteriskConfig
    llm: LLMConfig
    audio_transport: str = Field(default="externalmedia")  # 'externalmedia' | 'legacy'
    downstream_mode: str = Field(default="file")  # 'file' | 'stream'
    external_media: Optional[ExternalMediaConfig] = Field(default_factory=ExternalMediaConfig)
    audiosocket: Optional[AudioSocketConfig] = Field(default_factory=AudioSocketConfig)
    vad: Optional[VADConfig] = Field(default_factory=VADConfig)
    streaming: Optional[StreamingConfig] = Field(default_factory=StreamingConfig)
    barge_in: Optional[BargeInConfig] = Field(default_factory=BargeInConfig)
    logging: Optional[LoggingConfig] = Field(default_factory=LoggingConfig)
    pipelines: Dict[str, PipelineEntry] = Field(default_factory=dict)
    active_pipeline: Optional[str] = None

    # Ensure tests that construct AppConfig(**dict) directly still get normalized pipelines
    # similar to load_config(), which calls _normalize_pipelines().
    from pydantic import model_validator  # local import to keep top clear

    @model_validator(mode="before")
    @classmethod
    def _normalize_before(cls, data: Any):  # type: ignore[override]
        try:
            if isinstance(data, dict):
                _normalize_pipelines(data)
        except Exception:
            # Non-fatal: if normalization fails, Pydantic will raise a more specific error later
            pass
        return data

def _generate_default_pipeline(config_data: Dict[str, Any]) -> None:
    """Populate a default pipeline entry when none are provided."""
    default_provider = config_data.get("default_provider", "openai_realtime")
    pipeline_name = "default"
    # Milestone7: Align implicit defaults with the PipelineEntry schema.
    default_components = _compose_provider_components(default_provider)

    pipelines = config_data.setdefault("pipelines", {})
    existing_entry = pipelines.get(pipeline_name)

    if existing_entry is None:
        pipelines[pipeline_name] = _compose_provider_components(default_provider)
    elif isinstance(existing_entry, str):
        pipelines[pipeline_name] = _compose_provider_components(existing_entry)
    elif isinstance(existing_entry, dict):
        existing_entry.setdefault("stt", default_components["stt"])
        existing_entry.setdefault("llm", default_components["llm"])
        existing_entry.setdefault("tts", default_components["tts"])
        if not isinstance(existing_entry.get("options"), dict):
            existing_entry["options"] = {}
    else:
        pipelines[pipeline_name] = _compose_provider_components(default_provider)

    config_data.setdefault("active_pipeline", pipeline_name)


def load_config(path: str = "config/ai-agent.yaml") -> AppConfig:
    # If the provided path is not absolute, resolve it relative to the project root.
    if not os.path.isabs(path):
        path = os.path.join(_PROJ_DIR, path)

    try:
        with open(path, 'r') as f:
            config_str = f.read()

        # Substitute environment variables
        config_str_expanded = os.path.expandvars(config_str)
        
        config_data = yaml.safe_load(config_str_expanded)

        # Manually construct and inject the Asterisk config from environment variables.
        # This keeps secrets out of the YAML file and aligns with the Pydantic model.
        config_data['asterisk'] = {
            "host": os.getenv("ASTERISK_HOST"),
            "username": os.getenv("ASTERISK_ARI_USERNAME"),
            "password": os.getenv("ASTERISK_ARI_PASSWORD"),
            "app_name": "asterisk-ai-voice-agent"
        }

        # Merge YAML LLM section with environment variables without clobbering YAML values.
        # Precedence: YAML llm.* (if non-empty) > env vars > hardcoded defaults.
        llm_yaml = (config_data.get('llm') or {}) if isinstance(config_data.get('llm'), dict) else {}

        def _nonempty_string(val: Any) -> bool:
            return isinstance(val, str) and val.strip() != ""

        # Resolve initial_greeting
        initial_greeting = llm_yaml.get('initial_greeting')
        if not _nonempty_string(initial_greeting):
            initial_greeting = os.getenv("GREETING", "Hello, how can I help you?")
        # Resolve prompt/persona
        prompt_val = llm_yaml.get('prompt')
        if not _nonempty_string(prompt_val):
            prompt_val = os.getenv("AI_ROLE", "You are a helpful assistant.")
        # Resolve model and api_key
        model_val = llm_yaml.get('model') or "gpt-4o"
        api_key_val = llm_yaml.get('api_key') or os.getenv("OPENAI_API_KEY")

        # Apply environment variable interpolation to final strings to support ${VAR} placeholders
        try:
            initial_greeting = os.path.expandvars(initial_greeting or "")
        except Exception:
            pass
        try:
            prompt_val = os.path.expandvars(prompt_val or "")
        except Exception:
            pass

        config_data['llm'] = {
            "initial_greeting": initial_greeting,
            "prompt": prompt_val,
            "model": model_val,
            "api_key": api_key_val,
        }

        # Defaults for new flags if not present in YAML
        config_data.setdefault('audio_transport', os.getenv('AUDIO_TRANSPORT', 'externalmedia'))
        config_data.setdefault('downstream_mode', os.getenv('DOWNSTREAM_MODE', 'file'))
        if 'streaming' not in config_data:
            config_data['streaming'] = {}

        # AudioSocket configuration defaults
        audiosocket_cfg = config_data.get('audiosocket', {}) or {}
        audiosocket_cfg.setdefault('host', os.getenv('AUDIOSOCKET_HOST', '0.0.0.0'))
        try:
            audiosocket_cfg.setdefault('port', int(os.getenv('AUDIOSOCKET_PORT', audiosocket_cfg.get('port', 8090))))
        except ValueError:
            audiosocket_cfg['port'] = 8090
        # AudioSocket payload format expected by Asterisk dialplan (matches third arg to AudioSocket(...))
        audiosocket_cfg.setdefault('format', os.getenv('AUDIOSOCKET_FORMAT', audiosocket_cfg.get('format', 'ulaw')))
        config_data['audiosocket'] = audiosocket_cfg

        # Milestone7: Normalize pipelines for PipelineEntry schema while keeping legacy configs valid.
        _normalize_pipelines(config_data)

        # Sanitize providers.local for Bash-style ${VAR:-default}/${VAR:=default} tokens.
        # os.path.expandvars does not support these defaults and may leave tokens intact or empty.
        # Extract and apply the default values so Pydantic receives valid scalars.
        try:
            providers_block = config_data.get('providers', {}) or {}
            local_block = providers_block.get('local', {}) or {}

            def _apply_default_token(val, *, default=None):
                # If val is a token like "${NAME:-fallback}" or "${NAME:=fallback}", try to extract fallback.
                if isinstance(val, str) and val.strip().startswith('${') and val.strip().endswith('}'):
                    inner = val.strip()[2:-1]
                    # Split on first ':' to isolate var name vs default part
                    parts = inner.split(':', 1)
                    if len(parts) == 2:
                        default_part = parts[1]
                        # Strip any leading '-', '=' used in Bash syntax
                        default_part = default_part.lstrip('-=')
                        return default_part
                    return default
                # If val is empty string after env expansion, use provided default
                if val == '' and default is not None:
                    return default
                return val

            # Apply defaults for known local provider keys
            if isinstance(local_block, dict):
                local_block['ws_url'] = _apply_default_token(local_block.get('ws_url'), default='ws://127.0.0.1:8765')
                local_block['connect_timeout_sec'] = _apply_default_token(local_block.get('connect_timeout_sec'), default='5.0')
                local_block['response_timeout_sec'] = _apply_default_token(local_block.get('response_timeout_sec'), default='5.0')
                local_block['chunk_ms'] = _apply_default_token(local_block.get('chunk_ms'), default='200')

                # Coerce numeric strings to proper types
                try:
                    if isinstance(local_block.get('connect_timeout_sec'), str):
                        local_block['connect_timeout_sec'] = float(local_block['connect_timeout_sec'])
                except Exception:
                    local_block['connect_timeout_sec'] = 5.0
                try:
                    if isinstance(local_block.get('response_timeout_sec'), str):
                        local_block['response_timeout_sec'] = float(local_block['response_timeout_sec'])
                except Exception:
                    local_block['response_timeout_sec'] = 5.0
                try:
                    if isinstance(local_block.get('chunk_ms'), str):
                        local_block['chunk_ms'] = int(float(local_block['chunk_ms']))
                except Exception:
                    local_block['chunk_ms'] = 200

                providers_block['local'] = local_block
                config_data['providers'] = providers_block
        except Exception:
            # Non-fatal; Pydantic may still coerce correctly
            pass

        # Barge-in configuration defaults + env overrides
        barge_cfg = config_data.get('barge_in', {}) or {}
        try:
            if 'BARGE_IN_ENABLED' in os.environ:
                barge_cfg['enabled'] = os.getenv('BARGE_IN_ENABLED', 'true').lower() in ('1','true','yes')
            if 'BARGE_IN_INITIAL_PROTECTION_MS' in os.environ:
                barge_cfg['initial_protection_ms'] = int(os.getenv('BARGE_IN_INITIAL_PROTECTION_MS', '200'))
            if 'BARGE_IN_MIN_MS' in os.environ:
                barge_cfg['min_ms'] = int(os.getenv('BARGE_IN_MIN_MS', '250'))
            if 'BARGE_IN_ENERGY_THRESHOLD' in os.environ:
                barge_cfg['energy_threshold'] = int(os.getenv('BARGE_IN_ENERGY_THRESHOLD', '1000'))
            if 'BARGE_IN_COOLDOWN_MS' in os.environ:
                barge_cfg['cooldown_ms'] = int(os.getenv('BARGE_IN_COOLDOWN_MS', '500'))
            if 'BARGE_IN_POST_TTS_END_PROTECTION_MS' in os.environ:
                barge_cfg['post_tts_end_protection_ms'] = int(os.getenv('BARGE_IN_POST_TTS_END_PROTECTION_MS', '250'))
        except ValueError:
            pass
        config_data['barge_in'] = barge_cfg

        return AppConfig(**config_data)
    except FileNotFoundError:
        # Re-raise with a more informative error message
        raise FileNotFoundError(f"Configuration file not found at the resolved path: {path}")
    except yaml.YAMLError as e:
        # Re-raise with a more informative error message
        raise yaml.YAMLError(f"Error parsing YAML file at {path}: {e}")
