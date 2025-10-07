import asyncio
import logging
import math
import os
import random
import signal
import struct
import time
import uuid
import audioop
import base64
from collections import deque
from typing import Dict, Any, Optional, List

# Simple audio capture system removed - not used in production

# WebRTC VAD for robust speech detection
try:
    import webrtcvad  # pyright: ignore[reportMissingImports]
    WEBRTC_VAD_AVAILABLE = True
except ImportError:
    WEBRTC_VAD_AVAILABLE = False
    webrtcvad = None

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest, Histogram, Counter, Gauge

from .ari_client import ARIClient
from aiohttp import web
from pydantic import ValidationError

from .config import (
    AppConfig,
    load_config,
    LocalProviderConfig,
    DeepgramProviderConfig,
    OpenAIRealtimeProviderConfig,
)
from .pipelines import PipelineOrchestrator, PipelineOrchestratorError, PipelineResolution
from .logging_config import get_logger, configure_logging
from .rtp_server import RTPServer
from .audio.audiosocket_server import AudioSocketServer
from .providers.base import AIProviderInterface
from .providers.deepgram import DeepgramProvider
from .providers.local import LocalProvider
from .providers.openai_realtime import OpenAIRealtimeProvider
from .core import SessionStore, PlaybackManager, ConversationCoordinator
from .core.vad_manager import EnhancedVADManager, VADResult
from .core.streaming_playback_manager import StreamingPlaybackManager
from .core.models import CallSession

logger = get_logger(__name__)

# -----------------------------------------------------------------------------
# Prometheus latency histograms (module scope, registered once)
# -----------------------------------------------------------------------------
_TURN_STT_TO_TTS = Histogram(
    "ai_agent_stt_to_tts_seconds",
    "Time from STT final transcript to first TTS bytes",
    buckets=(0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0),
    labelnames=("pipeline", "provider"),
)
_TURN_RESPONSE_SECONDS = Histogram(
    "ai_agent_turn_response_seconds",
    "Approx time from STT final transcript to ARI playback start",
    buckets=(0.2, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0),
    labelnames=("pipeline", "provider"),
)

# Config exposure gauges (per call at session start)
_CFG_BARGE_MS = Gauge(
    "ai_agent_config_barge_in_ms",
    "Configured barge-in timing values (ms)",
    labelnames=("call_id", "param"),
)
_CFG_BARGE_THRESHOLD = Gauge(
    "ai_agent_config_barge_in_threshold",
    "Configured barge-in energy threshold",
    labelnames=("call_id",),
)
_CFG_STREAM_MS = Gauge(
    "ai_agent_config_streaming_ms",
    "Configured streaming timing values (ms)",
    labelnames=("call_id", "param"),
)
_CFG_TD_MS = Gauge(
    "ai_agent_config_turn_detection_ms",
    "Configured provider turn detection timing values (ms)",
    labelnames=("call_id", "param"),
)
_CFG_TD_THRESHOLD = Gauge(
    "ai_agent_config_turn_detection_threshold",
    "Configured provider turn detection threshold",
    labelnames=("call_id",),
)

# Barge-in reaction latency (seconds) from first energy to trigger
_BARGE_REACTION_SECONDS = Histogram(
    "ai_agent_barge_in_reaction_seconds",
    "Time from first speech energy to barge-in trigger",
    buckets=(0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0),
    labelnames=("call_id",),
)

# Per-call audio byte counters (ingress)
_STREAM_RX_BYTES = Counter(
    "ai_agent_stream_rx_bytes_total",
    "Inbound audio bytes from caller (per call)",
    labelnames=("call_id",),
)

class AudioFrameProcessor:
    """Processes audio in 40ms frames to prevent voice queue backlog."""
    
    def __init__(self, frame_size: int = 320):  # 40ms at 8kHz = 320 samples
        self.frame_size = frame_size
        self.buffer = bytearray()
        self.frame_bytes = frame_size * 2  # 2 bytes per sample (PCM16)
    
    def process_audio(self, audio_data: bytes) -> List[bytes]:
        """Process audio data and return complete frames."""
        # Accumulate audio in buffer
        self.buffer.extend(audio_data)
        
        # Extract complete frames
        frames = []
        while len(self.buffer) >= self.frame_bytes:
            frame = bytes(self.buffer[:self.frame_bytes])
            self.buffer = self.buffer[self.frame_bytes:]
            frames.append(frame)
        
        # Debug frame processing
        if len(frames) > 0:
            logger.debug("🎤 AVR FrameProcessor - Frames Generated",
                        input_bytes=len(audio_data),
                        buffer_before=len(self.buffer) + (len(frames) * self.frame_bytes),
                        buffer_after=len(self.buffer),
                        frames_generated=len(frames),
                        frame_size=self.frame_bytes)
        
        return frames

    
    
    
    def flush(self) -> bytes:
        """Flush remaining audio in buffer."""
        remaining = bytes(self.buffer)
        self.buffer = bytearray()
        return remaining

class VoiceActivityDetector:
    """Simple VAD to reduce unnecessary audio processing."""
    
    def __init__(self, speech_threshold: float = 0.3, silence_frames: int = 10):
        self.speech_threshold = speech_threshold
        self.max_silence_frames = silence_frames
        self.silence_frames = 0
        self.is_speaking = False
    
    def is_speech(self, audio_energy: float) -> bool:
        """Determine if audio contains speech."""
        if audio_energy > self.speech_threshold:
            self.silence_frames = 0
            if not self.is_speaking:
                self.is_speaking = True
                logger.debug("🎤 AVR VAD - Speech Started", 
                           energy=f"{audio_energy:.4f}", 
                           threshold=f"{self.speech_threshold:.4f}")
            return True
        else:
            self.silence_frames += 1
            if self.is_speaking and self.silence_frames >= self.max_silence_frames:
                self.is_speaking = False
                logger.debug("🎤 AVR VAD - Speech Ended", 
                           silence_frames=self.silence_frames,
                           max_silence=self.max_silence_frames)
            return self.silence_frames < self.max_silence_frames


class Engine:
    """The main application engine."""

    def __init__(self, config: AppConfig):
        self.config = config
        base_url = f"http://{config.asterisk.host}:{config.asterisk.port}/ari"
        self.ari_client = ARIClient(
            username=config.asterisk.username,
            password=config.asterisk.password,
            base_url=base_url,
            app_name=config.asterisk.app_name
        )
        # Set engine reference for event propagation
        self.ari_client.engine = self
        
        # Initialize core components
        self.session_store = SessionStore()
        self.conversation_coordinator = ConversationCoordinator(self.session_store)
        self.playback_manager = PlaybackManager(
            self.session_store,
            self.ari_client,
            conversation_coordinator=self.conversation_coordinator,
        )
        self.conversation_coordinator.set_playback_manager(self.playback_manager)
        # Per-call transcript timing cache for latency histograms
        self._last_transcript_ts: Dict[str, float] = {}
        
        # Initialize streaming playback manager
        streaming_config = {}
        if hasattr(config, 'streaming') and config.streaming:
            streaming_config = {
                'sample_rate': config.streaming.sample_rate,
                'jitter_buffer_ms': config.streaming.jitter_buffer_ms,
                'keepalive_interval_ms': config.streaming.keepalive_interval_ms,
                'connection_timeout_ms': config.streaming.connection_timeout_ms,
                'fallback_timeout_ms': config.streaming.fallback_timeout_ms,
                'chunk_size_ms': config.streaming.chunk_size_ms,
                # Additional tuning knobs
                'min_start_ms': config.streaming.min_start_ms,
                'low_watermark_ms': config.streaming.low_watermark_ms,
                'provider_grace_ms': config.streaming.provider_grace_ms,
                'logging_level': config.streaming.logging_level,
            }
        # Debug/diagnostics: allow broadcasting outbound frames to all AudioSocket conns
        try:
            streaming_config['audiosocket_broadcast_debug'] = bool(int(os.getenv('AUDIOSOCKET_BROADCAST_DEBUG', '0')))
        except Exception:
            streaming_config['audiosocket_broadcast_debug'] = False
        
        self.streaming_playback_manager = StreamingPlaybackManager(
            self.session_store,
            self.ari_client,
            conversation_coordinator=self.conversation_coordinator,
            fallback_playback_manager=self.playback_manager,
            streaming_config=streaming_config,
            audio_transport=self.config.audio_transport,
        )
        
        # Milestone7: Pipeline orchestrator coordinates per-call STT/LLM/TTS adapters.
        self.pipeline_orchestrator = PipelineOrchestrator(config)
        
        self.providers: Dict[str, AIProviderInterface] = {}
        # Per-call provider streaming queues (AgentAudio -> streaming playback)
        self._provider_stream_queues: Dict[str, asyncio.Queue] = {}
        self.conn_to_channel: Dict[str, str] = {}
        self.channel_to_conn: Dict[str, str] = {}
        self.conn_to_caller: Dict[str, str] = {}  # conn_id -> caller_channel_id
        self.audio_socket_server: Optional[AudioSocketServer] = None
        self.audiosocket_conn_to_ssrc: Dict[str, int] = {}
        self.audiosocket_resample_state: Dict[str, Optional[tuple]] = {}
        self.pending_channel_for_bind: Optional[str] = None
        # Support duplicate Local ;1/;2 AudioSocket connections per call
        self.channel_to_conns: Dict[str, set] = {}
        self.audiosocket_primary_conn: Dict[str, str] = {}
        # Audio buffering for better playback quality
        self.audio_buffers: Dict[str, bytes] = {}
        self.buffer_size = 1600  # 200ms of audio at 8kHz (1600 bytes of ulaw)
        self.rtp_server: Optional[Any] = None
        self.headless_sessions: Dict[str, Dict[str, Any]] = {}
        # Bridge and Local channel tracking for Local Channel Bridge pattern
        self.bridges: Dict[str, str] = {}  # channel_id -> bridge_id
        # Frame processing and VAD for optimized audio handling
        self.frame_processors: Dict[str, AudioFrameProcessor] = {}  # conn_id -> processor
        self.vad_detectors: Dict[str, VoiceActivityDetector] = {}  # conn_id -> VAD
        self.local_channels: Dict[str, str] = {}  # channel_id -> legacy local_channel_id
        self.audiosocket_channels: Dict[str, str] = {}  # call_id -> audiosocket_channel_id
        
        self.vad_manager: Optional[EnhancedVADManager] = None
        try:
            vad_cfg = getattr(config, "vad", None)
            if vad_cfg and getattr(vad_cfg, "enhanced_enabled", False):
                self.vad_manager = EnhancedVADManager(
                    energy_threshold=int(getattr(vad_cfg, "energy_threshold", 1500)),
                    confidence_threshold=float(getattr(vad_cfg, "confidence_threshold", 0.6)),
                    adaptive_threshold_enabled=bool(getattr(vad_cfg, "adaptive_threshold_enabled", False)),
                    noise_adaptation_rate=float(getattr(vad_cfg, "noise_adaptation_rate", 0.1)),
                    webrtc_aggressiveness=int(getattr(vad_cfg, "webrtc_aggressiveness", 1)),
                    min_speech_frames=int(getattr(vad_cfg, "webrtc_start_frames", 2)),
                    max_silence_frames=int(getattr(vad_cfg, "webrtc_end_silence_frames", 15)),
                )
                logger.info(
                    "Enhanced VAD enabled",
                    energy_threshold=self.vad_manager.energy_threshold,
                    confidence_threshold=self.vad_manager.confidence_threshold,
                    adaptive=self.vad_manager.adaptive_threshold_enabled,
                )
        except Exception:
            logger.error("Failed to initialize Enhanced VAD", exc_info=True)

        # WebRTC VAD for robust speech detection
        self.webrtc_vad = None
        if WEBRTC_VAD_AVAILABLE:
            try:
                # Use VAD configuration section
                aggressiveness = config.vad.webrtc_aggressiveness
                self.webrtc_vad = webrtcvad.Vad(aggressiveness)
                logger.info("🎤 WebRTC VAD initialized", aggressiveness=aggressiveness)
            except Exception as e:
                logger.warning("🎤 WebRTC VAD initialization failed", error=str(e))
                self.webrtc_vad = None
        else:
            logger.warning("🎤 WebRTC VAD not available - install py-webrtcvad")
        # Map our synthesized UUID extension to the real ARI caller channel id
        self.uuidext_to_channel: Dict[str, str] = {}
        # NEW: Caller channel tracking for dual StasisStart handling
        self.pending_local_channels: Dict[str, str] = {}  # local_channel_id -> caller_channel_id
        self.pending_audiosocket_channels: Dict[str, str] = {}  # audiosocket_channel_id -> caller_channel_id
        self._audio_rx_debug: Dict[str, int] = {}
        self._keepalive_tasks: Dict[str, asyncio.Task] = {}
        # Active playbacks are now managed by SessionStore
        # ExternalMedia to caller channel mapping is now managed by SessionStore
        # SSRC to caller channel mapping for RTP audio routing
        self.ssrc_to_caller: Dict[int, str] = {}  # ssrc -> caller_channel_id
        # Pipeline runtime structures (Milestone 7): per-call audio queues and runner tasks
        self._pipeline_queues: Dict[str, asyncio.Queue] = {}
        self._pipeline_tasks: Dict[str, asyncio.Task] = {}
        # Track calls where a pipeline was explicitly requested via AI_PROVIDER
        self._pipeline_forced: Dict[str, bool] = {}
        # Health server runner
        self._health_runner: Optional[web.AppRunner] = None

        # Event handlers
        self.ari_client.on_event("StasisStart", self._handle_stasis_start)
        self.ari_client.on_event("StasisEnd", self._handle_stasis_end)
        self.ari_client.on_event("ChannelDestroyed", self._handle_channel_destroyed)
        self.ari_client.on_event("ChannelDtmfReceived", self._handle_dtmf_received)
        self.ari_client.on_event("ChannelVarset", self._handle_channel_varset)

    async def on_rtp_packet(self, packet: bytes, addr: tuple):
        """Handle incoming RTP packets from the UDP server."""
        # ARCHITECT FIX: This legacy bypass fragments STT and bypasses VAD
        # Log warning and disable to ensure all audio goes through VAD
        logger.warning("🚨 LEGACY RTP BYPASS - This method bypasses VAD and fragments STT", 
                      packet_len=len(packet), 
                      addr=addr)
        
        # Disable this bypass to prevent STT fragmentation
        # All audio should go through RTPServer -> _on_rtp_audio -> _process_rtp_audio_with_vad
        return
        
        # LEGACY CODE (disabled):
        # if self.active_calls:
        #     channel_id = list(self.active_calls.keys())[0]
        #     call_data = self.active_calls[channel_id]
        #     provider = call_data.get("provider")
        #     if provider:
        #         # The first 12 bytes of an RTP packet are the header. The rest is payload.
        #         audio_payload = packet[12:]
        #         await provider.send_audio(audio_payload)

    async def _on_ari_event(self, event: Dict[str, Any]):
        """Default event handler for unhandled ARI events."""
        logger.debug("Received unhandled ARI event", event_type=event.get("type"), ari_event=event)

    async def _save_session(self, session: CallSession, *, new: bool = False) -> None:
        """Persist session updates and keep coordinator metrics in sync."""
        await self.session_store.upsert_call(session)
        if self.conversation_coordinator:
            if new:
                await self.conversation_coordinator.register_call(session)
            else:
                await self.conversation_coordinator.sync_from_session(session)

    async def start(self):
        """Connect to ARI and start the engine."""
        # 1) Load providers first (low risk)
        await self._load_providers()

        # Milestone7: Start pipeline orchestrator to prepare per-call component lookups.
        try:
            await self.pipeline_orchestrator.start()
        except PipelineOrchestratorError as exc:
            logger.error(
                "Milestone7 pipeline orchestrator failed to start; legacy provider flow will be used",
                error=str(exc),
                exc_info=True,
            )
        except Exception as exc:
            logger.error(
                "Unexpected error starting pipeline orchestrator",
                error=str(exc),
                exc_info=True,
            )

        # 2) Start health server EARLY so diagnostics are available even if transport/ARI fail
        try:
            asyncio.create_task(self._start_health_server())
        except Exception:
            logger.debug("Health server failed to start", exc_info=True)

        # 3) Log transport and downstream modes
        logger.info("Runtime modes", audio_transport=self.config.audio_transport, downstream_mode=self.config.downstream_mode)

        # 4) Prepare AudioSocket transport (guarded)
        if self.config.audio_transport == "audiosocket":
            try:
                if not self.config.audiosocket:
                    raise ValueError("AudioSocket configuration not found")

                host = self.config.audiosocket.host
                port = self.config.audiosocket.port
                self.audio_socket_server = AudioSocketServer(
                    host=host,
                    port=port,
                    on_uuid=self._audiosocket_handle_uuid,
                    on_audio=self._audiosocket_handle_audio,
                    on_disconnect=self._audiosocket_handle_disconnect,
                    on_dtmf=self._audiosocket_handle_dtmf,
                )
                await self.audio_socket_server.start()
                logger.info("AudioSocket server listening", host=host, port=port)
                # Configure streaming manager with AudioSocket format expected by dialplan
                as_format = None
                try:
                    if self.config.audiosocket and hasattr(self.config.audiosocket, 'format'):
                        as_format = self.config.audiosocket.format
                except Exception:
                    as_format = None
                self.streaming_playback_manager.set_transport(
                    audio_transport=self.config.audio_transport,
                    audiosocket_server=self.audio_socket_server,
                    audiosocket_format=as_format,
                )
            except Exception as exc:
                logger.error("Failed to start AudioSocket transport", error=str(exc), exc_info=True)
                self.audio_socket_server = None

        # 5) Prepare RTP server for ExternalMedia transport (guarded)
        if self.config.audio_transport == "externalmedia":
            try:
                if not self.config.external_media:
                    raise ValueError("ExternalMedia configuration not found")
                
                rtp_host = self.config.external_media.rtp_host
                rtp_port = self.config.external_media.rtp_port
                codec = self.config.external_media.codec
                
                # Create RTP server with callback to route audio to providers
                self.rtp_server = RTPServer(
                    host=rtp_host,
                    port=rtp_port,
                    engine_callback=self._on_rtp_audio,
                    codec=codec
                )
                
                # Start RTP server
                await self.rtp_server.start()
                logger.info("RTP server started for ExternalMedia transport", 
                           host=rtp_host, port=rtp_port, codec=codec)
                self.streaming_playback_manager.set_transport(
                    rtp_server=self.rtp_server,
                    audio_transport=self.config.audio_transport,
                )
            except Exception as exc:
                logger.error("Failed to start ExternalMedia RTP transport", error=str(exc), exc_info=True)
                self.rtp_server = None

        # 6) Connect to ARI regardless to keep readiness visible and allow Stasis handling
        await self.ari_client.connect()
        # Add PlaybackFinished event handler for timing control
        self.ari_client.add_event_handler("PlaybackFinished", self._on_playback_finished)
        asyncio.create_task(self.ari_client.start_listening())
        logger.info("Engine started and listening for calls.")

    async def stop(self):
        """Disconnect from ARI and stop the engine."""
        # Clean up all sessions from SessionStore
        sessions = await self.session_store.get_all_sessions()
        for session in sessions:
            await self._cleanup_call(session.call_id)
        await self.ari_client.disconnect()
        # Stop RTP server if running
        if hasattr(self, 'rtp_server') and self.rtp_server:
            await self.rtp_server.stop()
        # Stop health server
        if self.audio_socket_server:
            await self.audio_socket_server.stop()
            self.audio_socket_server = None
        try:
            if self._health_runner:
                await self._health_runner.cleanup()
        except Exception:
            logger.debug("Health server cleanup error", exc_info=True)
        # Milestone7: ensure orchestrator releases component assignments before shutdown.
        try:
            await self.pipeline_orchestrator.stop()
        except Exception:
            logger.debug("Pipeline orchestrator stop error", exc_info=True)
        logger.info("Engine stopped.")

    async def _load_providers(self):
        """Load and initialize AI providers from the configuration."""
        logger.info("Loading AI providers...")
        for name, provider_config_data in self.config.providers.items():
            if isinstance(provider_config_data, dict) and not provider_config_data.get("enabled", True):
                logger.info("Provider '%s' disabled in configuration; skipping initialization.", name)
                continue
            try:
                if name == "local":
                    config = LocalProviderConfig(**provider_config_data)
                    provider = LocalProvider(config, self.on_provider_event)
                    self.providers[name] = provider
                    logger.info(f"Provider '{name}' loaded successfully.")
                    
                    # Provide initial greeting from global LLM config
                    try:
                        if hasattr(provider, 'set_initial_greeting'):
                            provider.set_initial_greeting(getattr(self.config.llm, 'initial_greeting', None))
                    except Exception:
                        logger.debug("Failed to set initial greeting on LocalProvider", exc_info=True)

                    # Initialize persistent connection for local provider
                    if hasattr(provider, 'initialize'):
                        await provider.initialize()
                        logger.info(f"Provider '{name}' connection initialized.")
                elif name == "deepgram":
                    deepgram_config = self._build_deepgram_config(provider_config_data)
                    if not deepgram_config:
                        continue

                    # Validate OpenAI dependency for Deepgram
                    if not self.config.llm.api_key:
                        logger.error("Deepgram provider requires OpenAI API key in LLM config")
                        continue

                    provider = DeepgramProvider(deepgram_config, self.config.llm, self.on_provider_event)
                    self.providers[name] = provider
                    logger.info("Provider 'deepgram' loaded successfully with OpenAI LLM dependency.")
                elif name == "openai_realtime":
                    openai_cfg = self._build_openai_realtime_config(provider_config_data)
                    if not openai_cfg:
                        continue

                    provider = OpenAIRealtimeProvider(openai_cfg, self.on_provider_event)
                    self.providers[name] = provider
                    logger.info("Provider 'openai_realtime' loaded successfully.")
                else:
                    logger.warning(f"Unknown provider type: {name}")
                    continue
                    
            except Exception as e:
                logger.error(f"Failed to load provider '{name}': {e}", exc_info=True)
        
        # Validate that default provider is available
        if self.config.default_provider not in self.providers:
            available_providers = list(self.providers.keys())
            logger.error(f"Default provider '{self.config.default_provider}' not available. Available providers: {available_providers}")
        else:
            logger.info(f"Default provider '{self.config.default_provider}' is available and ready.")

    def _is_caller_channel(self, channel: dict) -> bool:
        """Check if this is a caller channel (SIP, PJSIP, etc.)"""
        channel_name = channel.get('name', '')
        return any(channel_name.startswith(prefix) for prefix in ['SIP/', 'PJSIP/', 'DAHDI/', 'IAX2/'])

    def _is_local_channel(self, channel: dict) -> bool:
        """Check if this is a Local channel"""
        channel_name = channel.get('name', '')
        return channel_name.startswith('Local/')

    def _is_audiosocket_channel(self, channel: dict) -> bool:
        """Check if this is an AudioSocket channel (native channel interface)."""
        channel_name = channel.get('name', '')
        return channel_name.startswith('AudioSocket/')

    def _is_external_media_channel(self, channel: dict) -> bool:
        """Check if this is an ExternalMedia channel"""
        channel_name = channel.get('name', '')
        return channel_name.startswith('UnicastRTP/')

    async def _find_caller_for_local(self, local_channel_id: str) -> Optional[str]:
        """Find the caller channel that corresponds to this Local channel."""
        # Check if we have a pending Local channel mapping
        if local_channel_id in self.pending_local_channels:
            return self.pending_local_channels[local_channel_id]
        
        # Fallback: search through SessionStore
        sessions = await self.session_store.get_all_sessions()
        for session in sessions:
            if session.local_channel_id == local_channel_id:
                return session.caller_channel_id
        
        return None

    async def _handle_stasis_start(self, event: dict):
        """Handle StasisStart events - Hybrid ARI approach with single handler."""
        logger.info("🎯 HYBRID ARI - StasisStart event received", event_data=event)
        channel = event.get('channel', {})
        channel_id = channel.get('id')
        channel_name = channel.get('name', '')
        
        logger.info("🎯 HYBRID ARI - Channel analysis", 
                   channel_id=channel_id,
                   channel_name=channel_name,
                   is_caller=self._is_caller_channel(channel),
                   is_local=self._is_local_channel(channel))
        
        if self._is_caller_channel(channel):
            # This is the caller channel entering Stasis - MAIN FLOW
            logger.info("🎯 HYBRID ARI - Processing caller channel", channel_id=channel_id)
            await self._handle_caller_stasis_start_hybrid(channel_id, channel)
        elif self._is_local_channel(channel):
            # This is the Local channel entering Stasis - legacy path
            logger.info("🎯 HYBRID ARI - Local channel entered Stasis",
                       channel_id=channel_id,
                       channel_name=channel_name)
            # Now add the Local channel to the bridge
            await self._handle_local_stasis_start_hybrid(channel_id, channel)
        elif self._is_audiosocket_channel(channel):
            logger.info(
                "🎯 HYBRID ARI - AudioSocket channel entered Stasis",
                channel_id=channel_id,
                channel_name=channel_name,
            )
            await self._handle_audiosocket_channel_stasis_start(channel_id, channel)
        elif self._is_external_media_channel(channel):
            # This is an ExternalMedia channel entering Stasis
            logger.info("🎯 EXTERNAL MEDIA - ExternalMedia channel entered Stasis", 
                       channel_id=channel_id,
                       channel_name=channel_name)
            await self._handle_external_media_stasis_start(channel_id, channel)
        else:
            logger.warning("🎯 HYBRID ARI - Unknown channel type in StasisStart", 
                          channel_id=channel_id, 
                          channel_name=channel_name)

    async def _handle_external_media_stasis_start(self, external_media_id: str, channel: dict):
        """Handle ExternalMedia channel entering Stasis."""
        try:
            # Find session by external_media_id
            session = await self.session_store.get_by_channel_id(external_media_id)
            if not session:
                # Fallback: search all sessions for external_media_id
                sessions = await self.session_store.get_all_sessions()
                for s in sessions:
                    if s.external_media_id == external_media_id:
                        session = s
                        break
            
            if not session:
                logger.warning("ExternalMedia channel entered Stasis but no caller found", 
                             external_media_id=external_media_id)
                return
            
            caller_channel_id = session.caller_channel_id
            
            # Add ExternalMedia channel to the bridge
            bridge_id = session.bridge_id
            if bridge_id:
                success = await self.ari_client.add_channel_to_bridge(bridge_id, external_media_id)
                if success:
                    logger.info("🎯 EXTERNAL MEDIA - ExternalMedia channel added to bridge", 
                               external_media_id=external_media_id,
                               bridge_id=bridge_id,
                               caller_channel_id=caller_channel_id)
                    
                    # Start the provider session now that media path is connected
                    await self._start_provider_session(caller_channel_id)
                else:
                    logger.error("🎯 EXTERNAL MEDIA - Failed to add ExternalMedia channel to bridge", 
                               external_media_id=external_media_id,
                               bridge_id=bridge_id)
            else:
                logger.error("ExternalMedia channel entered Stasis but no bridge found", 
                           external_media_id=external_media_id,
                           caller_channel_id=caller_channel_id)
                
        except Exception as e:
            logger.error("Error handling ExternalMedia StasisStart", 
                        external_media_id=external_media_id, 
                        error=str(e), 
                        exc_info=True)

    async def _handle_caller_stasis_start_hybrid(self, caller_channel_id: str, channel: dict):
        """Handle caller channel entering Stasis - Hybrid ARI approach."""
        caller_info = channel.get('caller', {})
        logger.info("🎯 HYBRID ARI - Caller channel entered Stasis", 
                    channel_id=caller_channel_id,
                    caller_name=caller_info.get('name'),
                    caller_number=caller_info.get('number'))
        
        # Check if call is already in progress
        existing_session = await self.session_store.get_by_call_id(caller_channel_id)
        if existing_session:
            logger.warning("🎯 HYBRID ARI - Caller already in progress", channel_id=caller_channel_id)
            return
        
        try:
            # Step 1: Answer the caller
            logger.info("🎯 HYBRID ARI - Step 1: Answering caller channel", channel_id=caller_channel_id)
            await self.ari_client.answer_channel(caller_channel_id)
            logger.info("🎯 HYBRID ARI - Step 1: ✅ Caller channel answered", channel_id=caller_channel_id)
            
            # Step 2: Create bridge immediately
            logger.info("🎯 HYBRID ARI - Step 2: Creating bridge immediately", channel_id=caller_channel_id)
            bridge_id = await self.ari_client.create_bridge(bridge_type="mixing")
            if not bridge_id:
                raise RuntimeError("Failed to create mixing bridge")
            logger.info("🎯 HYBRID ARI - Step 2: ✅ Bridge created", 
                       channel_id=caller_channel_id, 
                       bridge_id=bridge_id)
            
            # Step 3: Add caller to bridge
            logger.info("🎯 HYBRID ARI - Step 3: Adding caller to bridge", 
                       channel_id=caller_channel_id, 
                       bridge_id=bridge_id)
            caller_success = await self.ari_client.add_channel_to_bridge(bridge_id, caller_channel_id)
            if not caller_success:
                raise RuntimeError("Failed to add caller channel to bridge")
            logger.info("🎯 HYBRID ARI - Step 3: ✅ Caller added to bridge", 
                       channel_id=caller_channel_id, 
                       bridge_id=bridge_id)
            self.bridges[caller_channel_id] = bridge_id
            
            # Step 4: Create CallSession and store in SessionStore
            session = CallSession(
                call_id=caller_channel_id,
                caller_channel_id=caller_channel_id,
                bridge_id=bridge_id,
                provider_name=self.config.default_provider,
                audio_capture_enabled=False,
                status="connected"
            )
            session.enhanced_vad_enabled = bool(self.vad_manager)
            await self._save_session(session, new=True)
            # Export config metrics for this call
            try:
                await self._export_config_metrics(caller_channel_id)
            except Exception:
                logger.debug("Failed to export config metrics for call", call_id=caller_channel_id, exc_info=True)
            logger.info("🎯 HYBRID ARI - Step 4: ✅ Caller session created and stored",
                       channel_id=caller_channel_id,
                       bridge_id=bridge_id)

            # Milestone7: Per-call override via Asterisk channel var AI_PROVIDER.
            # Values:
            #   - openai_realtime | deepgram → full agent override
            #   - customX (any other token) → pipeline name
            ai_provider_value = None
            try:
                resp = await self.ari_client.send_command(
                    "GET",
                    f"channels/{caller_channel_id}/variable",
                    params={"variable": "AI_PROVIDER"},
                )
                if isinstance(resp, dict):
                    ai_provider_value = (resp.get("value") or "").strip()
            except Exception:
                logger.debug(
                    "AI_PROVIDER read failed; continuing with defaults",
                    channel_id=caller_channel_id,
                    exc_info=True,
                )

            provider_aliases = {
                "openai": "openai_realtime",
                "deepgram_agent": "deepgram",
            }
            resolved_provider = (
                provider_aliases.get(ai_provider_value, ai_provider_value)
                if ai_provider_value
                else None
            )

            pipeline_resolution = None
            if resolved_provider and resolved_provider in self.providers:
                # Full agent override for this call
                previous = session.provider_name
                session.provider_name = resolved_provider
                await self._save_session(session)
                logger.info(
                    "AI provider override applied from channel variable",
                    channel_id=caller_channel_id,
                    variable="AI_PROVIDER",
                    value=ai_provider_value,
                    resolved_provider=resolved_provider,
                    previous_provider=previous,
                    resolved_mode="full_agent",
                )
            elif ai_provider_value:
                # Treat as a pipeline name for this call
                pipeline_resolution = await self._assign_pipeline_to_session(
                    session, pipeline_name=ai_provider_value
                )
                if pipeline_resolution:
                    logger.info(
                        "AI pipeline selection applied from channel variable",
                        channel_id=caller_channel_id,
                        variable="AI_PROVIDER",
                        value=ai_provider_value,
                        pipeline=pipeline_resolution.pipeline_name,
                        components=pipeline_resolution.component_summary(),
                        resolved_mode="pipeline",
                    )
                    # Opt-in to adapter-driven pipeline execution for this call
                    try:
                        await self._ensure_pipeline_runner(session, forced=True)
                    except Exception:
                        logger.debug("Failed to start pipeline runner", call_id=caller_channel_id, exc_info=True)
                elif getattr(self.pipeline_orchestrator, "started", False):
                    logger.warning(
                        "Requested pipeline via AI_PROVIDER not found; falling back",
                        channel_id=caller_channel_id,
                        requested_pipeline=ai_provider_value,
                    )
                    pipeline_resolution = await self._assign_pipeline_to_session(session)
            else:
                # Default behavior (use active_pipeline if configured)
                pipeline_resolution = await self._assign_pipeline_to_session(session)
                if not pipeline_resolution and getattr(self.pipeline_orchestrator, "started", False):
                    logger.info(
                        "Milestone7 pipeline orchestrator falling back to legacy provider flow",
                        call_id=caller_channel_id,
                        provider=session.provider_name,
                    )
            
            # Step 5: Create ExternalMedia channel or originate Local channel
            if self.config.audio_transport == "externalmedia":
                logger.info("🎯 EXTERNAL MEDIA - Step 5: Creating ExternalMedia channel", channel_id=caller_channel_id)
                external_media_id = await self._start_external_media_channel(caller_channel_id)
                if external_media_id:
                    # Update session with ExternalMedia ID
                    session.external_media_id = external_media_id
                    session.status = "external_media_created"
                    await self._save_session(session)
                    logger.info("🎯 EXTERNAL MEDIA - ExternalMedia channel created, session updated", 
                               channel_id=caller_channel_id, 
                               external_media_id=external_media_id)
                else:
                    logger.error("🎯 EXTERNAL MEDIA - Failed to create ExternalMedia channel", channel_id=caller_channel_id)
            else:
                logger.info("🎯 HYBRID ARI - Step 5: Originating AudioSocket channel", channel_id=caller_channel_id)
                await self._originate_audiosocket_channel_hybrid(caller_channel_id)
            
        except Exception as e:
            logger.error("🎯 HYBRID ARI - Failed to handle caller StasisStart", 
                        caller_channel_id=caller_channel_id, 
                        error=str(e), exc_info=True)
            await self._cleanup_call(caller_channel_id)

    async def _handle_local_stasis_start_hybrid(self, local_channel_id: str, channel: dict):
        """Handle Local channel entering Stasis - Hybrid ARI approach."""
        logger.info("🎯 HYBRID ARI - Processing Local channel StasisStart", 
                   local_channel_id=local_channel_id)
        
        # Find the caller channel that this Local channel belongs to
        caller_channel_id = await self._find_caller_for_local(local_channel_id)
        if not caller_channel_id:
            logger.error("🎯 HYBRID ARI - No caller found for Local channel", 
                        local_channel_id=local_channel_id)
            await self.ari_client.hangup_channel(local_channel_id)
            return
        
        # Check if caller channel exists and has a bridge
        session = await self.session_store.get_by_call_id(caller_channel_id)
        if not session:
            logger.error("🎯 HYBRID ARI - Caller channel not found for Local channel", 
                        local_channel_id=local_channel_id,
                        caller_channel_id=caller_channel_id)
            await self.ari_client.hangup_channel(local_channel_id)
            return
        
        bridge_id = session.bridge_id
        
        try:
            # Add Local channel to bridge
            logger.info("🎯 HYBRID ARI - Adding Local channel to bridge", 
                       local_channel_id=local_channel_id,
                       bridge_id=bridge_id)
            local_success = await self.ari_client.add_channel_to_bridge(bridge_id, local_channel_id)
            if local_success:
                logger.info("🎯 HYBRID ARI - ✅ Local channel added to bridge", 
                           local_channel_id=local_channel_id,
                           bridge_id=bridge_id)
                # Update session with Local channel info
                session.local_channel_id = local_channel_id
                session.status = "connected"
                await self._save_session(session)
                self.local_channels[caller_channel_id] = local_channel_id
                
                
                # Start provider session now that media path is connected
                await self._start_provider_session(caller_channel_id)
            else:
                logger.error("🎯 HYBRID ARI - Failed to add Local channel to bridge", 
                           local_channel_id=local_channel_id,
                           bridge_id=bridge_id)
                await self.ari_client.hangup_channel(local_channel_id)
        except Exception as e:
            logger.error("🎯 HYBRID ARI - Failed to handle Local channel StasisStart", 
                        local_channel_id=local_channel_id,
                        error=str(e), exc_info=True)
            await self.ari_client.hangup_channel(local_channel_id)

    async def _handle_audiosocket_channel_stasis_start(self, audiosocket_channel_id: str, channel: dict):
        """Handle AudioSocket channel entering Stasis when using channel interface."""
        logger.info(
            "🎯 HYBRID ARI - Processing AudioSocket channel StasisStart",
            audiosocket_channel_id=audiosocket_channel_id,
            channel_name=channel.get('name'),
        )

        caller_channel_id = self.pending_audiosocket_channels.pop(audiosocket_channel_id, None)
        if not caller_channel_id:
            # Fallback lookup via SessionStore
            sessions = await self.session_store.get_all_sessions()
            for s in sessions:
                if getattr(s, 'audiosocket_channel_id', None) == audiosocket_channel_id:
                    caller_channel_id = s.caller_channel_id
                    break

        if not caller_channel_id:
            logger.error(
                "🎯 HYBRID ARI - No caller found for AudioSocket channel",
                audiosocket_channel_id=audiosocket_channel_id,
            )
            await self.ari_client.hangup_channel(audiosocket_channel_id)
            return

        session = await self.session_store.get_by_call_id(caller_channel_id)
        if not session:
            logger.error(
                "🎯 HYBRID ARI - Session missing for AudioSocket channel",
                audiosocket_channel_id=audiosocket_channel_id,
                caller_channel_id=caller_channel_id,
            )
            await self.ari_client.hangup_channel(audiosocket_channel_id)
            return

        bridge_id = session.bridge_id
        if not bridge_id:
            logger.error(
                "🎯 HYBRID ARI - No bridge available for AudioSocket channel",
                audiosocket_channel_id=audiosocket_channel_id,
                caller_channel_id=caller_channel_id,
            )
            await self.ari_client.hangup_channel(audiosocket_channel_id)
            return

        try:
            added = await self.ari_client.add_channel_to_bridge(bridge_id, audiosocket_channel_id)
            if not added:
                raise RuntimeError("Failed to add AudioSocket channel to bridge")

            logger.info(
                "🎯 HYBRID ARI - ✅ AudioSocket channel added to bridge",
                audiosocket_channel_id=audiosocket_channel_id,
                bridge_id=bridge_id,
                caller_channel_id=caller_channel_id,
            )

            session.audiosocket_channel_id = audiosocket_channel_id
            session.status = "audiosocket_channel_connected"
            await self._save_session(session)

            self.audiosocket_channels[caller_channel_id] = audiosocket_channel_id
            self.bridges[audiosocket_channel_id] = bridge_id

            if not session.provider_session_active:
                await self._start_provider_session(caller_channel_id)
        except Exception as exc:
            logger.error(
                "🎯 HYBRID ARI - Failed to process AudioSocket channel",
                audiosocket_channel_id=audiosocket_channel_id,
                caller_channel_id=caller_channel_id,
                error=str(exc),
                exc_info=True,
            )
            await self.ari_client.hangup_channel(audiosocket_channel_id)

    async def _handle_caller_stasis_start(self, caller_channel_id: str, channel: dict):
        """Handle caller channel entering Stasis - LEGACY (kept for reference)."""
        caller_info = channel.get('caller', {})
        logger.info("Caller channel entered Stasis", 
                    channel_id=caller_channel_id,
                    caller_name=caller_info.get('name'),
                    caller_number=caller_info.get('number'))
        
        # Check if call is already in progress
        existing_session = await self.session_store.get_by_call_id(caller_channel_id)
        if existing_session:
            logger.warning("Caller already in progress", channel_id=caller_channel_id)
            return
        
        try:
            # Answer the caller
            await self.ari_client.answer_channel(caller_channel_id)
            logger.info("Caller channel answered", channel_id=caller_channel_id)
            
            # Create session in SessionStore
            session = CallSession(
                call_id=caller_channel_id,
                caller_channel_id=caller_channel_id,
                provider_name=self.config.default_provider,
                status="waiting_for_local",
                audio_capture_enabled=False
            )
            session.enhanced_vad_enabled = bool(self.vad_manager)
            await self._save_session(session, new=True)
            
            # Originate Local channel
            await self._originate_local_channel(caller_channel_id)
            
        except Exception as e:
            logger.error("Failed to handle caller StasisStart", 
                        caller_channel_id=caller_channel_id, 
                        error=str(e), exc_info=True)
            await self._cleanup_call(caller_channel_id)

    async def _handle_local_stasis_start(self, local_channel_id: str, channel: dict):
        """Handle Local channel entering Stasis."""
        logger.info("Local channel entered Stasis", 
                    channel_id=local_channel_id,
                    channel_name=channel.get('name'))
        
        try:
            # Find the caller this Local channel belongs to
            caller_channel_id = await self._find_caller_for_local(local_channel_id)
            if not caller_channel_id:
                logger.error("No caller found for Local channel", local_channel_id=local_channel_id)
                await self.ari_client.hangup_channel(local_channel_id)
                return
            
            # Update session with Local channel ID
            session = await self.session_store.get_by_call_id(caller_channel_id)
            if session:
                session.local_channel_id = local_channel_id
                await self._save_session(session)
                self.local_channels[caller_channel_id] = local_channel_id
            
            # Create bridge and connect channels
            await self._create_bridge_and_connect(caller_channel_id, local_channel_id)
            
        except Exception as e:
            logger.error("Failed to handle Local StasisStart", 
                        local_channel_id=local_channel_id, 
                        error=str(e), exc_info=True)
            # Clean up both channels
            caller_channel_id = await self._find_caller_for_local(local_channel_id)
            if caller_channel_id:
                await self._cleanup_call(caller_channel_id)
            await self.ari_client.hangup_channel(local_channel_id)

    async def _originate_audiosocket_channel_hybrid(self, caller_channel_id: str):
        """Originate an AudioSocket channel using the native channel interface."""
        if not self.config.audiosocket:
            logger.error(
                "🎯 HYBRID ARI - AudioSocket config missing, cannot originate channel",
                caller_channel_id=caller_channel_id,
            )
            raise RuntimeError("AudioSocket configuration missing")

        audio_uuid = str(uuid.uuid4())
        host = self.config.audiosocket.host or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = self.config.audiosocket.port
        # Match channel interface codec to YAML audiosocket.format
        codec = "slin"
        try:
            fmt = (getattr(self.config.audiosocket, 'format', '') or '').lower()
            if fmt in ("ulaw", "mulaw", "g711_ulaw"):
                codec = "ulaw"
            else:
                codec = "slin"
        except Exception:
            codec = "slin"
        endpoint = f"AudioSocket/{host}:{port}/{audio_uuid}/c({codec})"

        orig_params = {
            "endpoint": endpoint,
            "app": self.config.asterisk.app_name,
            "timeout": "30",
            "channelVars": {
                "AUDIOSOCKET_UUID": audio_uuid,
            },
        }

        logger.info(
            "🎯 HYBRID ARI - Originating AudioSocket channel",
            caller_channel_id=caller_channel_id,
            endpoint=endpoint,
            audio_uuid=audio_uuid,
        )

        try:
            response = await self.ari_client.send_command("POST", "channels", params=orig_params)
            if response and response.get("id"):
                audiosocket_channel_id = response["id"]
                self.pending_audiosocket_channels[audiosocket_channel_id] = caller_channel_id
                self.uuidext_to_channel[audio_uuid] = caller_channel_id

                session = await self.session_store.get_by_call_id(caller_channel_id)
                if session:
                    session.audiosocket_uuid = audio_uuid
                    await self._save_session(session)
                else:
                    logger.warning(
                        "🎯 HYBRID ARI - Session not found while recording AudioSocket UUID",
                        caller_channel_id=caller_channel_id,
                    )

                logger.info(
                    "🎯 HYBRID ARI - AudioSocket channel originated",
                    caller_channel_id=caller_channel_id,
                    audiosocket_channel_id=audiosocket_channel_id,
                )
            else:
                raise RuntimeError("Failed to originate AudioSocket channel")
        except Exception as e:
            logger.error(
                "🎯 HYBRID ARI - AudioSocket channel originate failed",
                caller_channel_id=caller_channel_id,
                error=str(e),
                exc_info=True,
            )
            raise

    async def _originate_local_channel_hybrid(self, caller_channel_id: str):
        """Originate single Local channel - Dialplan approach."""
        # Generate UUID for channel binding
        audio_uuid = str(uuid.uuid4())
        # Originate Local channel directly to dialplan context
        local_endpoint = f"Local/{audio_uuid}@ai-agent-media-fork/n"
        
        orig_params = {
            "endpoint": local_endpoint,
            "extension": audio_uuid,  # Use UUID as extension
            "context": "ai-agent-media-fork",  # Specify the dialplan context
            "timeout": "30"
        }
        
        logger.info("🎯 DIALPLAN EXTERNALMEDIA - Originating ExternalMedia Local channel", 
                    endpoint=local_endpoint, 
                    caller_channel_id=caller_channel_id,
                    audio_uuid=audio_uuid)
        
        try:
            response = await self.ari_client.send_command("POST", "channels", params=orig_params)
            if response and response.get("id"):
                local_channel_id = response["id"]
                # Store mapping for ExternalMedia binding
                self.pending_local_channels[local_channel_id] = caller_channel_id
                self.uuidext_to_channel[audio_uuid] = caller_channel_id
                logger.info("🎯 DIALPLAN EXTERNALMEDIA - ExternalMedia Local channel originated", 
                           local_channel_id=local_channel_id, 
                           caller_channel_id=caller_channel_id,
                           audio_uuid=audio_uuid)
                
                # Store Local channel info - will be added to bridge when ExternalMedia connects
                session = await self.session_store.get_by_call_id(caller_channel_id)
                if session:
                    session.external_media_id = local_channel_id
                    await self._save_session(session)
                    logger.info("🎯 DIALPLAN EXTERNALMEDIA - ExternalMedia channel ready for connection", 
                               local_channel_id=local_channel_id,
                               caller_channel_id=caller_channel_id)
                else:
                    logger.error("🎯 DIALPLAN EXTERNALMEDIA - Caller channel not found for ExternalMedia channel", 
                               local_channel_id=local_channel_id,
                               caller_channel_id=caller_channel_id)
                    raise RuntimeError("Caller channel not found")
            else:
                raise RuntimeError("Failed to originate ExternalMedia Local channel")
        except Exception as e:
            logger.error("🎯 DIALPLAN EXTERNALMEDIA - ExternalMedia channel originate failed", 
                        caller_channel_id=caller_channel_id,
                        audio_uuid=audio_uuid,
                        error=str(e), exc_info=True)
            raise

    async def _originate_local_channel(self, caller_channel_id: str):
        """Originate Local channel for ExternalMedia - LEGACY (kept for reference)."""
        local_endpoint = f"Local/{caller_channel_id}@ai-agent-media-fork/n"
        
        orig_params = {
            "endpoint": local_endpoint,
            "extension": caller_channel_id,
            "context": "ai-agent-media-fork",
            "priority": "1",
            "timeout": "30",
            "app": self.config.asterisk.app_name,
        }
        
        logger.info("Originating Local channel", 
                    endpoint=local_endpoint, 
                    caller_channel_id=caller_channel_id)
        
        try:
            response = await self.ari_client.send_command("POST", "channels", params=orig_params)
            if response and response.get("id"):
                local_channel_id = response["id"]
                # Store pending mapping
                self.pending_local_channels[local_channel_id] = caller_channel_id
                logger.info("Local channel originated", 
                           local_channel_id=local_channel_id, 
                           caller_channel_id=caller_channel_id)
            else:
                raise RuntimeError("Failed to originate Local channel")
        except Exception as e:
            logger.error("Local channel originate failed", 
                        caller_channel_id=caller_channel_id, 
                        error=str(e), exc_info=True)
            raise

    async def _handle_stasis_end(self, event: dict):
        """Handle StasisEnd event and clean up call resources."""
        try:
            channel = event.get("channel", {}) or {}
            channel_id = channel.get("id")
            if not channel_id:
                return
            logger.info("Stasis ended", channel_id=channel_id)
            await self._cleanup_call(channel_id)
        except Exception as exc:
            logger.error("Error handling StasisEnd", error=str(exc), exc_info=True)

    async def _handle_channel_destroyed(self, event: dict):
        """Clean up when a channel is destroyed."""
        try:
            channel = event.get("channel", {}) or {}
            channel_id = channel.get("id")
            if not channel_id:
                return
            logger.info("Channel destroyed", channel_id=channel_id)
            await self._cleanup_call(channel_id)
        except Exception as exc:
            logger.error("Error handling ChannelDestroyed", error=str(exc), exc_info=True)

    async def _handle_dtmf_received(self, event: dict):
        """Handle ChannelDtmfReceived events (informational logging for now)."""
        try:
            channel = event.get("channel", {}) or {}
            digit = event.get("digit")
            channel_id = channel.get("id")
            logger.info(
                "Channel DTMF received",
                channel_id=channel_id,
                digit=digit,
            )
        except Exception as exc:
            logger.error("Error handling ChannelDtmfReceived", error=str(exc), exc_info=True)

    async def _handle_channel_varset(self, event: dict):
        """Monitor ChannelVarset events for debugging configuration state."""
        try:
            channel = event.get("channel", {}) or {}
            variable = event.get("variable")
            value = event.get("value")
            channel_id = channel.get("id")
            logger.debug(
                "Channel variable set",
                channel_id=channel_id,
                variable=variable,
                value=value,
            )
        except Exception as exc:
            logger.error("Error handling ChannelVarset", error=str(exc), exc_info=True)

    async def _cleanup_call(self, channel_or_call_id: str) -> None:
        """Shared cleanup for StasisEnd/ChannelDestroyed paths."""
        try:
            # Resolve session by call_id first, then fallback to channel lookup.
            session = await self.session_store.get_by_call_id(channel_or_call_id)
            if not session:
                session = await self.session_store.get_by_channel_id(channel_or_call_id)
            if not session:
                logger.debug("No session found during cleanup", identifier=channel_or_call_id)
                return

            call_id = session.call_id
            logger.info("Cleaning up call", call_id=call_id)

            # Idempotent re-entrancy guard
            if getattr(session, "cleanup_completed", False):
                logger.debug("Cleanup already completed", call_id=call_id)
                return
            if getattr(session, "cleanup_in_progress", False):
                logger.debug("Cleanup already in progress", call_id=call_id)
                return
            try:
                session.cleanup_in_progress = True
                await self.session_store.upsert_call(session)
            except Exception:
                pass

            # Stop any active streaming playback.
            try:
                await self.streaming_playback_manager.stop_streaming_playback(call_id)
            except Exception:
                logger.debug("Streaming playback stop failed during cleanup", call_id=call_id, exc_info=True)

            # Stop the active provider session if one exists.
            try:
                provider_name = session.provider_name
                provider = self.providers.get(provider_name)
                if provider and hasattr(provider, "stop_session"):
                    await provider.stop_session()
            except Exception:
                logger.debug("Provider stop_session failed during cleanup", call_id=call_id, exc_info=True)

            # Tear down bridge.
            bridge_id = session.bridge_id
            if bridge_id:
                try:
                    await self.ari_client.destroy_bridge(bridge_id)
                    logger.info("Bridge destroyed", call_id=call_id, bridge_id=bridge_id)
                except Exception:
                    logger.debug("Bridge destroy failed", call_id=call_id, bridge_id=bridge_id, exc_info=True)

            # Hang up associated channels.
            for channel_id in filter(None, [session.caller_channel_id, session.local_channel_id, session.external_media_id, session.audiosocket_channel_id]):
                try:
                    await self.ari_client.hangup_channel(channel_id)
                except Exception:
                    logger.debug("Hangup failed during cleanup", call_id=call_id, channel_id=channel_id, exc_info=True)

            # Remove residual mappings so new calls don’t inherit.
            self.bridges.pop(session.caller_channel_id, None)
            if session.local_channel_id:
                self.pending_local_channels.pop(session.local_channel_id, None)
                self.local_channels.pop(session.caller_channel_id, None)
            if session.audiosocket_channel_id:
                self.pending_audiosocket_channels.pop(session.audiosocket_channel_id, None)
                self.audiosocket_channels.pop(session.caller_channel_id, None)
            if session.audiosocket_uuid:
                self.uuidext_to_channel.pop(session.audiosocket_uuid, None)

            # Cancel adapter pipeline runner, clear queue and forced flag
            try:
                task = self._pipeline_tasks.pop(call_id, None)
                if task:
                    task.cancel()
                q = self._pipeline_queues.pop(call_id, None)
                if q:
                    try:
                        q.put_nowait(None)
                    except Exception:
                        pass
                self._pipeline_forced.pop(call_id, None)
            except Exception:
                logger.debug("Pipeline cleanup failed", call_id=call_id, exc_info=True)

            # Remove SSRC mapping for this call (if any)
            try:
                to_delete = [ssrc for ssrc, cid in self.ssrc_to_caller.items() if cid == call_id]
                for ssrc in to_delete:
                    self.ssrc_to_caller.pop(ssrc, None)
            except Exception:
                pass

            # Release pipeline components before dropping session.
            if getattr(self, "pipeline_orchestrator", None) and self.pipeline_orchestrator.enabled:
                try:
                    await self.pipeline_orchestrator.release_pipeline(call_id)
                except Exception:
                    logger.debug("Milestone7 pipeline release failed during cleanup", call_id=call_id, exc_info=True)

            # Finally remove the session.
            await self.session_store.remove_call(call_id)

            if self.conversation_coordinator:
                await self.conversation_coordinator.unregister_call(call_id)
            
            # Clean up VAD manager state for this call
            if self.vad_manager:
                try:
                    await self.vad_manager.reset_call(call_id)
                    self.vad_manager.context_analyzer.cleanup_call(call_id)
                except Exception:
                    logger.debug("VAD cleanup failed during call cleanup", call_id=call_id, exc_info=True)

            try:
                # If the session still exists in store (rare race), mark completed; otherwise ignore
                sess2 = await self.session_store.get_by_call_id(call_id)
                if sess2:
                    sess2.cleanup_completed = True
                    sess2.cleanup_in_progress = False
                    await self.session_store.upsert_call(sess2)
            except Exception:
                pass

            logger.info("Call cleanup completed", call_id=call_id)
        except Exception as exc:
            logger.error("Error cleaning up call", identifier=channel_or_call_id, error=str(exc), exc_info=True)
        finally:
            # Best-effort: if session still exists and we marked in-progress, clear it to unblock future attempts
            try:
                sess3 = await self.session_store.get_by_call_id(channel_or_call_id)
                if not sess3:
                    sess3 = await self.session_store.get_by_channel_id(channel_or_call_id)
                if sess3 and getattr(sess3, "cleanup_in_progress", False) and not getattr(sess3, "cleanup_completed", False):
                    sess3.cleanup_in_progress = False
                    await self.session_store.upsert_call(sess3)
            except Exception:
                pass

    async def _create_bridge_and_connect(self, caller_channel_id: str, local_channel_id: str):
        """Create bridge and connect both channels."""
        try:
            # Create mixing bridge
            bridge_id = await self.ari_client.create_bridge(bridge_type="mixing")
            if not bridge_id:
                raise RuntimeError("Failed to create mixing bridge")
            
            logger.info("Bridge created", bridge_id=bridge_id, 
                       caller_channel_id=caller_channel_id, 
                       local_channel_id=local_channel_id)
            
            # Add both channels to bridge
            caller_success = await self.ari_client.add_channel_to_bridge(bridge_id, caller_channel_id)
            local_success = await self.ari_client.add_channel_to_bridge(bridge_id, local_channel_id)
            
            if not caller_success:
                logger.error("Failed to add caller channel to bridge", 
                            bridge_id=bridge_id, caller_channel_id=caller_channel_id)
                raise RuntimeError("Failed to add caller channel to bridge")
            
            if not local_success:
                logger.error("Failed to add Local channel to bridge", 
                            bridge_id=bridge_id, local_channel_id=local_channel_id)
                raise RuntimeError("Failed to add Local channel to bridge")
            
            # Store bridge info
            self.bridges[caller_channel_id] = bridge_id
            # Update session with bridge info
            call_id = session.call_id
            
            # Signal end of stream
            queue = getattr(session, "streaming_audio_queue", None)
            if queue:
                await queue.put(None)  # End of stream signal
            
            # Stop streaming playback
            if hasattr(session, "current_stream_id"):
                await self.streaming_playback_manager.stop_streaming_playback(call_id)
                session.current_stream_id = None
                session.streaming_started = False
            
            # Reset queue for the next response
            session.streaming_audio_queue = asyncio.Queue()
            await self._save_session(session)
            await self._reset_vad_after_playback(session)

            logger.info(
                "🎵 STREAMING DONE - Real-time audio streaming completed",
                call_id=call_id,
            )
            
            # Update conversation state
            if session.conversation_state == "greeting":
                session.conversation_state = "listening"
                logger.info("Greeting completed, now listening for conversation", call_id=call_id)
            elif session.conversation_state == "processing":
                session.conversation_state = "listening"
                logger.info("Response streamed, listening for next user input", call_id=call_id)
            
            await self._save_session(session)
            
            if self.conversation_coordinator:
                await self.conversation_coordinator.update_conversation_state(call_id, "listening")
                
        except Exception as e:
            logger.error("Error handling streaming audio done",
                        call_id=session.call_id,
                        error=str(e),
                        exc_info=True)
    
    async def _handle_streaming_ready(self, call_id: str) -> None:
        """Handle streaming ready event."""
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if session:
                session.streaming_ready = True
                await self._save_session(session)
                logger.info("🎵 STREAMING READY - Agent ready for streaming",
                           call_id=call_id)
        except Exception as e:
            logger.error("Error handling streaming ready",
                        call_id=call_id,
                        error=str(e))
    
    async def _handle_streaming_response(self, call_id: str) -> None:
        """Handle streaming response event."""
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if session:
                session.streaming_response = True
                await self._save_session(session)
                logger.info("🎵 STREAMING RESPONSE - Agent generating streaming response",
                           call_id=call_id)
        except Exception as e:
            logger.error("Error handling streaming response",
                        call_id=call_id,
                        error=str(e))

    async def _audiosocket_handle_uuid(self, conn_id: str, uuid_str: str) -> bool:
        """Bind inbound AudioSocket connection to the caller channel via UUID."""
        try:
            caller_channel_id = self.uuidext_to_channel.get(uuid_str)

            # Handle race where the TCP client connects before we finish recording
            # the UUID mapping. Give the originate path a brief window to catch up.
            if not caller_channel_id:
                for attempt in range(3):
                    await asyncio.sleep(0.05)
                    caller_channel_id = self.uuidext_to_channel.get(uuid_str)
                    if caller_channel_id:
                        logger.debug(
                            "AudioSocket UUID resolved after retry",
                            conn_id=conn_id,
                            uuid=uuid_str,
                            attempt=attempt + 1,
                        )
                        break

            if not caller_channel_id:
                logger.warning(
                    "AudioSocket UUID not recognized",
                    conn_id=conn_id,
                    uuid=uuid_str,
                )
                return False

            # Track mappings
            self.conn_to_channel[conn_id] = caller_channel_id
            self.channel_to_conn[caller_channel_id] = conn_id
            self.channel_to_conns.setdefault(caller_channel_id, set()).add(conn_id)
            if caller_channel_id not in self.audiosocket_primary_conn:
                self.audiosocket_primary_conn[caller_channel_id] = conn_id

            # Update session
            session = await self.session_store.get_by_call_id(caller_channel_id)
            if session:
                session.audiosocket_uuid = uuid_str
                # Record current AudioSocket connection for streaming playback
                try:
                    session.audiosocket_conn_id = conn_id
                except Exception:
                    pass
                session.status = "audiosocket_bound"
                await self._save_session(session)

            logger.info(
                "AudioSocket connection bound to caller",
                conn_id=conn_id,
                uuid=uuid_str,
                caller_channel_id=caller_channel_id,
            )
            return True
        except Exception as exc:
            logger.error("Error binding AudioSocket UUID", conn_id=conn_id, uuid=uuid_str, error=str(exc), exc_info=True)
            return False

    async def _audiosocket_handle_audio(self, conn_id: str, audio_bytes: bytes) -> None:
        """Forward inbound AudioSocket audio to the active provider for the bound call."""
        try:
            caller_channel_id = self.conn_to_channel.get(conn_id)
            if not caller_channel_id and self.audio_socket_server:
                # Fallback: resolve via server's UUID registry
                try:
                    uuid_str = self.audio_socket_server.get_uuid_for_conn(conn_id)
                    if uuid_str:
                        caller_channel_id = self.uuidext_to_channel.get(uuid_str)
                        if caller_channel_id:
                            self.conn_to_channel[conn_id] = caller_channel_id
                except Exception:
                    pass

            if not caller_channel_id:
                logger.debug("AudioSocket audio received for unknown connection", conn_id=conn_id, bytes=len(audio_bytes))
                return

            session = await self.session_store.get_by_call_id(caller_channel_id)
            if not session:
                logger.debug("No session for caller; dropping AudioSocket audio", conn_id=conn_id, caller_channel_id=caller_channel_id)
                return

            # Per-call RX bytes
            try:
                _STREAM_RX_BYTES.labels(caller_channel_id).inc(len(audio_bytes))
            except Exception:
                pass

            # Post-TTS end protection: drop inbound briefly after gating clears to avoid agent echo re-capture
            try:
                cfg = getattr(self.config, 'barge_in', None)
                post_guard_ms = int(getattr(cfg, 'post_tts_end_protection_ms', 0)) if cfg else 0
            except Exception:
                post_guard_ms = 0
            if post_guard_ms and getattr(session, 'tts_ended_ts', 0.0) and session.audio_capture_enabled:
                try:
                    elapsed_ms = int((time.time() - float(session.tts_ended_ts)) * 1000)
                except Exception:
                    elapsed_ms = post_guard_ms
                if elapsed_ms < post_guard_ms:
                    logger.debug(
                        "Dropping inbound during post-TTS protection window",
                        call_id=caller_channel_id,
                        elapsed_ms=elapsed_ms,
                        protect_ms=post_guard_ms,
                    )
                    return

            vad_result: Optional[VADResult] = None
            if self.vad_manager:
                try:
                    vad_result = await self._run_enhanced_vad(session, audio_bytes)
                except Exception:
                    logger.debug(
                        "Enhanced VAD processing error",
                        call_id=caller_channel_id,
                        exc_info=True,
                    )

            # Self-echo mitigation and barge-in detection
            # If TTS is playing (capture disabled), decide whether to drop or trigger barge-in
            if hasattr(session, 'audio_capture_enabled') and not session.audio_capture_enabled:
                cfg = getattr(self.config, 'barge_in', None)
                if not cfg or not getattr(cfg, 'enabled', True):
                    logger.debug("Dropping inbound AudioSocket audio during TTS playback (barge-in disabled)",
                                 conn_id=conn_id, caller_channel_id=caller_channel_id, bytes=len(audio_bytes))
                    return

                # Protection window from TTS start to avoid initial self-echo
                now = time.time()
                tts_elapsed_ms = 0
                try:
                    if getattr(session, 'tts_started_ts', 0.0) > 0:
                        tts_elapsed_ms = int((now - session.tts_started_ts) * 1000)
                except Exception:
                    tts_elapsed_ms = 0

                initial_protect = int(getattr(cfg, 'initial_protection_ms', 200))
                # Greeting-specific extra protection to avoid self-echo barge-in
                try:
                    if getattr(session, 'conversation_state', None) == 'greeting':
                        greet_ms = int(getattr(cfg, 'greeting_protection_ms', 0))
                        if greet_ms > initial_protect:
                            initial_protect = greet_ms
                except Exception:
                    pass
                if tts_elapsed_ms < initial_protect:
                    logger.debug("Dropping inbound during initial TTS protection window",
                                 conn_id=conn_id, caller_channel_id=caller_channel_id,
                                 tts_elapsed_ms=tts_elapsed_ms, protect_ms=initial_protect)
                    return

                # Barge-in detection: accumulate candidate window based on multi-criteria (VAD + energy)
                threshold = int(getattr(cfg, 'energy_threshold', 1000))
                frame_ms = 20
                energy = 0
                confidence = 0.0
                vad_speech = False
                webrtc_positive = False

                if vad_result:
                    frame_ms = max(vad_result.frame_duration_ms, 1)
                    energy = vad_result.energy_level
                    confidence = vad_result.confidence
                    vad_speech = vad_result.is_speech
                    webrtc_positive = vad_result.webrtc_result
                    try:
                        session.vad_state['last_vad_result'] = {
                            'is_speech': vad_speech,
                            'confidence': confidence,
                            'energy': energy,
                            'webrtc': webrtc_positive,
                        }
                    except Exception:
                        pass
                else:
                    try:
                        pcm16_frame = audioop.ulaw2lin(audio_bytes, 2)
                        energy = audioop.rms(pcm16_frame, 2)
                    except Exception:
                        energy = 0

                criteria_met = 0
                if vad_speech:
                    criteria_met += 1
                if energy >= threshold:
                    criteria_met += 1
                if vad_result and confidence >= getattr(self.vad_manager, 'confidence_threshold', 0.6):
                    criteria_met += 1
                if webrtc_positive:
                    criteria_met += 1

                if vad_result:
                    if criteria_met >= 2:
                        if int(getattr(session, 'barge_in_candidate_ms', 0)) == 0:
                            try:
                                session.barge_start_ts = now
                            except Exception:
                                session.barge_start_ts = 0.0
                        session.barge_in_candidate_ms = int(getattr(session, 'barge_in_candidate_ms', 0)) + frame_ms
                    else:
                        session.barge_in_candidate_ms = 0
                else:
                    if energy >= threshold:
                        if int(getattr(session, 'barge_in_candidate_ms', 0)) == 0:
                            try:
                                session.barge_start_ts = now
                            except Exception:
                                session.barge_start_ts = 0.0
                        session.barge_in_candidate_ms = int(getattr(session, 'barge_in_candidate_ms', 0)) + frame_ms
                    else:
                        session.barge_in_candidate_ms = 0

                # Cooldown check to avoid flapping
                cooldown_ms = int(getattr(cfg, 'cooldown_ms', 500))
                last_barge_in_ts = float(getattr(session, 'last_barge_in_ts', 0.0) or 0.0)
                in_cooldown = (now - last_barge_in_ts) * 1000 < cooldown_ms if last_barge_in_ts else False

                min_ms = int(getattr(cfg, 'min_ms', 250))
                should_trigger = not in_cooldown and session.barge_in_candidate_ms >= min_ms

                if should_trigger:
                    # Trigger barge-in: stop active playback(s), clear gating, and continue forwarding audio
                    try:
                        playback_ids = await self.session_store.list_playbacks_for_call(caller_channel_id)
                        for pid in playback_ids:
                            try:
                                await self.ari_client.stop_playback(pid)
                            except Exception:
                                logger.debug("Playback stop error during barge-in", playback_id=pid, exc_info=True)

                        # Clear all active gating tokens
                        tokens = list(getattr(session, 'tts_tokens', set()) or [])
                        for token in tokens:
                            try:
                                if self.conversation_coordinator:
                                    await self.conversation_coordinator.on_tts_end(caller_channel_id, token, reason="barge-in")
                            except Exception:
                                logger.debug("Failed to clear gating token during barge-in", token=token, exc_info=True)

                        session.barge_in_candidate_ms = 0
                        session.last_barge_in_ts = now
                        # Observe reaction latency if we captured onset
                        try:
                            if float(getattr(session, 'barge_start_ts', 0.0) or 0.0) > 0.0:
                                reaction_s = max(0.0, now - float(session.barge_start_ts))
                                _BARGE_REACTION_SECONDS.labels(caller_channel_id).observe(reaction_s)
                                session.barge_start_ts = 0.0
                        except Exception:
                            pass
                        await self._save_session(session)
                        
                        # Notify VAD manager of barge-in event for adaptive learning
                        if self.vad_manager and vad_result:
                            self.vad_manager.notify_call_event(
                                caller_channel_id, 
                                "barge_in", 
                                {"confidence": confidence, "energy": energy, "criteria_met": criteria_met}
                            )
                        
                        logger.info(
                            "🎧 BARGE-IN triggered",
                            call_id=caller_channel_id,
                            energy=energy,
                            criteria_met=criteria_met,
                            confidence=confidence,
                            vad_speech=vad_speech,
                            webrtc=webrtc_positive,
                        )
                    except Exception:
                        logger.error("Error triggering barge-in", call_id=caller_channel_id, exc_info=True)
                    # After barge-in, fall through to forward this frame to provider
                else:
                    # Not yet triggered; drop inbound frame while TTS is active
                    if energy > 0 and self.conversation_coordinator:
                        try:
                            self.conversation_coordinator.note_audio_during_tts(caller_channel_id)
                        except Exception:
                            pass
                    logger.debug(
                        "Dropping inbound during TTS",
                        call_id=caller_channel_id,
                        candidate_ms=session.barge_in_candidate_ms,
                        energy=energy,
                        criteria_met=criteria_met,
                        confidence=confidence,
                    )
                    return

            # If pipeline execution is forced, route to pipeline queue after converting to PCM16 @ 16 kHz
            if self._pipeline_forced.get(caller_channel_id):
                q = self._pipeline_queues.get(caller_channel_id)
                if q:
                    try:
                        pcm16 = self._as_to_pcm16_16k(audio_bytes)
                        q.put_nowait(pcm16)
                        return
                    except asyncio.QueueFull:
                        logger.debug("Pipeline queue full; dropping AudioSocket frame", call_id=caller_channel_id)
                        return

            # Enhanced VAD Audio Filtering with continuous delivery
            forward_original_audio = True
            payload_bytes = audio_bytes

            if vad_result:
                now = time.time()
                state = session.vad_state

                # Initialize VAD state if needed
                if 'vad_start_time' not in state:
                    state['vad_start_time'] = now
                    state['last_speech_time'] = now
                    state['frames_since_speech'] = 0

                frames_since_speech = int(state.get('frames_since_speech', 0))
                call_duration = now - float(state.get('vad_start_time', now))

                if call_duration >= 2.0:
                    forward_original_audio = (
                        vad_result.is_speech
                        or vad_result.confidence > 0.3
                        or frames_since_speech < 25
                        or self._should_use_vad_fallback(session)
                    )

                if vad_result.is_speech:
                    state['last_speech_time'] = now
                    state['frames_since_speech'] = 0
                else:
                    state['frames_since_speech'] = frames_since_speech + 1

                if not forward_original_audio:
                    payload_bytes = self._ulaw_silence(len(audio_bytes))
                    logger.debug(
                        "🎤 VAD - Replacing frame with silence",
                        call_id=caller_channel_id,
                        confidence=f"{vad_result.confidence:.2f}",
                        energy=vad_result.energy_level,
                        is_speech=vad_result.is_speech,
                        frames_since_speech=state.get('frames_since_speech', 0),
                    )

            provider_name = session.provider_name or self.config.default_provider
            provider = self.providers.get(provider_name)
            if not provider or not hasattr(provider, 'send_audio'):
                logger.debug("Provider unavailable for audio", provider=provider_name)
                return

            await provider.send_audio(payload_bytes)
        except Exception as exc:
            logger.error("Error handling AudioSocket audio", conn_id=conn_id, error=str(exc), exc_info=True)

    async def _run_enhanced_vad(self, session: CallSession, audio_bytes_ulaw: bytes) -> Optional[VADResult]:
        """Convert μ-law audio to 20 ms PCM16 frames and run enhanced VAD."""
        if not self.vad_manager or not audio_bytes_ulaw:
            return None

        try:
            pcm16 = EnhancedVADManager.mu_law_to_pcm16(audio_bytes_ulaw)
        except Exception:
            logger.debug(
                "Enhanced VAD conversion failed",
                call_id=session.call_id,
                exc_info=True,
            )
            return None

        if not pcm16:
            return None

        vad_state = session.vad_state.setdefault("enhanced_vad", {})
        frame_buffer: bytearray = vad_state.setdefault("frame_buffer", bytearray())
        frame_buffer.extend(pcm16)

        result: Optional[VADResult] = None
        stats = vad_state.setdefault("stats", {"frames": 0, "speech_frames": 0})

        while len(frame_buffer) >= 320:
            frame = bytes(frame_buffer[:320])
            del frame_buffer[:320]
            result = await self.vad_manager.process_frame(session.call_id, frame)
            stats["frames"] = stats.get("frames", 0) + 1
            if result.is_speech:
                stats["speech_frames"] = stats.get("speech_frames", 0) + 1

        if result:
            try:
                total = max(stats.get("frames", 0), 1)
                speech_ratio = stats.get("speech_frames", 0) / total
                session.vad_state["enhanced_summary"] = {
                    "frames": stats.get("frames", 0),
                    "speech_frames": stats.get("speech_frames", 0),
                    "speech_ratio": speech_ratio,
                    "last_confidence": result.confidence,
                    "last_energy": result.energy_level,
                }
            except Exception:
                pass

        return result

    def _should_use_vad_fallback(self, session: CallSession) -> bool:
        """Determine if we should use fallback audio forwarding when VAD doesn't detect speech."""
        try:
            vad_config = getattr(self.config, 'vad', None)
            if not vad_config or not getattr(vad_config, 'fallback_enabled', True):
                return False
            
            now = time.time()
            last_speech_time = session.vad_state.get('last_speech_time')
            if not last_speech_time:
                session.vad_state['last_speech_time'] = now
                return False

            silence_duration = (now - float(last_speech_time)) * 1000
            fallback_interval = getattr(vad_config, 'fallback_interval_ms', 1500)
            if silence_duration < fallback_interval:
                return False

            fallback_state = session.vad_state.setdefault('fallback_state', {
                'last_fallback_ts': 0.0,
            })

            last_fallback_ts = float(fallback_state.get('last_fallback_ts', 0.0) or 0.0)
            fallback_period_ms = 200  # Forward real audio every 200 ms during extended silence

            if (now - last_fallback_ts) * 1000 >= fallback_period_ms:
                fallback_state['last_fallback_ts'] = now
                logger.debug(
                    "🎤 VAD - Periodic fallback forwarding original audio",
                    call_id=session.call_id,
                    silence_duration_ms=int(silence_duration),
                    fallback_interval_ms=fallback_interval,
                )
                return True

            return False
            
        except Exception as e:
            logger.debug("VAD fallback logic error", call_id=session.call_id, error=str(e))
            return True  # Default to allowing audio through on error

    @staticmethod
    def _ulaw_silence(length: int) -> bytes:
        if length <= 0:
            return b""
        return bytes([0xFF]) * length

    async def _export_config_metrics(self, call_id: str) -> None:
        """Expose configured knobs as Prometheus gauges for this call."""
        try:
            b = getattr(self.config, 'barge_in', None)
            if b:
                _CFG_BARGE_MS.labels(call_id, "initial_protection_ms").set(int(getattr(b, 'initial_protection_ms', 0)))
                _CFG_BARGE_MS.labels(call_id, "min_ms").set(int(getattr(b, 'min_ms', 0)))
                _CFG_BARGE_MS.labels(call_id, "post_tts_end_protection_ms").set(int(getattr(b, 'post_tts_end_protection_ms', 0)))
                _CFG_BARGE_MS.labels(call_id, "greeting_protection_ms").set(int(getattr(b, 'greeting_protection_ms', 0)))
                _CFG_BARGE_THRESHOLD.labels(call_id).set(int(getattr(b, 'energy_threshold', 0)))
        except Exception:
            pass
        try:
            s = getattr(self.config, 'streaming', None)
            if s:
                _CFG_STREAM_MS.labels(call_id, "min_start_ms").set(int(getattr(s, 'min_start_ms', 0)))
                _CFG_STREAM_MS.labels(call_id, "greeting_min_start_ms").set(int(getattr(s, 'greeting_min_start_ms', 0)))
                _CFG_STREAM_MS.labels(call_id, "low_watermark_ms").set(int(getattr(s, 'low_watermark_ms', 0)))
                _CFG_STREAM_MS.labels(call_id, "jitter_buffer_ms").set(int(getattr(s, 'jitter_buffer_ms', 0)))
                _CFG_STREAM_MS.labels(call_id, "fallback_timeout_ms").set(int(getattr(s, 'fallback_timeout_ms', 0)))
        except Exception:
            pass
        try:
            pblock = (getattr(self.config, 'providers', {}) or {}).get('openai_realtime', {})
            td = (pblock or {}).get('turn_detection') or {}
            if td:
                _CFG_TD_MS.labels(call_id, "silence_duration_ms").set(int(td.get('silence_duration_ms', 0)))
                _CFG_TD_MS.labels(call_id, "prefix_padding_ms").set(int(td.get('prefix_padding_ms', 0)))
                try:
                    _CFG_TD_THRESHOLD.labels(call_id).set(float(td.get('threshold', 0.0)))
                except Exception:
                    pass
        except Exception:
            pass

    async def _audiosocket_handle_disconnect(self, conn_id: str) -> None:
        """Cleanup mappings when an AudioSocket connection disconnects."""
        try:
            caller_channel_id = self.conn_to_channel.pop(conn_id, None)
            if caller_channel_id:
                conns = self.channel_to_conns.get(caller_channel_id, set())
                conns.discard(conn_id)
                if not conns:
                    self.channel_to_conns.pop(caller_channel_id, None)
                # Reset primary if needed
                if self.audiosocket_primary_conn.get(caller_channel_id) == conn_id:
                    self.audiosocket_primary_conn.pop(caller_channel_id, None)
                    if conns:
                        self.audiosocket_primary_conn[caller_channel_id] = next(iter(conns))
                # Clear audiosocket_conn_id on session if it matched
                try:
                    sess = await self.session_store.get_by_call_id(caller_channel_id)
                    if sess and getattr(sess, 'audiosocket_conn_id', None) == conn_id:
                        sess.audiosocket_conn_id = None
                        await self._save_session(sess)
                except Exception:
                    pass
            logger.info("AudioSocket connection disconnected", conn_id=conn_id, caller_channel_id=caller_channel_id)
        except Exception as exc:
            logger.error("Error during AudioSocket disconnect cleanup", conn_id=conn_id, error=str(exc), exc_info=True)

    async def _audiosocket_handle_dtmf(self, conn_id: str, digit: str) -> None:
        """Handle DTMF received over AudioSocket (informational)."""
        try:
            caller_channel_id = self.conn_to_channel.get(conn_id)
            logger.info("AudioSocket DTMF received", conn_id=conn_id, caller_channel_id=caller_channel_id, digit=digit)
        except Exception as exc:
            logger.error("Error handling AudioSocket DTMF", conn_id=conn_id, error=str(exc), exc_info=True)

    async def _on_rtp_audio(self, ssrc: int, pcm_16k: bytes) -> None:
        """Route inbound ExternalMedia RTP audio (PCM16 @ 16 kHz) to the active provider.

        This mirrors the gating/barge-in logic of `_audiosocket_handle_audio` and
        establishes an SSRC→call_id mapping the first time we see a new SSRC.
        """
        try:
            # Resolve call_id from SSRC mapping or infer from sessions awaiting SSRC
            caller_channel_id = self.ssrc_to_caller.get(ssrc)
            if not caller_channel_id:
                # Choose the most recent session that has an ExternalMedia channel and no SSRC yet
                sessions = await self.session_store.get_all_sessions()
                candidate = None
                for s in sessions:
                    try:
                        if getattr(s, 'external_media_id', None) and not getattr(s, 'ssrc', None):
                            if candidate is None or float(getattr(s, 'created_at', 0.0)) > float(getattr(candidate, 'created_at', 0.0)):
                                candidate = s
                    except Exception:
                        continue
                if candidate:
                    caller_channel_id = candidate.caller_channel_id
                    self.ssrc_to_caller[ssrc] = caller_channel_id
                    try:
                        candidate.ssrc = ssrc
                        await self._save_session(candidate)
                    except Exception:
                        pass
                    try:
                        if getattr(self, 'rtp_server', None) and hasattr(self.rtp_server, 'map_ssrc_to_call_id'):
                            self.rtp_server.map_ssrc_to_call_id(ssrc, caller_channel_id)
                        
                    except Exception:
                        pass

            if not caller_channel_id:
                logger.debug("RTP audio received for unknown SSRC", ssrc=ssrc, bytes=len(pcm_16k))
                return

            session = await self.session_store.get_by_call_id(caller_channel_id)
            if not session:
                logger.debug("No session for caller; dropping RTP audio", ssrc=ssrc, caller_channel_id=caller_channel_id)
                return

            # Post-TTS end guard to avoid self-echo re-capture
            try:
                cfg = getattr(self.config, 'barge_in', None)
                post_guard_ms = int(getattr(cfg, 'post_tts_end_protection_ms', 0)) if cfg else 0
            except Exception:
                post_guard_ms = 0
            if post_guard_ms and getattr(session, 'tts_ended_ts', 0.0) and session.audio_capture_enabled:
                try:
                    elapsed_ms = int((time.time() - float(session.tts_ended_ts)) * 1000)
                except Exception:
                    elapsed_ms = post_guard_ms
                if elapsed_ms < post_guard_ms:
                    logger.debug(
                        "Dropping inbound RTP during post-TTS protection window",
                        call_id=caller_channel_id,
                        elapsed_ms=elapsed_ms,
                        protect_ms=post_guard_ms,
                    )
                    return

            # If TTS is playing (capture disabled), decide whether to drop or barge-in
            if hasattr(session, 'audio_capture_enabled') and not session.audio_capture_enabled:
                cfg = getattr(self.config, 'barge_in', None)
                if not cfg or not getattr(cfg, 'enabled', True):
                    logger.debug("Dropping inbound RTP during TTS playback (barge-in disabled)",
                                 ssrc=ssrc, caller_channel_id=caller_channel_id, bytes=len(pcm_16k))
                    return

                now = time.time()
                tts_elapsed_ms = 0
                try:
                    if getattr(session, 'tts_started_ts', 0.0) > 0:
                        tts_elapsed_ms = int((now - session.tts_started_ts) * 1000)
                except Exception:
                    tts_elapsed_ms = 0

                initial_protect = int(getattr(cfg, 'initial_protection_ms', 200))
                try:
                    if getattr(session, 'conversation_state', None) == 'greeting':
                        greet_ms = int(getattr(cfg, 'greeting_protection_ms', 0))
                        if greet_ms > initial_protect:
                            initial_protect = greet_ms
                except Exception:
                    pass
                if tts_elapsed_ms < initial_protect:
                    logger.debug("Dropping inbound RTP during initial TTS protection window",
                                 ssrc=ssrc, caller_channel_id=caller_channel_id,
                                 tts_elapsed_ms=tts_elapsed_ms, protect_ms=initial_protect)
                    return

                # Barge-in detection on PCM16 energy
                try:
                    energy = audioop.rms(pcm_16k, 2)
                except Exception:
                    energy = 0
                threshold = int(getattr(cfg, 'energy_threshold', 1000))
                frame_ms = 20
                if energy >= threshold:
                    if int(getattr(session, 'barge_in_candidate_ms', 0)) == 0:
                        try:
                            session.barge_start_ts = now
                        except Exception:
                            session.barge_start_ts = 0.0
                    session.barge_in_candidate_ms = int(getattr(session, 'barge_in_candidate_ms', 0)) + frame_ms
                else:
                    session.barge_in_candidate_ms = 0

                cooldown_ms = int(getattr(cfg, 'cooldown_ms', 500))
                last_barge_in_ts = float(getattr(session, 'last_barge_in_ts', 0.0) or 0.0)
                in_cooldown = (now - last_barge_in_ts) * 1000 < cooldown_ms if last_barge_in_ts else False

                min_ms = int(getattr(cfg, 'min_ms', 250))
                if not in_cooldown and session.barge_in_candidate_ms >= min_ms:
                    try:
                        playback_ids = await self.session_store.list_playbacks_for_call(caller_channel_id)
                        for pid in playback_ids:
                            try:
                                await self.ari_client.stop_playback(pid)
                            except Exception:
                                logger.debug("Playback stop error during RTP barge-in", playback_id=pid, exc_info=True)

                        tokens = list(getattr(session, 'tts_tokens', set()) or [])
                        for token in tokens:
                            try:
                                if self.conversation_coordinator:
                                    await self.conversation_coordinator.on_tts_end(caller_channel_id, token, reason="barge-in")
                            except Exception:
                                logger.debug("Failed to clear gating token during RTP barge-in", token=token, exc_info=True)

                        session.barge_in_candidate_ms = 0
                        session.last_barge_in_ts = now
                        try:
                            if float(getattr(session, 'barge_start_ts', 0.0) or 0.0) > 0.0:
                                reaction_s = max(0.0, now - float(session.barge_start_ts))
                                _BARGE_REACTION_SECONDS.labels(caller_channel_id).observe(reaction_s)
                                session.barge_start_ts = 0.0
                        except Exception:
                            pass
                        await self._save_session(session)
                        logger.info("🎧 BARGE-IN (RTP) triggered", call_id=caller_channel_id)
                    except Exception:
                        logger.error("Error triggering RTP barge-in", call_id=caller_channel_id, exc_info=True)
                else:
                    # Not yet triggered; drop inbound frame while TTS is active
                    if energy > 0 and self.conversation_coordinator:
                        try:
                            self.conversation_coordinator.note_audio_during_tts(caller_channel_id)
                        except Exception:
                            pass
                    logger.debug("Dropping inbound RTP during TTS (candidate_ms=%d, energy=%d)",
                                 session.barge_in_candidate_ms, energy)
                    return

            # If a pipeline was explicitly requested for this call, route to pipeline queue
            if self._pipeline_forced.get(caller_channel_id):
                q = self._pipeline_queues.get(caller_channel_id)
                if q:
                    try:
                        q.put_nowait(pcm_16k)
                        return
                    except asyncio.QueueFull:
                        logger.debug("Pipeline queue full; dropping RTP frame", call_id=caller_channel_id)
                        return

            provider_name = session.provider_name or self.config.default_provider
            provider = self.providers.get(provider_name)
            if not provider or not hasattr(provider, 'send_audio'):
                logger.debug("Provider unavailable for RTP audio", provider=provider_name)
                return

            # Forward PCM16 16k frames to provider
            await provider.send_audio(pcm_16k)
        except Exception as exc:
            logger.error("Error handling RTP audio", ssrc=ssrc, error=str(exc), exc_info=True)

    def _build_deepgram_config(self, provider_cfg: Dict[str, Any]) -> Optional[DeepgramProviderConfig]:
        """Construct a DeepgramProviderConfig from raw provider settings with validation."""
        try:
            cfg = DeepgramProviderConfig(**provider_cfg)
            if not cfg.api_key:
                logger.error("Deepgram provider API key missing (DEEPGRAM_API_KEY)")
                return None
            return cfg
        except Exception as exc:
            logger.error("Failed to build DeepgramProviderConfig", error=str(exc), exc_info=True)
            return None

    def _build_openai_realtime_config(self, provider_cfg: Dict[str, Any]) -> Optional[OpenAIRealtimeProviderConfig]:
        """Construct an OpenAIRealtimeProviderConfig from raw provider settings."""
        try:
            # Respect provider overrides; only fill when missing/empty
            merged = dict(provider_cfg)
            try:
                instr = (merged.get("instructions") or "").strip()
            except Exception:
                instr = ""
            if not instr:
                merged["instructions"] = getattr(self.config.llm, "prompt", None)
            try:
                greet = (merged.get("greeting") or "").strip()
            except Exception:
                greet = ""
            if not greet:
                merged["greeting"] = getattr(self.config.llm, "initial_greeting", None)

            cfg = OpenAIRealtimeProviderConfig(**merged)
            if not cfg.enabled:
                logger.info("OpenAI Realtime provider disabled in configuration; skipping initialization.")
                return None
            if not cfg.api_key:
                logger.error("OpenAI Realtime provider API key missing (OPENAI_API_KEY)")
                return None
            return cfg
        except Exception as exc:
            logger.error("Failed to build OpenAIRealtimeProviderConfig", error=str(exc), exc_info=True)
            return None

    async def on_provider_event(self, event: Dict[str, Any]):
        """Handle async events from the active provider (Deepgram/OpenAI/local).

        For file-based downstream (current default), buffer AgentAudio bytes until
        AgentAudioDone, then play the accumulated audio via PlaybackManager.
        """
        try:
            etype = event.get("type")
            call_id = event.get("call_id")
            if not call_id:
                return

            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.warning("Provider event for unknown call", event_type=etype, call_id=call_id)
                return

            # Downstream strategy: stream provider audio in near-real time via StreamingPlaybackManager
            if etype == "AgentAudio":
                chunk: bytes = event.get("data") or b""
                if not chunk:
                    return
                # Ensure a streaming queue exists and streaming is started
                q = self._provider_stream_queues.get(call_id)
                if q is None:
                    q = asyncio.Queue(maxsize=256)
                    self._provider_stream_queues[call_id] = q
                    # Kick off streaming playback; manager is idempotent if already active
                    try:
                        playback_type = "greeting" if getattr(session, "conversation_state", "") == "greeting" else "streaming-response"
                        await self.streaming_playback_manager.start_streaming_playback(call_id, q, playback_type=playback_type)
                    except Exception:
                        logger.error("Failed to start streaming playback", call_id=call_id, exc_info=True)
                        # Fallback to file playback if streaming cannot start
                        try:
                            playback_id = await self.playback_manager.play_audio(call_id, chunk, "streaming-response")
                            if not playback_id:
                                logger.error("Fallback file playback failed", call_id=call_id, size=len(chunk))
                            return
                        except Exception:
                            logger.error("Fallback file playback exception", call_id=call_id, exc_info=True)
                            return
                # Enqueue chunk (drop on full to avoid backpressure loops)
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    logger.debug("Provider streaming queue full; dropping chunk", call_id=call_id)
            elif etype == "AgentAudioDone":
                q = self._provider_stream_queues.get(call_id)
                if q is not None:
                    # Signal end of stream
                    try:
                        q.put_nowait(None)  # sentinel for StreamingPlaybackManager
                    except asyncio.QueueFull:
                        # Even if full, attempt graceful end later
                        asyncio.create_task(q.put(None))
                    # Clear saved queue reference
                    self._provider_stream_queues.pop(call_id, None)
                else:
                    logger.debug("AgentAudioDone with no active stream queue", call_id=call_id)
            else:
                # Log control/JSON events at debug for now
                logger.debug("Provider control event", provider_event=event)

        except Exception as exc:
            logger.error("Error handling provider event", error=str(exc), exc_info=True)

    def _as_to_pcm16_16k(self, audio_bytes: bytes) -> bytes:
        """Convert AudioSocket inbound bytes to PCM16 @ 16 kHz for pipeline STT.

        Assumes AudioSocket format is 8 kHz μ-law (default) or PCM16.
        """
        try:
            fmt = None
            try:
                if self.config and getattr(self.config, 'audiosocket', None):
                    fmt = (self.config.audiosocket.format or 'ulaw').lower()
            except Exception:
                fmt = 'ulaw'
            if fmt in ('ulaw', 'mulaw', 'g711_ulaw'):
                pcm8k = audioop.ulaw2lin(audio_bytes, 2)
            else:
                # Treat as PCM16 8 kHz
                pcm8k = audio_bytes
            pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
            return pcm16k
        except Exception:
            logger.debug("AudioSocket -> PCM16 16k conversion failed", exc_info=True)
            return audio_bytes

    async def _ensure_pipeline_runner(self, session: CallSession, *, forced: bool = False) -> None:
        """Create per-call queue and start pipeline runner if not already started."""
        call_id = session.call_id
        if call_id in self._pipeline_tasks:
            if forced:
                self._pipeline_forced[call_id] = True
            return
        # Require orchestrator enabled and a selected pipeline
        if not getattr(self, 'pipeline_orchestrator', None) or not self.pipeline_orchestrator.enabled:
            return
        if not getattr(session, 'pipeline_name', None):
            return
        # Create queue and start task
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._pipeline_queues[call_id] = q
        self._pipeline_forced[call_id] = bool(forced)
        task = asyncio.create_task(self._pipeline_runner(call_id))
        self._pipeline_tasks[call_id] = task
        logger.info("Pipeline runner started", call_id=call_id, pipeline=session.pipeline_name)

    async def _pipeline_runner(self, call_id: str) -> None:
        """Minimal adapter-driven loop: STT -> LLM -> TTS -> file playback.

        Designed to be opt-in (forced via AI_PROVIDER=pipeline_name) to avoid
        impacting the tested ExternalMedia + Local full-agent path.
        """
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                return
            pipeline = self.pipeline_orchestrator.get_pipeline(call_id, getattr(session, 'pipeline_name', None))
            if not pipeline:
                logger.debug("Pipeline runner: no pipeline resolved", call_id=call_id)
                return
            # Open per-call state for adapters (best-effort)
            try:
                await pipeline.stt_adapter.open_call(call_id, pipeline.stt_options)
            except Exception:
                logger.debug("STT open_call failed", call_id=call_id, exc_info=True)
            else:
                logger.info("Pipeline STT adapter session opened", call_id=call_id)
            try:
                await pipeline.llm_adapter.open_call(call_id, pipeline.llm_options)
            except Exception:
                logger.debug("LLM open_call failed", call_id=call_id, exc_info=True)
            else:
                logger.info("Pipeline LLM adapter session opened", call_id=call_id)
            try:
                await pipeline.tts_adapter.open_call(call_id, pipeline.tts_options)
            except Exception:
                logger.debug("TTS open_call failed", call_id=call_id, exc_info=True)
            else:
                logger.info("Pipeline TTS adapter session opened", call_id=call_id)

            # Pipeline-managed initial greeting (optional)
            greeting = ""
            try:
                greeting = (getattr(self.config.llm, "initial_greeting", None) or "").strip()
            except Exception:
                greeting = ""
            if greeting:
                max_attempts = 2
                for attempt in range(1, max_attempts + 1):
                    try:
                        tts_bytes = bytearray()
                        async for chunk in pipeline.tts_adapter.synthesize(call_id, greeting, pipeline.tts_options):
                            if chunk:
                                tts_bytes.extend(chunk)
                        if not tts_bytes:
                            logger.warning(
                                "Pipeline greeting produced no audio",
                                call_id=call_id,
                                attempt=attempt,
                            )
                        else:
                            await self.playback_manager.play_audio(call_id, bytes(tts_bytes), "pipeline-tts-greeting")
                        break
                    except RuntimeError as exc:
                        error_text = str(exc).lower()
                        if attempt < max_attempts and "session" in error_text:
                            logger.debug(
                                "Pipeline greeting retry after session error",
                                call_id=call_id,
                                attempt=attempt,
                                exc_info=True,
                            )
                            try:
                                await pipeline.tts_adapter.open_call(call_id, pipeline.tts_options)
                                continue
                            except Exception:
                                logger.debug(
                                    "Pipeline greeting re-open_call failed",
                                    call_id=call_id,
                                    attempt=attempt,
                                    exc_info=True,
                                )
                        logger.error(
                            "Pipeline greeting synthesis failed",
                            call_id=call_id,
                            attempt=attempt,
                            error=str(exc),
                            exc_info=True,
                        )
                        break
                    except Exception:
                        logger.error(
                            "Pipeline greeting unexpected failure",
                            call_id=call_id,
                            attempt=attempt,
                            exc_info=True,
                        )
                        break

            # Accumulate into ~160ms chunks for STT while keeping ingestion responsive
            bytes_per_ms = 32  # 16k Hz * 2 bytes / 1000 ms
            base_commit_ms = 160
            stt_chunk_ms = int(pipeline.stt_options.get("chunk_ms", base_commit_ms)) if pipeline.stt_options else base_commit_ms
            commit_ms = max(stt_chunk_ms, 80)
            commit_bytes = bytes_per_ms * commit_ms

            inbound_queue = self._pipeline_queues.get(call_id)
            if not inbound_queue:
                return

            buffer_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=200)
            transcript_queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=8)

            use_streaming = bool((pipeline.stt_options or {}).get("streaming", False))
            if use_streaming:
                streaming_supported = all(
                    hasattr(pipeline.stt_adapter, attr)
                    for attr in ("start_stream", "send_audio", "iter_results", "stop_stream")
                )
                if not streaming_supported:
                    logger.warning(
                        "Streaming STT requested but adapter does not support streaming APIs; falling back to chunked mode",
                        call_id=call_id,
                        component=getattr(pipeline.stt_adapter, "component_key", "unknown"),
                    )
                    use_streaming = False
            stream_format = (pipeline.stt_options or {}).get("stream_format", "pcm16_16k")
            if use_streaming:
                try:
                    logger.info(
                        "Streaming STT enabled",
                        call_id=call_id,
                        commit_ms=commit_ms,
                        stream_format=stream_format,
                        buffer_max=getattr(buffer_queue, "_maxsize", 200) if hasattr(buffer_queue, "_maxsize") else 200,
                    )
                except Exception:
                    logger.debug("Streaming STT info log failed", exc_info=True)

            async def enqueue_buffer(item: Optional[bytes]) -> None:
                if item is None:
                    await buffer_queue.put(None)
                    return
                while True:
                    if buffer_queue.full():
                        dropped = await buffer_queue.get()
                        if dropped is not None:
                            logger.debug(
                                "Pipeline audio buffer overflow; dropping oldest frame",
                                call_id=call_id,
                            )
                        continue
                    await buffer_queue.put(item)
                    return

            async def ingest_audio() -> None:
                try:
                    while True:
                        chunk = await inbound_queue.get()
                        if chunk is None:
                            await enqueue_buffer(None)
                            break
                        await enqueue_buffer(chunk)
                except asyncio.CancelledError:
                    pass

            if not use_streaming:

                async def process_audio(audio_chunk: bytes) -> None:
                    transcript = ""
                    try:
                        transcript = await pipeline.stt_adapter.transcribe(
                            call_id,
                            audio_chunk,
                            16000,
                            pipeline.stt_options,
                        )
                    except Exception:
                        logger.debug("STT transcribe failed", call_id=call_id, exc_info=True)
                        return
                    transcript = (transcript or "").strip()
                    if not transcript:
                        return
                    # Record time when a final transcript is obtained
                    try:
                        self._last_transcript_ts[call_id] = time.time()
                    except Exception:
                        pass
                    try:
                        transcript_queue.put_nowait(transcript)
                    except asyncio.QueueFull:
                        try:
                            dropped = transcript_queue.get_nowait()
                            logger.warning(
                                "Pipeline transcript backlog full; dropping oldest transcript",
                                call_id=call_id,
                                dropped_preview=(dropped or "")[:80] if dropped else "",
                            )
                        except asyncio.QueueEmpty:
                            pass
                        await transcript_queue.put(transcript)

                async def stt_worker() -> None:
                    local_buf = bytearray()
                    try:
                        while True:
                            frame = await buffer_queue.get()
                            if frame is None:
                                if local_buf:
                                    await process_audio(bytes(local_buf))
                                await transcript_queue.put(None)
                                break
                            local_buf.extend(frame)
                            if len(local_buf) < commit_bytes:
                                continue
                            await process_audio(bytes(local_buf))
                            local_buf.clear()
                    except asyncio.CancelledError:
                        pass

            else:

                async def stt_sender() -> None:
                    local_buf = bytearray()
                    try:
                        while True:
                            frame = await buffer_queue.get()
                            if frame is None:
                                if local_buf:
                                    try:
                                        await pipeline.stt_adapter.send_audio(
                                            call_id,
                                            bytes(local_buf),
                                            fmt=stream_format,
                                        )
                                    except Exception:
                                        logger.debug(
                                            "Streaming STT final send failed",
                                            call_id=call_id,
                                            exc_info=True,
                                        )
                                    local_buf.clear()
                                break
                            local_buf.extend(frame)
                            if len(local_buf) < commit_bytes:
                                continue
                            chunk = bytes(local_buf)
                            local_buf.clear()
                            try:
                                await pipeline.stt_adapter.send_audio(
                                    call_id,
                                    chunk,
                                    fmt=stream_format,
                                )
                            except Exception:
                                logger.debug(
                                    "Streaming STT send failed",
                                    call_id=call_id,
                                    exc_info=True,
                                )
                    except asyncio.CancelledError:
                        pass
                    finally:
                        if local_buf:
                            try:
                                await pipeline.stt_adapter.send_audio(
                                    call_id,
                                    bytes(local_buf),
                                    fmt=stream_format,
                                )
                            except Exception:
                                logger.debug(
                                    "Streaming STT residual send failed",
                                    call_id=call_id,
                                    exc_info=True,
                                )

                async def stt_receiver() -> None:
                    try:
                        async for final in pipeline.stt_adapter.iter_results(call_id):
                            try:
                                # Record time when a final transcript arrives
                                self._last_transcript_ts[call_id] = time.time()
                                transcript_queue.put_nowait(final)
                            except asyncio.QueueFull:
                                try:
                                    transcript_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                                await transcript_queue.put(final)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.debug(
                            "Streaming STT receive loop error",
                            call_id=call_id,
                            exc_info=True,
                        )
                    finally:
                        try:
                            transcript_queue.put_nowait(None)
                        except asyncio.QueueFull:
                            pass

            async def dialog_worker() -> None:
                pending_segments: List[str] = []
                flush_task: Optional[asyncio.Task] = None
                accumulation_timeout = float(
                    (pipeline.llm_options or {}).get("aggregation_timeout_sec", 2.0)
                )

                async def cancel_flush() -> None:
                    nonlocal flush_task
                    if flush_task and not flush_task.done():
                        current = asyncio.current_task()
                        if flush_task is not current:
                            flush_task.cancel()
                    flush_task = None

                async def run_turn(transcript_text: str) -> None:
                    response_text = ""
                    pipeline_label = getattr(session, 'pipeline_name', None) or 'none'
                    provider_label = getattr(session, 'provider_name', None) or 'unknown'
                    t_start = self._last_transcript_ts.get(call_id)
                    try:
                        response_text = await pipeline.llm_adapter.generate(
                            call_id,
                            transcript_text,
                            {"messages": [{"role": "user", "content": transcript_text}]},
                            pipeline.llm_options,
                        )
                    except Exception:
                        logger.debug("LLM generate failed", call_id=call_id, exc_info=True)
                        return
                    response_text = (response_text or "").strip()
                    if not response_text:
                        return
                    tts_bytes = bytearray()
                    first_tts_ts: Optional[float] = None
                    try:
                        async for tts_chunk in pipeline.tts_adapter.synthesize(
                            call_id,
                            response_text,
                            pipeline.tts_options,
                        ):
                            if tts_chunk:
                                if first_tts_ts is None:
                                    first_tts_ts = time.time()
                                    try:
                                        if t_start is not None:
                                            _TURN_STT_TO_TTS.labels(pipeline_label, provider_label).observe(max(0.0, first_tts_ts - t_start))
                                    except Exception:
                                        pass
                                tts_bytes.extend(tts_chunk)
                    except Exception:
                        logger.debug("TTS synth failed", call_id=call_id, exc_info=True)
                        return
                    if not tts_bytes:
                        return
                    try:
                        playback_id = await self.playback_manager.play_audio(
                            call_id,
                            bytes(tts_bytes),
                            "pipeline-tts",
                        )
                        try:
                            if playback_id and t_start is not None:
                                _TURN_RESPONSE_SECONDS.labels(pipeline_label, provider_label).observe(max(0.0, time.time() - t_start))
                        except Exception:
                            pass
                        if not playback_id:
                            logger.error(
                                "Pipeline playback failed",
                                call_id=call_id,
                                size=len(tts_bytes),
                            )
                    except Exception:
                        logger.error("Pipeline playback exception", call_id=call_id, exc_info=True)

                async def maybe_respond(force: bool, from_flush: bool = False) -> None:
                    nonlocal pending_segments, flush_task
                    if not pending_segments:
                        if from_flush:
                            flush_task = None
                        else:
                            await cancel_flush()
                        return
                    aggregated = " ".join(pending_segments).strip()
                    if not aggregated:
                        pending_segments.clear()
                        if from_flush:
                            flush_task = None
                        else:
                            await cancel_flush()
                        return
                    words = len([w for w in aggregated.split() if w])
                    chars = len(aggregated.replace(" ", ""))
                    threshold_met = words >= 3 or chars >= 12
                    if not threshold_met:
                        if force:
                            pending_segments.clear()
                            if from_flush:
                                flush_task = None
                            else:
                                await cancel_flush()
                        else:
                            logger.debug(
                                "Accumulating transcript before LLM",
                                call_id=call_id,
                                preview=aggregated[:80],
                                chars=chars,
                                words=words,
                            )
                        return
                    if from_flush:
                        flush_task = None
                    else:
                        await cancel_flush()
                    await run_turn(aggregated)
                    pending_segments.clear()

                async def schedule_flush() -> None:
                    nonlocal flush_task
                    await cancel_flush()

                    async def _flush() -> None:
                        try:
                            await asyncio.sleep(accumulation_timeout)
                            await maybe_respond(force=True, from_flush=True)
                        except asyncio.CancelledError:
                            pass

                    flush_task = asyncio.create_task(_flush())

                try:
                    while True:
                        transcript = await transcript_queue.get()
                        if transcript is None:
                            await maybe_respond(force=True)
                            break
                        normalized = (transcript or "").strip()
                        if not normalized:
                            if pending_segments and flush_task is None:
                                await schedule_flush()
                            continue
                        pending_segments.append(normalized)
                        await maybe_respond(force=False)
                        if pending_segments:
                            await schedule_flush()
                except asyncio.CancelledError:
                    pass
                finally:
                    await cancel_flush()

            ingest_task = asyncio.create_task(ingest_audio())

            if use_streaming:
                stt_send_task: Optional[asyncio.Task] = None
                stt_recv_task: Optional[asyncio.Task] = None
                dialog_task: Optional[asyncio.Task] = None
                stop_called = False

                try:
                    await pipeline.stt_adapter.start_stream(call_id, pipeline.stt_options or {})
                    stt_send_task = asyncio.create_task(stt_sender())
                    stt_recv_task = asyncio.create_task(stt_receiver())
                    dialog_task = asyncio.create_task(dialog_worker())

                    if stt_send_task:
                        await stt_send_task
                    await pipeline.stt_adapter.stop_stream(call_id)
                    stop_called = True
                    await asyncio.gather(
                        *(task for task in (stt_recv_task, dialog_task) if task is not None),
                        return_exceptions=True,
                    )
                finally:
                    ingest_task.cancel()
                    tasks_to_cancel = []
                    for task in (stt_send_task, stt_recv_task, dialog_task):
                        if task and not task.done():
                            task.cancel()
                            tasks_to_cancel.append(task)
                    await asyncio.gather(ingest_task, *tasks_to_cancel, return_exceptions=True)
                    if not stop_called:
                        await pipeline.stt_adapter.stop_stream(call_id)
            else:
                stt_task = asyncio.create_task(stt_worker())
                dialog_task = asyncio.create_task(dialog_worker())

                try:
                    await dialog_task
                finally:
                    ingest_task.cancel()
                    stt_task.cancel()
                    await asyncio.gather(ingest_task, stt_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("Pipeline runner crashed", call_id=call_id, exc_info=True)

    async def _assign_pipeline_to_session(
        self,
        session: CallSession,
        pipeline_name: Optional[str] = None,
    ) -> Optional[PipelineResolution]:
        """Milestone7: Resolve pipeline components for a session and persist metadata."""
        if not getattr(self, "pipeline_orchestrator", None):
            return None
        if not self.pipeline_orchestrator.enabled:
            return None
        try:
            resolution = self.pipeline_orchestrator.get_pipeline(session.call_id, pipeline_name)
        except PipelineOrchestratorError as exc:
            logger.error(
                "Milestone7 pipeline resolution failed",
                call_id=session.call_id,
                requested_pipeline=pipeline_name,
                error=str(exc),
                exc_info=True,
            )
            return None
        except Exception as exc:
            logger.error(
                "Milestone7 pipeline resolution unexpected error",
                call_id=session.call_id,
                requested_pipeline=pipeline_name,
                error=str(exc),
                exc_info=True,
            )
            return None
 
        if not resolution:
            logger.debug(
                "Milestone7 pipeline orchestrator returned no resolution",
                call_id=session.call_id,
                requested_pipeline=pipeline_name,
            )
            return None
 
        component_summary = resolution.component_summary()
        updated = False
 
        if session.pipeline_name != resolution.pipeline_name:
            session.pipeline_name = resolution.pipeline_name
            updated = True
 
        if session.pipeline_components != component_summary:
            session.pipeline_components = component_summary
            updated = True
 
        provider_override = resolution.primary_provider
        if provider_override:
            if provider_override in self.providers:
                if session.provider_name != provider_override:
                    logger.info(
                        "Milestone7 pipeline overriding provider",
                        call_id=session.call_id,
                        previous_provider=session.provider_name,
                        override_provider=provider_override,
                    )
                    session.provider_name = provider_override
                    updated = True
            else:
                logger.warning(
                    "Milestone7 pipeline requested provider not loaded; continuing with session provider",
                    call_id=session.call_id,
                    requested_provider=provider_override,
                    current_provider=session.provider_name,
                    available_providers=list(self.providers.keys()),
                )
 
        if updated:
            await self._save_session(session)
 
        if not resolution.prepared:
            resolution.prepared = True
            logger.info(
                "Milestone7 pipeline resolved",
                call_id=session.call_id,
                pipeline=session.pipeline_name,
                components=component_summary,
                provider=session.provider_name,
            )
            options_summary = resolution.options_summary()
            if any(options_summary.values()):
                logger.debug(
                    "Milestone7 pipeline options",
                    call_id=session.call_id,
                    pipeline=session.pipeline_name,
                    options=options_summary,
                )
 
        return resolution
 
    async def _start_provider_session(self, call_id: str) -> None:
        """Start the provider session for a call when media path is ready."""
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.error("Start provider session called for unknown call", call_id=call_id)
                return

            # Preserve any per-call override previously applied. Only assign a pipeline
            # here if one has already been selected (e.g., via AI_PROVIDER or active_pipeline)
            pipeline_resolution = None
            if getattr(self.pipeline_orchestrator, "enabled", False):
                if getattr(session, "pipeline_name", None):
                    pipeline_resolution = await self._assign_pipeline_to_session(
                        session, pipeline_name=session.pipeline_name
                    )

            # Pipeline-only mode: if a pipeline is selected for this call, do not start
            # the legacy provider session or play the provider-managed greeting.
            if pipeline_resolution:
                logger.info(
                    "Pipeline-only mode: skipping legacy provider session; greeting will be handled by pipeline",
                    call_id=call_id,
                    pipeline=pipeline_resolution.pipeline_name,
                )
                try:
                    await self._ensure_pipeline_runner(session, forced=True)
                except Exception:
                    logger.debug(
                        "Failed to ensure pipeline runner in _start_provider_session",
                        call_id=call_id,
                        exc_info=True,
                    )
                return

            provider_name = session.provider_name or self.config.default_provider
            provider = self.providers.get(provider_name)

            if not provider:
                fallback_name = self.config.default_provider
                fallback_provider = self.providers.get(fallback_name)
                if fallback_provider:
                    logger.warning(
                        "Milestone7 pipeline provider unavailable; falling back to default provider",
                        call_id=call_id,
                        requested_provider=provider_name,
                        fallback_provider=fallback_name,
                    )
                    provider_name = fallback_name
                    provider = fallback_provider
                    if session.provider_name != fallback_name:
                        session.provider_name = fallback_name
                        await self._save_session(session)
                else:
                    logger.error(
                        "No provider available to start session",
                        call_id=call_id,
                        requested_provider=provider_name,
                        fallback_provider=fallback_name,
                    )
                    return

            if pipeline_resolution:
                logger.info(
                    "Milestone7 pipeline starting provider session",
                    call_id=call_id,
                    pipeline=pipeline_resolution.pipeline_name,
                    components=pipeline_resolution.component_summary(),
                    provider=provider_name,
                )
            elif getattr(self.pipeline_orchestrator, "enabled", False):
                logger.debug(
                    "Milestone7 pipeline orchestrator did not return a resolution; using legacy provider flow",
                    call_id=call_id,
                    provider=provider_name,
                )
            # Set provider input mode based on transport so send_audio can convert properly
            try:
                if hasattr(provider, 'set_input_mode'):
                    if self.config.audio_transport == 'externalmedia':
                        provider.set_input_mode('pcm16_16k')
                    else:
                        # Determine input mode from AudioSocket format
                        as_fmt = None
                        try:
                            if self.config.audiosocket and hasattr(self.config.audiosocket, 'format'):
                                as_fmt = (self.config.audiosocket.format or '').lower()
                        except Exception:
                            as_fmt = None
                        if as_fmt in ('ulaw', 'mulaw', 'g711_ulaw'):
                            provider.set_input_mode('mulaw8k')
                        else:
                            # Default to PCM16 at 8 kHz when AudioSocket is slin16 or unspecified
                            provider.set_input_mode('pcm16_8k')
            except Exception:
                logger.debug("Provider set_input_mode failed or unsupported", exc_info=True)

            await provider.start_session(call_id)
            # If provider supports an explicit greeting (e.g., LocalProvider), trigger it now
            try:
                if hasattr(provider, 'play_initial_greeting'):
                    await provider.play_initial_greeting(call_id)
            except Exception:
                logger.debug("Provider initial greeting failed or unsupported", exc_info=True)
            session.provider_session_active = True
            # Ensure upstream capture is enabled for real-time providers when not gated
            try:
                if not session.tts_playing and not session.audio_capture_enabled:
                    session.audio_capture_enabled = True
            except Exception:
                pass
            await self._save_session(session)
            # Sync gauges if coordinator is present
            if self.conversation_coordinator:
                try:
                    await self.conversation_coordinator.sync_from_session(session)
                except Exception:
                    pass
            logger.info("Provider session started", call_id=call_id, provider=provider_name)
        except Exception as exc:
            logger.error("Failed to start provider session", call_id=call_id, error=str(exc), exc_info=True)

    async def _on_playback_finished(self, event: Dict[str, Any]):
        """Delegate ARI PlaybackFinished to PlaybackManager for gating and cleanup."""
        try:
            playback_id = None
            playback = event.get("playback", {}) or {}
            playback_id = playback.get("id") or event.get("playbackId")
            if not playback_id:
                logger.debug("PlaybackFinished without playback id", playback_event=event)
                return
            await self.playback_manager.on_playback_finished(playback_id)
        except Exception as exc:
            logger.error("Error in PlaybackFinished handler", error=str(exc), exc_info=True)

    async def _start_health_server(self):
        """Start aiohttp health/metrics server on 0.0.0.0:15000."""
        try:
            app = web.Application()
            app.router.add_get('/live', self._live_handler)
            app.router.add_get('/ready', self._ready_handler)
            app.router.add_get('/health', self._health_handler)
            app.router.add_get('/metrics', self._metrics_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 15000)
            await site.start()
            self._health_runner = runner
            logger.info("Health endpoint started", host="0.0.0.0", port=15000)
        except Exception as exc:
            logger.error("Failed to start health endpoint", error=str(exc), exc_info=True)

    async def _health_handler(self, request):
        """Return JSON with engine/provider status."""
        try:
            providers = {}
            for name, prov in (self.providers or {}).items():
                ready = True
                try:
                    if hasattr(prov, 'is_ready'):
                        ready = bool(prov.is_ready())
                except Exception:
                    ready = True
                providers[name] = {"ready": ready}

            # Compute readiness
            default_ready = False
            if self.config and getattr(self.config, 'default_provider', None) in (self.providers or {}):
                prov = self.providers[self.config.default_provider]
                try:
                    default_ready = bool(prov.is_ready()) if hasattr(prov, 'is_ready') else True
                except Exception:
                    default_ready = True
            ari_connected = bool(self.ari_client and self.ari_client.running)
            audiosocket_listening = self.audio_socket_server is not None if self.config.audio_transport == 'audiosocket' else True
            is_ready = ari_connected and audiosocket_listening and default_ready

            payload = {
                "status": "healthy" if is_ready else "degraded",
                "ari_connected": ari_connected,
                "rtp_server_running": bool(getattr(self, 'rtp_server', None)),
                "audio_transport": self.config.audio_transport,
                "active_calls": len(await self.session_store.get_all_sessions()),
                "active_playbacks": 0,
                "providers": providers,
                "rtp_server": {},
                "audiosocket": {
                    "listening": audiosocket_listening,
                    "host": getattr(self.config.audiosocket, 'host', None) if self.config.audiosocket else None,
                    "port": getattr(self.config.audiosocket, 'port', None) if self.config.audiosocket else None,
                    "active_connections": (self.audio_socket_server.get_connection_count() if self.audio_socket_server else 0),
                },
                "audiosocket_listening": audiosocket_listening,
                "conversation": {
                    "gating_active": 0,
                    "capture_disabled": 0,
                    "barge_in_total": 0,
                },
                "streaming": {},
                "streaming_details": [],
            }
            return web.json_response(payload)
        except Exception as exc:
            return web.json_response({"status": "error", "error": str(exc)}, status=500)

    async def _live_handler(self, request):
        """Liveness probe: returns 200 if process is up."""
        return web.Response(text="ok", status=200)

    async def _ready_handler(self, request):
        """Readiness probe: 200 only if ARI, transport, and default provider are ready."""
        try:
            ari_connected = bool(self.ari_client and self.ari_client.running)
            transport_ok = True
            if self.config.audio_transport == 'audiosocket':
                transport_ok = self.audio_socket_server is not None
            elif self.config.audio_transport == 'externalmedia':
                transport_ok = self.rtp_server is not None
            provider_ok = False
            if self.config and getattr(self.config, 'default_provider', None) in (self.providers or {}):
                prov = self.providers[self.config.default_provider]
                try:
                    provider_ok = bool(prov.is_ready()) if hasattr(prov, 'is_ready') else True
                except Exception:
                    provider_ok = True

            is_ready = ari_connected and transport_ok and provider_ok
            status = 200 if is_ready else 503
            return web.json_response({
                "ari_connected": ari_connected,
                "transport_ok": transport_ok,
                "provider_ok": provider_ok,
                "ready": is_ready,
            }, status=status)
        except Exception as exc:
            return web.json_response({"ready": False, "error": str(exc)}, status=500)

    async def _metrics_handler(self, request):
        """Expose Prometheus metrics."""
        try:
            data = generate_latest()
            # aiohttp forbids 'charset=' inside content_type arg; pass full header via headers.
            return web.Response(body=data, headers={"Content-Type": CONTENT_TYPE_LATEST})
        except Exception as exc:
            return web.Response(text=str(exc), status=500)


async def main():
    config = load_config()
    # Initialize structured logging according to YAML-configured level (default INFO)
    try:
        level_name = str(getattr(getattr(config, 'logging', None), 'level', 'info')).upper()
        level = getattr(logging, level_name, logging.INFO)
        configure_logging(log_level=level)
    except Exception:
        # Fallback to INFO if configuration not yet available
        configure_logging(log_level="INFO")
    engine = Engine(config)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    service_task = loop.create_task(engine.start())
    await shutdown_event.wait()

    await engine.stop()
    service_task.cancel()
    try:
        await service_task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("AI Voice Agent has shut down.")
