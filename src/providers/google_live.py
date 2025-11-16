"""
Google Gemini Live API provider implementation.

This module integrates Google's Gemini Live API (bidirectional streaming) into the
Asterisk AI Voice Agent. Audio from AudioSocket is resampled to PCM16 @ 16 kHz,
streamed to Gemini Live API, and PCM16 output is resampled to the configured
downstream AudioSocket format (µ-law or PCM16 8 kHz).

Key features:
- Real-time bidirectional voice streaming
- Native audio processing (no separate STT/TTS)
- Built-in Voice Activity Detection (VAD)
- Barge-in support
- Function calling / tool use
- Session management for long conversations
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import struct
import audioop
from typing import Any, Dict, Optional, List
from collections import deque

import websockets
from websockets import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from structlog import get_logger
from prometheus_client import Gauge, Counter

from .base import AIProviderInterface, ProviderCapabilities
from ..audio import (
    convert_pcm16le_to_target_format,
    mulaw_to_pcm16le,
    resample_audio,
)
from ..config import GoogleProviderConfig

# Tool calling support
from src.tools.registry import tool_registry
from src.tools.adapters.google import GoogleToolAdapter

logger = get_logger(__name__)

# Constants
_GEMINI_INPUT_RATE = 16000  # Gemini requires 16kHz input
_GEMINI_OUTPUT_RATE = 24000  # Gemini outputs 24kHz audio
_COMMIT_INTERVAL_SEC = 0.02  # 20ms chunks (320 bytes at 16kHz)
_KEEPALIVE_INTERVAL_SEC = 15.0
_WEBSOCKET_ENDPOINT = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

# Metrics
_GOOGLE_LIVE_SESSIONS = Gauge(
    "ai_agent_google_live_active_sessions",
    "Number of active Google Live API sessions",
)
_GOOGLE_LIVE_AUDIO_SENT = Counter(
    "ai_agent_google_live_audio_bytes_sent",
    "Total audio bytes sent to Google Live API",
    labelnames=("call_id",),
)
_GOOGLE_LIVE_AUDIO_RECEIVED = Counter(
    "ai_agent_google_live_audio_bytes_received",
    "Total audio bytes received from Google Live API",
    labelnames=("call_id",),
)


class GoogleLiveProvider(AIProviderInterface):
    """
    Google Gemini Live API provider using bidirectional WebSocket streaming.

    Lifecycle:
    1. start_session(call_id) -> establishes WebSocket, sends setup message
    2. send_audio(bytes) -> converts AudioSocket frames to PCM16 16kHz, streams to Gemini
    3. Receive server responses: audio, text transcription, tool calls
    4. stop_session() -> closes WebSocket and cancels background tasks

    Audio flow:
    - Input: 8kHz µ-law → 16kHz PCM16 → Gemini Live API
    - Output: 24kHz PCM16 from Gemini → 8kHz µ-law/PCM16 → AudioSocket
    """

    def __init__(
        self,
        config: GoogleProviderConfig,
        on_event,
        gating_manager=None,
    ):
        super().__init__(on_event)
        self.config = config
        self.websocket: Optional[WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._gating_manager = gating_manager

        self._call_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._setup_complete: bool = False
        self._greeting_completed: bool = False
        self._in_audio_burst: bool = False
        self._setup_ack_event: Optional[asyncio.Event] = None  # ACK gate like Deepgram
        self._hangup_after_response: bool = False  # Flag to trigger hangup after next response
        self._farewell_in_progress: bool = False  # Track if farewell is being spoken
        # Conversation state
        self._conversation_history: List[Dict[str, Any]] = []
        
        # Transcription buffering - hold latest partial until turnComplete
        self._input_transcription_buffer: str = ""
        self._output_transcription_buffer: str = ""
        
        # VAD burst accumulation state (for improved speech recognition)
        self._speech_buffer = bytearray()
        self._is_speech_active = False
        self._silence_frames = 0
        self._silence_threshold = 10  # frames (~200ms @ 20ms/frame)
        self._speech_threshold_rms = 100  # RMS threshold for speech detection (lowered for telephony)
        self._min_speech_frames = 3  # Minimum frames to start accumulation (~60ms)
        self._speech_frames = 0
        
        # Metrics tracking
        self._session_start_time: Optional[float] = None

    @staticmethod
    def get_capabilities() -> Optional[ProviderCapabilities]:
        """Return capabilities of Google Live provider for transport orchestration."""
        return ProviderCapabilities(
            # Audio format capabilities
            input_encodings=["ulaw", "pcm16"],  # μ-law or PCM16
            input_sample_rates_hz=[8000, 16000],  # Telephony or wideband
            output_encodings=["ulaw", "pcm16"],  # Output resampled to telephony
            output_sample_rates_hz=[8000, 16000, 24000],  # Gemini native is 24kHz
            preferred_chunk_ms=20,  # 20ms chunks for smooth streaming
            can_negotiate=True,  # Can adapt to different formats
            # Provider type and audio processing capabilities
            is_full_agent=True,  # Full bidirectional agent (not pipeline component)
            has_native_vad=True,  # Gemini Live has built-in Voice Activity Detection
            has_native_barge_in=True,  # Handles interruptions automatically
            requires_continuous_audio=True,  # Needs continuous audio stream for VAD
        )
    
    @property
    def supported_codecs(self) -> List[str]:
        """Return list of supported audio codecs (μ-law for telephony)."""
        return ["ulaw"]

    async def start_session(
        self,
        call_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Start a Google Gemini Live API session.

        Args:
            call_id: Unique identifier for the call
            context: Optional context including system prompt, tools, etc.
        """
        self._call_id = call_id
        self._session_start_time = time.time()
        self._conversation_history = []
        self._setup_complete = False
        self._greeting_completed = False

        logger.info(
            "Starting Google Live session",
            call_id=call_id,
            model=self.config.llm_model,
        )

        # Build WebSocket URL with API key
        api_key = self.config.api_key or ""
        
        # Debug: Check API key
        if not api_key:
            logger.error(
                "GOOGLE_API_KEY not found! Cannot connect to Google Live API.",
                call_id=call_id,
            )
            raise ValueError("GOOGLE_API_KEY is required for Google Live provider")
        
        api_key_preview = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "<too_short>"
        logger.debug(
            "Connecting to Google Live API",
            call_id=call_id,
            endpoint=_WEBSOCKET_ENDPOINT,
            api_key_preview=api_key_preview,
        )
        
        ws_url = f"{_WEBSOCKET_ENDPOINT}?key={api_key}"

        try:
            # Establish WebSocket connection
            self.websocket = await websockets.connect(
                ws_url,
                subprotocols=["gemini-live"],
                max_size=10 * 1024 * 1024,  # 10MB max message size
            )
            
            _GOOGLE_LIVE_SESSIONS.inc()
            
            logger.info(
                "Google Live WebSocket connected",
                call_id=call_id,
            )

            # Create ACK event BEFORE sending setup (like Deepgram pattern)
            self._setup_ack_event = asyncio.Event()
            
            # Start receive loop FIRST (so it can catch setupComplete)
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"google-live-receive-{call_id}",
            )
            
            # Send setup message to configure session
            await self._send_setup(context)
            
            # Wait for setup acknowledgment
            logger.debug("Waiting for Google Live setupComplete...", call_id=self._call_id)
            await asyncio.wait_for(self._setup_ack_event.wait(), timeout=5.0)
            logger.info("Google Live setup complete (ACK received)", call_id=self._call_id)
        
            # Note: Greeting is sent by _handle_setup_complete() to avoid race condition
            # Do NOT send greeting here as it would duplicate the greeting
            
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(),
                name=f"google-live-keepalive-{call_id}",
            )

            logger.info(
                "Google Live session started",
                call_id=call_id,
            )

        except Exception as e:
            logger.error(
                "Failed to start Google Live session",
                call_id=call_id,
                error=str(e),
                exc_info=True,
            )
            await self.stop_session()
            raise

    async def _send_setup(self, context: Optional[Dict[str, Any]]) -> None:
        """Send session setup message to Gemini Live API."""
        # Use instructions from config (like OpenAI Realtime pattern)
        system_prompt = self.config.instructions
        
        # Build generation config from configurable parameters
        # https://gist.github.com/quartzjer/9636066e96b4f904162df706210770e4
        generation_config = {
            "responseModalities": self.config.response_modalities,  # Configurable: "audio", "text", or "audio_text"
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": self.config.tts_voice_name or "Aoede"
                    }
                }
            },
            # LLM generation parameters (all configurable via YAML)
            "temperature": self.config.llm_temperature,
            "maxOutputTokens": self.config.llm_max_output_tokens,
            "topP": self.config.llm_top_p,
            "topK": self.config.llm_top_k,
        }

        # Detailed debug logging for speech configuration
        speech_cfg = generation_config.get("speechConfig", {})
        voice_cfg = speech_cfg.get("voiceConfig", {}).get("prebuiltVoiceConfig", {})
        logger.debug(
            "Google Live speech configuration",
            call_id=self._call_id,
            voice_name=voice_cfg.get("voiceName"),
            response_modalities=generation_config.get("responseModalities"),
        )

        # Build tools config (aligned with Deepgram/OpenAI pattern)
        # Always initialize tool adapter and get all registered tools
        tools = []
        try:
            self._tool_adapter = GoogleToolAdapter(tool_registry)
            tools = self._tool_adapter.get_tools_config()
            if tools:
                logger.debug(
                    "Google Live tools configured",
                    call_id=self._call_id,
                    tool_count=len(tools[0].get("functionDeclarations", [])) if tools else 0
                )
        except Exception as e:
            logger.warning(f"Failed to configure tools: {e}", call_id=self._call_id, exc_info=True)

        # Setup message
        setup_msg = {
            "setup": {
                "model": f"models/{self.config.llm_model}",
                "generation_config": generation_config,
            }
        }

        if system_prompt:
            setup_msg["setup"]["system_instruction"] = {
                "parts": [{"text": system_prompt}]
            }

        if tools:
            setup_msg["setup"]["tools"] = tools
        
        # Enable transcriptions for conversation history tracking (configurable)
        # This allows us to populate email summaries and transcripts
        # Note: Using camelCase per Google Live API format
        # Use empty object {} to enable with default settings (no "model" field - API doesn't support it)
        if self.config.enable_input_transcription:
            setup_msg["setup"]["inputAudioTranscription"] = {}
        
        if self.config.enable_output_transcription:
            setup_msg["setup"]["outputAudioTranscription"] = {}
        
        # CRITICAL: Enable automatic Voice Activity Detection
        # This is required for Google Live to detect when user is speaking
        # Without this, VAD may not work correctly with AudioSocket transport
        setup_msg["setup"]["realtimeInputConfig"] = {
            "automaticActivityDetection": {
                "disabled": False  # Explicitly enable VAD
            }
            # NOTE: languageHints is NOT supported by Google Live API
            # Attempted to add for speech recognition quality but API rejects it
            # Error: "Unknown name 'languageHints' at 'setup.realtime_input_config'"
            # Language detection must be handled via system prompt instead
        }

        # Debug: Log setup message structure
        logger.debug(
            "Sending Google Live setup message",
            call_id=self._call_id,
            setup_keys=list(setup_msg.get("setup", {}).keys()),
            model=setup_msg.get("setup", {}).get("model"),
            has_system_instruction=bool(system_prompt),
            tools_count=len(tools),
            generation_config=generation_config,
        )

        await self._send_message(setup_msg)
        
        logger.info(
            "Sent Google Live setup",
            call_id=self._call_id,
            has_system_prompt=bool(system_prompt),
            tools_count=len(tools),
        )

    async def _send_message(self, message: Dict[str, Any]) -> None:
        """Send a message to Google Live API."""
        if not self.websocket:
            logger.warning("No websocket connection", call_id=self._call_id)
            return

        async with self._send_lock:
            try:
                await self.websocket.send(json.dumps(message))
            except Exception as e:
                logger.error(
                    "Failed to send message to Google Live",
                    call_id=self._call_id,
                    error=str(e),
                )

    async def _send_greeting(self) -> None:
        """Send greeting by asking Gemini to speak it (validated pattern from Golden Baseline)."""
        greeting = (self.config.greeting or "").strip()
        if not greeting:
            return
        
        logger.info("Sending greeting request to Google Live", call_id=self._call_id, greeting_preview=greeting[:50])
        
        # Per Golden Baseline (docs/case-studies/Google-Live-Golden-Baseline.md):
        # Validated approach for ExternalMedia RTP - send user turn requesting greeting
        # This worked successfully in production testing (Nov 14, 2025 - Call 1763092342.5132)
        # 
        # NOTE: This is the VALIDATED pattern, but current AudioSocket implementation
        # is experiencing greeting repetition issue that ExternalMedia RTP did not have.
        # Need to investigate AudioSocket-specific difference.
        greeting_msg = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": f"Please greet the caller with the following message: {greeting}"}]
                    }
                ],
                "turnComplete": True
            }
        }
        await self._send_message(greeting_msg)
        
        logger.info(
            "✅ Greeting request sent to Gemini (Golden Baseline pattern)",
            call_id=self._call_id,
        )

    async def send_audio(self, audio_chunk: bytes, sample_rate: int = 8000, encoding: str = "ulaw") -> None:
        """
        Send audio chunk to Gemini Live API.

        Args:
            audio_chunk: Raw audio bytes (µ-law or PCM16)
            sample_rate: Sample rate of input audio (default from config)
            encoding: Audio encoding (ulaw/linear16/pcm16)
        """
        if not self.websocket or not self._setup_complete:
            return

        try:
            # DIAGNOSTIC: Track frame reception for fragmentation analysis
            if not hasattr(self, '_audio_frame_count'):
                self._audio_frame_count = 0
                self._audio_bytes_received = 0
                self._last_frame_time = time.time()
                self._frame_gaps = []
            
            self._audio_frame_count += 1
            self._audio_bytes_received += len(audio_chunk)
            
            # Measure inter-frame timing
            current_time = time.time()
            if self._last_frame_time:
                gap_ms = (current_time - self._last_frame_time) * 1000
                self._frame_gaps.append(gap_ms)
                
                # Log irregular gaps (should be ~20ms for continuous audio)
                if gap_ms > 50 or gap_ms < 10:
                    logger.warning(
                        "⚠️ IRREGULAR FRAME GAP",
                        call_id=self._call_id,
                        gap_ms=f"{gap_ms:.1f}",
                        expected="~20ms",
                        frame_num=self._audio_frame_count,
                    )
            self._last_frame_time = current_time
            
            # Periodic frame stats
            if self._audio_frame_count % 50 == 0:
                avg_gap = sum(self._frame_gaps[-50:]) / min(50, len(self._frame_gaps)) if self._frame_gaps else 0
                logger.info(
                    "📊 AUDIO FRAME STATS",
                    call_id=self._call_id,
                    frames_received=self._audio_frame_count,
                    total_bytes=self._audio_bytes_received,
                    avg_gap_ms=f"{avg_gap:.1f}",
                    sample_rate_in=sample_rate,
                    encoding_in=encoding,
                )
            
            # Infer format from chunk size if not specified
            if encoding == "ulaw" or (sample_rate == 8000 and len(audio_chunk) == 160):
                # μ-law to PCM16
                pcm16_src = mulaw_to_pcm16le(audio_chunk)
                src_rate = sample_rate
            else:
                # Already PCM16
                pcm16_src = audio_chunk
                src_rate = sample_rate

            # Resample to provider's input rate (16kHz for Gemini Live)
            provider_rate = self.config.provider_input_sample_rate_hz
            if src_rate != provider_rate:
                pcm16_provider, _ = resample_audio(
                    pcm16_src,
                    source_rate=src_rate,
                    target_rate=provider_rate,
                )
            else:
                pcm16_provider = pcm16_src

            # VAD-based burst accumulation: Detect speech and send as bursts
            # This matches Google Live's output pattern and improves recognition
            import audioop
            chunk_rms = audioop.rms(pcm16_provider, 2)
            is_speech = chunk_rms > self._speech_threshold_rms
            
            # Log VAD analysis for debugging
            logger.debug(
                "VAD frame analysis",
                call_id=self._call_id,
                chunk_rms=chunk_rms,
                threshold=self._speech_threshold_rms,
                is_speech=is_speech,
                speech_active=self._is_speech_active,
                buffer_frames=len(self._speech_buffer) // 640,
                silence_frames=self._silence_frames,
            )
            
            if is_speech:
                # Speech detected - accumulate
                self._speech_buffer.extend(pcm16_provider)
                self._speech_frames += 1
                self._silence_frames = 0
                
                if not self._is_speech_active and self._speech_frames >= self._min_speech_frames:
                    # Speech session started
                    self._is_speech_active = True
                    logger.info(
                        "🎤 SPEECH START - Beginning burst accumulation",
                        call_id=self._call_id,
                        rms=chunk_rms,
                        buffer_size=len(self._speech_buffer),
                    )
            else:
                # Silence detected
                if self._is_speech_active:
                    self._silence_frames += 1
                    
                    # Include trailing silence in burst (natural speech boundary)
                    if self._silence_frames < self._silence_threshold:
                        self._speech_buffer.extend(pcm16_provider)
                    
                    # End of utterance detected
                    if self._silence_frames >= self._silence_threshold:
                        if len(self._speech_buffer) > 0:
                            # Send accumulated speech as single burst
                            burst_to_send = bytes(self._speech_buffer)
                            burst_rms = audioop.rms(burst_to_send, 2)
                            
                            logger.info(
                                "📤 SPEECH BURST - Sending accumulated audio",
                                call_id=self._call_id,
                                burst_bytes=len(burst_to_send),
                                burst_duration_ms=int(len(burst_to_send) / (provider_rate * 2) * 1000),
                                burst_rms=burst_rms,
                                frames_accumulated=self._speech_frames,
                            )
                            
                            # Encode entire burst as base64
                            audio_b64 = base64.b64encode(burst_to_send).decode("utf-8")
                            
                            # Send realtime input burst
                            message = {
                                "realtimeInput": {
                                    "mediaChunks": [
                                        {
                                            "mimeType": f"audio/pcm;rate={provider_rate}",
                                            "data": audio_b64,
                                        }
                                    ]
                                }
                            }
                            
                            await self._send_message(message)
                            _GOOGLE_LIVE_AUDIO_SENT.labels(call_id=self._call_id).inc(len(burst_to_send))
                            
                            # Reset for next utterance
                            self._speech_buffer.clear()
                        
                        self._is_speech_active = False
                        self._speech_frames = 0
                        self._silence_frames = 0
                else:
                    # Pure silence - don't accumulate
                    self._speech_frames = 0

        except Exception as e:
            logger.error(
                "Error sending audio to Google Live",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _receive_loop(self) -> None:
        """Continuously receive and process messages from Gemini Live API."""
        if not self.websocket:
            return

        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_server_message(data)
                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to decode Google Live message",
                        call_id=self._call_id,
                        error=str(e),
                    )
                except Exception as e:
                    logger.error(
                        "Error handling Google Live message",
                        call_id=self._call_id,
                        error=str(e),
                        exc_info=True,
                    )
        except (ConnectionClosedError, ConnectionClosedOK) as e:
            # Enhanced logging for WebSocket close
            close_reason = e.reason if hasattr(e, 'reason') else "No reason provided"
            close_code = e.code if hasattr(e, 'code') else None
            
            # Decode close code meaning
            close_code_meanings = {
                1000: "Normal closure",
                1001: "Going away",
                1002: "Protocol error",
                1003: "Unsupported data",
                1006: "Abnormal closure (no close frame)",
                1007: "Invalid frame payload data",
                1008: "Policy violation (likely auth/permission issue)",
                1009: "Message too big",
                1010: "Mandatory extension missing",
                1011: "Internal server error",
            }
            close_meaning = close_code_meanings.get(close_code, "Unknown")
            
            logger.warning(
                "Google Live WebSocket closed",
                call_id=self._call_id,
                code=close_code,
                meaning=close_meaning,
                reason=close_reason,
            )
            
            # Specific guidance for common errors
            if close_code == 1008:
                logger.error(
                    "Policy violation (1008) - Check API key permissions and Gemini Live API access",
                    call_id=self._call_id,
                    hint="Verify: 1) GOOGLE_API_KEY is correct 2) Gemini API is enabled 3) API key has generativelanguage.liveapi.user role",
                )
        except Exception as e:
            logger.error(
                "Google Live receive loop error",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_server_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming message from Gemini Live API."""
        message_type = None
        
        # Determine message type
        if "setupComplete" in data:
            message_type = "setupComplete"
        elif "serverContent" in data:
            message_type = "serverContent"
        elif "toolCall" in data:
            message_type = "toolCall"
        elif "toolCallCancellation" in data:
            message_type = "toolCallCancellation"
        elif "inputTranscription" in data:
            message_type = "inputTranscription"
        elif "outputTranscription" in data:
            message_type = "outputTranscription"
        elif "goAway" in data:
            message_type = "goAway"

        logger.debug(
            "Received Google Live message",
            call_id=self._call_id,
            message_type=message_type,
        )

        # Handle by type
        if message_type == "setupComplete":
            await self._handle_setup_complete(data)
        elif message_type == "serverContent":
            await self._handle_server_content(data)
        elif message_type == "toolCall":
            await self._handle_tool_call(data)
        elif message_type == "goAway":
            await self._handle_go_away(data)
        # Note: inputTranscription and outputTranscription are NOT separate message types
        # They are fields within serverContent and are handled in _handle_server_content()

    async def _handle_setup_complete(self, data: Dict[str, Any]) -> None:
        """Handle setupComplete message."""
        self._setup_complete = True
        
        # Unblock audio streaming (ACK pattern like Deepgram)
        if self._setup_ack_event:
            self._setup_ack_event.set()
        
        logger.info(
            "Google Live setup complete",
            call_id=self._call_id,
        )

        # Play greeting if configured
        if self.config.greeting:
            await self._send_greeting()

    async def _handle_server_content(self, data: Dict[str, Any]) -> None:
        """Handle serverContent message (audio, text, etc.)."""
        content = data.get("serverContent", {})
        
        # Handle input transcription (user speech) - per official API docs
        # CONFIRMED BY TESTING: API sends INCREMENTAL fragments, not cumulative updates
        # Despite documentation suggesting cumulative behavior, actual API sends:
        # " What" -> " is" -> " the" -> " la" -> "ten" -> "cy" -> " for" -> " this project."
        # We must CONCATENATE all fragments until turnComplete
        input_transcription = content.get("inputTranscription")
        if input_transcription:
            text = input_transcription.get("text", "")
            if text:
                # Concatenate fragments (not replace!)
                self._input_transcription_buffer += text
                logger.debug(
                    "Google Live input transcription fragment",
                    call_id=self._call_id,
                    fragment=text,
                    buffer_length=len(self._input_transcription_buffer),
                )
        
        # Handle output transcription (AI speech) - per official API docs
        # Like inputTranscription, API sends incremental fragments that must be concatenated
        output_transcription = content.get("outputTranscription")
        if output_transcription:
            text = output_transcription.get("text", "")
            if text:
                # Concatenate AI speech fragments
                self._output_transcription_buffer += text
                logger.debug(
                    "Google Live output transcription fragment",
                    call_id=self._call_id,
                    fragment=text,
                    buffer_length=len(self._output_transcription_buffer),
                )
        
        # Check if model turn is complete - THIS is when we save the final transcription
        turn_complete = content.get("turnComplete", False)
        
        # Save final transcriptions when turn completes (per API recommendation)
        if turn_complete:
            # Save user speech if buffered
            if self._input_transcription_buffer:
                logger.info(
                    "Google Live final user transcription (turnComplete)",
                    call_id=self._call_id,
                    text=self._input_transcription_buffer[:150],
                )
                await self._track_conversation_message("user", self._input_transcription_buffer)
                self._input_transcription_buffer = ""
            
            # Save AI speech if buffered
            if self._output_transcription_buffer:
                logger.info(
                    "Google Live final AI transcription (turnComplete)",
                    call_id=self._call_id,
                    text=self._output_transcription_buffer[:150],
                )
                await self._track_conversation_message("assistant", self._output_transcription_buffer)
                self._output_transcription_buffer = ""
        
        # Extract parts (using camelCase keys from actual API)
        for part in content.get("modelTurn", {}).get("parts", []):
            # Handle audio output
            if "inlineData" in part:
                inline_data = part["inlineData"]
                if inline_data.get("mimeType", "").startswith("audio/pcm"):
                    await self._handle_audio_output(inline_data["data"])
            
            # Handle text output (for debugging/logging only)
            # Note: We now get cleaner AI transcriptions from outputTranscription field
            if "text" in part:
                text = part["text"]
                logger.debug(
                    "Google Live text response from modelTurn (not saved - using outputTranscription instead)",
                    call_id=self._call_id,
                    text_preview=text[:100],
                )

        # Handle turn completion
        if turn_complete:
            await self._handle_turn_complete()

    async def _handle_audio_output(self, audio_b64: str) -> None:
        """
        Handle audio output from Gemini.

        Args:
            audio_b64: Base64-encoded PCM16 audio (at output_sample_rate_hz from config)
        """
        try:
            # Decode base64
            pcm16_provider = base64.b64decode(audio_b64)
            
            _GOOGLE_LIVE_AUDIO_RECEIVED.labels(call_id=self._call_id).inc(len(pcm16_provider))

            # Resample from provider output rate to target wire rate (from config)
            provider_output_rate = self.config.output_sample_rate_hz
            target_rate = self.config.target_sample_rate_hz
            
            if provider_output_rate != target_rate:
                pcm16_target, _ = resample_audio(
                    pcm16_provider,
                    source_rate=provider_output_rate,
                    target_rate=target_rate,
                )
            else:
                pcm16_target = pcm16_provider

            # Convert to target format (from config: ulaw/linear16/pcm16)
            target_encoding = self.config.target_encoding.lower()
            if target_encoding in ("ulaw", "mulaw", "g711_ulaw"):
                output_audio = convert_pcm16le_to_target_format(pcm16_target, "mulaw")
            else:
                # PCM16/linear16 - no conversion needed
                output_audio = pcm16_target

            # Emit audio event (matching OpenAI Realtime pattern)
            if not self._in_audio_burst:
                self._in_audio_burst = True
            
            if self.on_event:
                await self.on_event(
                    {
                        "type": "AgentAudio",
                        "data": output_audio,
                        "call_id": self._call_id,
                        "encoding": target_encoding,  # Tell engine what format we're sending
                        "sample_rate": target_rate,  # Tell engine what rate we're sending
                    }
                )

        except Exception as e:
            logger.error(
                "Error handling Google Live audio output",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_turn_complete(self) -> None:
        """Handle turn completion."""
        had_audio = self._in_audio_burst
        
        # Note: Transcription is now saved in _handle_server_content when turnComplete=true
        # No need to flush here - it's already been handled
        
        if self._in_audio_burst:
            self._in_audio_burst = False
            if self.on_event:
                await self.on_event(
                    {
                        "type": "AgentAudioDone",
                        "call_id": self._call_id,
                        "streaming_done": True,
                    }
                )

        # Mark greeting as complete after first turn
        if not self._greeting_completed:
            self._greeting_completed = True
            logger.info(
                "Google Live greeting completed",
                call_id=self._call_id,
            )
        
        # Handle hangup if requested after this turn
        if self._hangup_after_response:
            logger.info(
                "🔚 Farewell response completed - triggering hangup",
                call_id=self._call_id,
            )
            
            # Emit HangupReady event to trigger hangup in engine
            try:
                if self.on_event:
                    await self.on_event({
                        "type": "HangupReady",
                        "call_id": self._call_id,
                        "reason": "farewell_completed",
                        "had_audio": had_audio
                    })
            except Exception as e:
                logger.error(
                    "Failed to emit HangupReady event",
                    call_id=self._call_id,
                    error=str(e)
                )
            
            # Reset hangup flag
            self._hangup_after_response = False

    async def _handle_tool_call(self, data: Dict[str, Any]) -> None:
        """Handle toolCall message."""
        tool_call = data.get("toolCall", {})
        
        if not self._tool_adapter:
            logger.warning(
                "Received tool call but no tool adapter configured",
                call_id=self._call_id,
            )
            return

        try:
            # Extract function call details (camelCase per official API)
            function_calls = tool_call.get("functionCalls", [])
            
            for func_call in function_calls:
                func_name = func_call.get("name")
                func_args = func_call.get("args", {})
                call_id = func_call.get("id")

                logger.info(
                    "Google Live tool call",
                    call_id=self._call_id,
                    function=func_name,
                    tool_call_id=call_id,
                )

                # Build tool execution context
                from src.tools.context import ToolExecutionContext
                tool_context = ToolExecutionContext(
                    call_id=self._call_id,
                    caller_channel_id=getattr(self, '_caller_channel_id', None),
                    bridge_id=getattr(self, '_bridge_id', None),
                    session_store=getattr(self, '_session_store', None),
                    ari_client=getattr(self, '_ari_client', None),
                    config=getattr(self, '_full_config', None),
                    provider_name="google_live",
                )

                # Execute tool
                result = await self._tool_adapter.execute_tool(
                    func_name,
                    func_args,
                    tool_context,
                )

                # Check for hangup intent (like OpenAI Realtime pattern)
                if func_name == "hangup_call" and result:
                    if result.get("will_hangup"):
                        self._hangup_after_response = True
                        logger.info(
                            "🔚 Hangup tool executed - next response will trigger hangup",
                            call_id=self._call_id
                        )

                # Send tool response (camelCase per official API)
                tool_response = {
                    "toolResponse": {
                        "functionResponses": [
                            {
                                "id": call_id,
                                "name": func_name,
                                "response": result,
                            }
                        ]
                    }
                }
                await self._send_message(tool_response)

                logger.info(
                    "Sent Google Live tool response",
                    call_id=self._call_id,
                    function=func_name,
                )

        except Exception as e:
            logger.error(
                "Error handling Google Live tool call",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _track_conversation_message(self, role: str, text: str) -> None:
        """
        Track conversation message to session history for transcripts.
        
        Similar to OpenAI Realtime pattern - saves messages to session.conversation_history
        for email summary/transcript tools.
        
        Args:
            role: "user" or "assistant"
            text: Message content
        """
        if not text or not text.strip():
            return
        
        # Get session_store from provider context (injected by engine)
        session_store = getattr(self, '_session_store', None)
        if not session_store:
            logger.debug(
                "No session_store available for conversation tracking",
                call_id=self._call_id,
                role=role
            )
            return
        
        try:
            session = await session_store.get_by_call_id(self._call_id)
            if session:
                # Add to conversation history
                session.conversation_history.append({
                    "role": role,  # "user" or "assistant"
                    "content": text,
                    "timestamp": time.time()
                })
                # Update session
                await session_store.upsert_call(session)
                logger.debug(
                    "✅ Tracked conversation message",
                    call_id=self._call_id,
                    role=role,
                    text_preview=text[:50] + "..." if len(text) > 50 else text
                )
        except Exception as e:
            logger.warning(
                f"Failed to track conversation message: {e}",
                call_id=self._call_id,
                role=role,
                exc_info=True
            )

    async def _handle_go_away(self, data: Dict[str, Any]) -> None:
        """Handle goAway message (server disconnect warning)."""
        logger.warning(
            "Google Live server sending goAway",
            call_id=self._call_id,
        )
        # Prepare for reconnection if needed

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive messages."""
        while self.websocket and not self.websocket.closed:
            try:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SEC)
                # Send empty realtime input as keepalive (camelCase)
                if self._setup_complete:
                    await self._send_message({"realtimeInput": {}})
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Keepalive error",
                    call_id=self._call_id,
                    error=str(e),
                )

    async def cancel_response(self) -> None:
        """
        Cancel the current response (barge-in).
        
        Note: Gemini Live API supports interruption natively via VAD.
        When user starts speaking, the model automatically stops generating.
        """
        # Gemini handles this automatically, but we can log it
        logger.info(
            "Barge-in detected (handled by Gemini VAD)",
            call_id=self._call_id,
        )

    async def stop_session(self) -> None:
        """Stop the Google Live session and cleanup resources."""
        if not self._call_id:
            return

        logger.info(
            "Stopping Google Live session",
            call_id=self._call_id,
        )

        # Cancel background tasks
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task

        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task

        # Close WebSocket
        if self.websocket and not self.websocket.closed:
            await self.websocket.close()
            _GOOGLE_LIVE_SESSIONS.dec()

        # Clear state
        self._call_id = None
        self._session_id = None
        self._setup_complete = False
        self._speech_buffer.clear()  # Clear VAD burst buffer
        self._conversation_history.clear()

        if self._session_start_time:
            duration = time.time() - self._session_start_time
            logger.info(
                "Google Live session ended",
                duration_seconds=round(duration, 2),
            )

        logger.info("Google Live session stopped")
