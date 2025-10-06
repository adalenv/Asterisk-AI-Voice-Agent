"""
StreamingPlaybackManager - Handles streaming audio playback via AudioSocket/ExternalMedia.

This module provides streaming audio playback capabilities that send audio chunks
directly over the AudioSocket connection instead of using file-based playback.
It includes automatic fallback to file playback on errors or timeouts.
"""

import asyncio
import time
from typing import Optional, Dict, Any, TYPE_CHECKING, Set
import structlog
from prometheus_client import Counter, Gauge
import math
import audioop

from src.core.session_store import SessionStore
from src.core.models import CallSession, PlaybackRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.conversation_coordinator import ConversationCoordinator
    from src.core.playback_manager import PlaybackManager

logger = structlog.get_logger(__name__)

# Prometheus metrics for streaming playback (module-scope, registered once)
_STREAMING_ACTIVE_GAUGE = Gauge(
    "ai_agent_streaming_active",
    "Whether streaming playback is active for a call (1 = active)",
    labelnames=("call_id",),
)
_STREAMING_BYTES_TOTAL = Counter(
    "ai_agent_streaming_bytes_total",
    "Total bytes queued to streaming playback (pre-conversion)",
    labelnames=("call_id",),
)
_STREAMING_FALLBACKS_TOTAL = Counter(
    "ai_agent_streaming_fallbacks_total",
    "Number of times streaming fell back to file playback",
    labelnames=("call_id",),
)
_STREAMING_JITTER_DEPTH = Gauge(
    "ai_agent_streaming_jitter_buffer_depth",
    "Current jitter buffer depth in queued chunks",
    labelnames=("call_id",),
)
_STREAMING_LAST_CHUNK_AGE = Gauge(
    "ai_agent_streaming_last_chunk_age_seconds",
    "Seconds since last streaming chunk was received",
    labelnames=("call_id",),
)
_STREAMING_KEEPALIVES_SENT_TOTAL = Counter(
    "ai_agent_streaming_keepalives_sent_total",
    "Count of keepalive ticks sent while streaming",
    labelnames=("call_id",),
)
_STREAMING_KEEPALIVE_TIMEOUTS_TOTAL = Counter(
    "ai_agent_streaming_keepalive_timeouts_total",
    "Count of keepalive-detected streaming timeouts",
    labelnames=("call_id",),
)
_STREAM_TX_BYTES = Counter(
    "ai_agent_stream_tx_bytes_total",
    "Outbound audio bytes sent to caller (per call)",
    labelnames=("call_id",),
)


class StreamingPlaybackManager:
    """
    Manages streaming audio playback with automatic fallback to file playback.
    
    Responsibilities:
    - Stream audio chunks directly over AudioSocket/ExternalMedia
    - Handle jitter buffering and timing
    - Implement automatic fallback to file playback
    - Manage streaming state and cleanup
    - Coordinate with ConversationCoordinator for gating
    """
    
    def __init__(
        self,
        session_store: SessionStore,
        ari_client,
        conversation_coordinator: Optional["ConversationCoordinator"] = None,
        fallback_playback_manager: Optional["PlaybackManager"] = None,
        streaming_config: Optional[Dict[str, Any]] = None,
        audio_transport: str = "externalmedia",
        rtp_server: Optional[Any] = None,
        audiosocket_server: Optional[Any] = None,
    ):
        self.session_store = session_store
        self.ari_client = ari_client
        self.conversation_coordinator = conversation_coordinator
        self.fallback_playback_manager = fallback_playback_manager
        self.streaming_config = streaming_config or {}
        self.audio_transport = audio_transport
        self.rtp_server = rtp_server
        self.audiosocket_server = audiosocket_server
        self.audiosocket_format: str = "ulaw"  # default format expected by dialplan
        # Debug: when True, send frames to all AudioSocket conns for the call
        self.audiosocket_broadcast_debug: bool = bool(self.streaming_config.get('audiosocket_broadcast_debug', False))
        
        # Streaming state
        self.active_streams: Dict[str, Dict[str, Any]] = {}  # call_id -> stream_info
        self.jitter_buffers: Dict[str, asyncio.Queue] = {}  # call_id -> audio_queue
        self.keepalive_tasks: Dict[str, asyncio.Task] = {}  # call_id -> keepalive_task
        # Per-call remainder buffer for precise frame sizing
        self.frame_remainders: Dict[str, bytes] = {}
        # First outbound frame logged tracker
        self._first_send_logged: Set[str] = set()
        # Startup gating to allow jitter buffers to fill before playback begins
        self._startup_ready: Dict[str, bool] = {}
        
        # Configuration defaults
        self.sample_rate = self.streaming_config.get('sample_rate', 8000)
        self.jitter_buffer_ms = self.streaming_config.get('jitter_buffer_ms', 50)
        self.keepalive_interval_ms = self.streaming_config.get('keepalive_interval_ms', 5000)
        self.connection_timeout_ms = self.streaming_config.get('connection_timeout_ms', 10000)
        self.fallback_timeout_ms = self.streaming_config.get('fallback_timeout_ms', 4000)
        self.chunk_size_ms = self.streaming_config.get('chunk_size_ms', 20)
        # Derived configuration (chunk counts)
        self.min_start_ms = max(0, int(self.streaming_config.get('min_start_ms', 120)))
        self.low_watermark_ms = max(0, int(self.streaming_config.get('low_watermark_ms', 80)))
        self.provider_grace_ms = max(0, int(self.streaming_config.get('provider_grace_ms', 500)))
        self.min_start_chunks = max(1, int(math.ceil(self.min_start_ms / max(1, self.chunk_size_ms))))
        self.low_watermark_chunks = max(0, int(math.ceil(self.low_watermark_ms / max(1, self.chunk_size_ms))))
        # Logging verbosity override
        self.logging_level = (self.streaming_config.get('logging_level') or "info").lower()
        if self.logging_level == "debug":
            logger.debug("Streaming playback logging level set to DEBUG")
        elif self.logging_level == "warning":
            logger.warning("Streaming playback logging level set to WARNING")
        elif self.logging_level not in ("info", "debug", "warning"):
            logger.info("Streaming playback logging level", value=self.logging_level)
        
        logger.info("StreamingPlaybackManager initialized",
                   sample_rate=self.sample_rate,
                   jitter_buffer_ms=self.jitter_buffer_ms)
    
    async def start_streaming_playback(
        self, 
        call_id: str, 
        audio_chunks: asyncio.Queue,
        playback_type: str = "response"
    ) -> Optional[str]:
        """
        Start streaming audio playback for a call.
        
        Args:
            call_id: Canonical call ID
            audio_chunks: Queue of audio chunks to stream
            playback_type: Type of playback (greeting, response, etc.)
        
        Returns:
            stream_id if successful, None if failed
        """
        try:
            # Reuse active stream if one already exists
            if self.is_stream_active(call_id):
                existing = self.active_streams[call_id]['stream_id']
                logger.debug("Streaming already active for call", call_id=call_id, stream_id=existing)
                return existing

            # Get session to determine target channel
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.error("Cannot start streaming - call session not found",
                           call_id=call_id)
                return None
            
            # Generate stream ID
            stream_id = self._generate_stream_id(call_id, playback_type)
            
            # Initialize jitter buffer sized from config
            try:
                chunk_ms = max(1, int(self.chunk_size_ms))
                jb_ms = max(0, int(self.jitter_buffer_ms))
                jb_chunks = max(1, int(math.ceil(jb_ms / chunk_ms)))
            except Exception:
                jb_chunks = 10
            jitter_buffer = asyncio.Queue(maxsize=jb_chunks)
            self.jitter_buffers[call_id] = jitter_buffer
            # Mark streaming active in metrics and session
            _STREAMING_ACTIVE_GAUGE.labels(call_id).set(1)
            if session:
                session.streaming_started = True
                session.current_stream_id = stream_id
                await self.session_store.upsert_call(session)
            
            # Set TTS gating before starting stream
            gating_success = True
            if self.conversation_coordinator:
                gating_success = await self.conversation_coordinator.on_tts_start(call_id, stream_id)
            else:
                gating_success = await self.session_store.set_gating_token(call_id, stream_id)

            if not gating_success:
                logger.error("Failed to start streaming gating",
                           call_id=call_id,
                           stream_id=stream_id)
                return None
            
            # Start streaming task
            streaming_task = asyncio.create_task(
                self._stream_audio_loop(call_id, stream_id, audio_chunks, jitter_buffer)
            )
            
            # Start keepalive task
            keepalive_task = asyncio.create_task(
                self._keepalive_loop(call_id, stream_id)
            )
            self.keepalive_tasks[call_id] = keepalive_task
            
            # Store stream info
            self.active_streams[call_id] = {
                'stream_id': stream_id,
                'playback_type': playback_type,
                'streaming_task': streaming_task,
                'keepalive_task': keepalive_task,
                'start_time': time.time(),
                'chunks_sent': 0,
                'last_chunk_time': time.time(),
                'startup_ready': False,
            }
            self._startup_ready[call_id] = False
            
            logger.info("🎵 STREAMING PLAYBACK - Started",
                       call_id=call_id,
                       stream_id=stream_id,
                       playback_type=playback_type)
            
            return stream_id
            
        except Exception as e:
            logger.error("Error starting streaming playback",
                        call_id=call_id,
                        playback_type=playback_type,
                        error=str(e),
                        exc_info=True)
            return None
    
    async def _stream_audio_loop(
        self, 
        call_id: str, 
        stream_id: str, 
        audio_chunks: asyncio.Queue,
        jitter_buffer: asyncio.Queue
    ) -> None:
        """Main streaming loop that processes audio chunks."""
        try:
            fallback_timeout = self.fallback_timeout_ms / 1000.0
            last_send_time = time.time()
            
            while True:
                try:
                    # Wait for audio chunk with timeout
                    chunk = await asyncio.wait_for(
                        audio_chunks.get(), 
                        timeout=fallback_timeout
                    )
                    
                    if chunk is None:  # End of stream signal
                        logger.info("🎵 STREAMING PLAYBACK - End of stream",
                                   call_id=call_id,
                                   stream_id=stream_id)
                        break
                    
                    # Update timing
                    now = time.time()
                    last_send_time = now
                    if call_id in self.active_streams:
                        self.active_streams[call_id]['last_chunk_time'] = now
                        self.active_streams[call_id]['chunks_sent'] += 1
                    # Update metrics and session counters for queued chunk
                    try:
                        _STREAMING_BYTES_TOTAL.labels(call_id).inc(len(chunk))
                        _STREAMING_JITTER_DEPTH.labels(call_id).set(jitter_buffer.qsize())
                        _STREAMING_LAST_CHUNK_AGE.labels(call_id).set(0.0)
                        sess = await self.session_store.get_by_call_id(call_id)
                        if sess:
                            sess.streaming_bytes_sent += len(chunk)
                            sess.streaming_jitter_buffer_depth = jitter_buffer.qsize()
                            await self.session_store.upsert_call(sess)
                    except Exception:
                        logger.debug("Streaming metrics update failed", call_id=call_id)
                    
                    # Add to jitter buffer
                    await jitter_buffer.put(chunk)
                    
                    # Process jitter buffer
                    success = await self._process_jitter_buffer(call_id, stream_id, jitter_buffer)
                    if not success:
                        await self._record_fallback(call_id, "transport-failure")
                        await self._fallback_to_file_playback(call_id, stream_id)
                        break
                    
                except asyncio.TimeoutError:
                    # No audio chunk received within timeout
                    if time.time() - last_send_time > fallback_timeout:
                        logger.warning("🎵 STREAMING PLAYBACK - Timeout, falling back to file playback",
                                     call_id=call_id,
                                     stream_id=stream_id,
                                     timeout=fallback_timeout)
                        await self._record_fallback(call_id, f"timeout>{fallback_timeout}s")
                        await self._fallback_to_file_playback(call_id, stream_id)
                        break
                    continue
                    
        except Exception as e:
            logger.error("Error in streaming audio loop",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e),
                        exc_info=True)
            await self._record_fallback(call_id, str(e))
            await self._fallback_to_file_playback(call_id, stream_id)
        finally:
            await self._cleanup_stream(call_id, stream_id)
    
    async def _process_jitter_buffer(
        self,
        call_id: str,
        stream_id: str,
        jitter_buffer: asyncio.Queue
    ) -> bool:
        """Process audio chunks from jitter buffer."""
        try:
            # Hold playback until jitter buffer has the minimum startup chunks
            ready = self._startup_ready.get(call_id, False)
            if not ready:
                if jitter_buffer.qsize() < self.min_start_chunks:
                    return True
                self._startup_ready[call_id] = True
                if call_id in self.active_streams:
                    self.active_streams[call_id]['startup_ready'] = True
                logger.debug(
                    "Streaming jitter buffer warm-up complete",
                    call_id=call_id,
                    stream_id=stream_id,
                    buffered_chunks=jitter_buffer.qsize(),
                )

            # Process available chunks with pacing to avoid flooding Asterisk
            while not jitter_buffer.empty():
                # Low watermark check: if depth drops below threshold, pause to rebuild buffer
                if self.low_watermark_chunks and jitter_buffer.qsize() < self.low_watermark_chunks and self._startup_ready.get(call_id, False):
                    logger.debug("Streaming jitter buffer low watermark pause",
                                 call_id=call_id,
                                 stream_id=stream_id,
                                 buffered_chunks=jitter_buffer.qsize(),
                                 low_watermark=self.low_watermark_chunks)
                    await asyncio.sleep(self.chunk_size_ms / 1000.0)
                    _STREAMING_JITTER_DEPTH.labels(call_id).set(jitter_buffer.qsize())
                    break
                chunk = jitter_buffer.get_nowait()

                # Convert audio format if needed
                processed_chunk = await self._process_audio_chunk(chunk)
                if not processed_chunk:
                    continue

                if self.audio_transport == "audiosocket":
                    # Segment to fixed 20ms frames and pace sends
                    fmt = (self.audiosocket_format or "ulaw").lower()
                    bytes_per_sample = 1 if fmt in ("ulaw", "mulaw", "mu-law") else 2
                    frame_size = int(self.sample_rate * (self.chunk_size_ms / 1000.0) * bytes_per_sample)
                    if frame_size <= 0:
                        frame_size = 160 if bytes_per_sample == 1 else 320  # 8k@20ms

                    pending = self.frame_remainders.get(call_id, b"") + processed_chunk
                    offset = 0
                    total_len = len(pending)
                    while (total_len - offset) >= frame_size:
                        frame = pending[offset:offset + frame_size]
                        offset += frame_size
                        success = await self._send_audio_chunk(call_id, stream_id, frame)
                        if not success:
                            return False
                        # Pacing: sleep for chunk duration to avoid overrun
                        await asyncio.sleep(self.chunk_size_ms / 1000.0)

                    # Save remainder for next round
                    self.frame_remainders[call_id] = pending[offset:]
                else:
                    # ExternalMedia/RTP path: send as-is (RTP layer handles timing)
                    success = await self._send_audio_chunk(call_id, stream_id, processed_chunk)
                    if not success:
                        return False

        except Exception as e:
            logger.error("Error processing jitter buffer",
                        call_id=call_id,
                        error=str(e))
            return False

        return True
    
    async def _process_audio_chunk(self, chunk: bytes) -> Optional[bytes]:
        """Process audio chunk for streaming.

        - Deepgram emits μ-law 8 kHz frames.
        - For AudioSocket downstream we must send PCM16 8 kHz.
        - For ExternalMedia we pass through μ-law (RTP layer can handle ulaw).
        """
        if not chunk:
            return None
        try:
            if self.audio_transport == "audiosocket":
                fmt = (self.audiosocket_format or "ulaw").lower()
                if fmt in ("slin16", "slinear", "pcm16", "linear16", "slin"):
                    # Convert μ-law (provider) to 16-bit PCM for AudioSocket downstream
                    return audioop.ulaw2lin(chunk, 2)
                # If dialplan expects μ-law, pass through unchanged
                return chunk
            # ExternalMedia/RTP path: pass-through
            return chunk
        except Exception as e:
            logger.error("Audio chunk processing failed", error=str(e), exc_info=True)
            return None
    
    async def _send_audio_chunk(self, call_id: str, stream_id: str, chunk: bytes) -> bool:
        """Send audio chunk via configured streaming transport."""
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.warning("Cannot stream audio - session not found", call_id=call_id)
                return False

            if self.audio_transport == "externalmedia":
                if not self.rtp_server:
                    logger.warning("Streaming transport unavailable (no RTP server)", call_id=call_id)
                    return False

                ssrc = getattr(session, "ssrc", None)
                success = await self.rtp_server.send_audio(call_id, chunk, ssrc=ssrc)
                if not success:
                    logger.warning("RTP streaming send failed", call_id=call_id, stream_id=stream_id)
                else:
                    try:
                        _STREAM_TX_BYTES.labels(call_id).inc(len(chunk))
                    except Exception:
                        pass
                return success

            if self.audio_transport == "audiosocket":
                if not self.audiosocket_server:
                    logger.warning("Streaming transport unavailable (no AudioSocket server)", call_id=call_id)
                    return False
                conn_id = getattr(session, "audiosocket_conn_id", None)
                if not conn_id:
                    logger.warning("Streaming transport missing AudioSocket connection", call_id=call_id)
                    return False
                # One-time debug for first outbound frame to identify codec/format
                if call_id not in self._first_send_logged:
                    fmt = (self.audiosocket_format or "ulaw").lower()
                    logger.info(
                        "🎵 STREAMING OUTBOUND - First frame",
                        call_id=call_id,
                        stream_id=stream_id,
                        transport=self.audio_transport,
                        audiosocket_format=fmt,
                        frame_bytes=len(chunk),
                        sample_rate=self.sample_rate,
                        chunk_size_ms=self.chunk_size_ms,
                        conn_id=conn_id,
                    )
                    self._first_send_logged.add(call_id)
                # Optional broadcast mode for diagnostics
                if self.audiosocket_broadcast_debug:
                    conns = list(set(getattr(session, 'audiosocket_conns', []) or []))
                    sent = 0
                    for cid in conns or [conn_id]:
                        if await self.audiosocket_server.send_audio(cid, chunk):
                            sent += 1
                    if sent == 0:
                        logger.warning("AudioSocket broadcast send failed (no recipients)", call_id=call_id, stream_id=stream_id)
                        return False
                    if len(conns) > 1:
                        logger.debug("AudioSocket broadcast sent", call_id=call_id, stream_id=stream_id, recipients=len(conns))
                    return True
                # Normal single-conn send
                success = await self.audiosocket_server.send_audio(conn_id, chunk)
                if not success:
                    logger.warning("AudioSocket streaming send failed", call_id=call_id, stream_id=stream_id)
                else:
                    try:
                        _STREAM_TX_BYTES.labels(call_id).inc(len(chunk))
                    except Exception:
                        pass
                return success

            logger.warning("Streaming transport not implemented for audio_transport",
                           call_id=call_id,
                           audio_transport=self.audio_transport)
            return False

        except Exception as e:
            logger.error("Error sending streaming audio chunk",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e),
                        exc_info=True)
            return False

    def set_transport(
        self,
        *,
        rtp_server: Optional[Any] = None,
        audiosocket_server: Optional[Any] = None,
        audio_transport: Optional[str] = None,
        audiosocket_format: Optional[str] = None,
    ) -> None:
        """Configure transport dependencies after engine initialization."""
        if rtp_server is not None:
            self.rtp_server = rtp_server
        if audiosocket_server is not None:
            self.audiosocket_server = audiosocket_server
        if audio_transport is not None:
            self.audio_transport = audio_transport
        if audiosocket_format is not None:
            self.audiosocket_format = audiosocket_format

    async def _record_fallback(self, call_id: str, reason: str) -> None:
        """Increment fallback counters and persist the last error."""
        try:
            _STREAMING_FALLBACKS_TOTAL.labels(call_id).inc()
            sess = await self.session_store.get_by_call_id(call_id)
            if sess:
                sess.streaming_fallback_count += 1
                sess.last_streaming_error = reason
                await self.session_store.upsert_call(sess)
        except Exception:
            logger.debug("Failed to record streaming fallback", call_id=call_id, reason=reason, exc_info=True)
    
    async def _fallback_to_file_playback(
        self, 
        call_id: str, 
        stream_id: str
    ) -> None:
        """Fallback to file-based playback when streaming fails."""
        try:
            if not self.fallback_playback_manager:
                logger.error("No fallback playback manager available",
                           call_id=call_id,
                           stream_id=stream_id)
                return
            
            # Get session
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.error("Cannot fallback - session not found",
                           call_id=call_id)
                return
            
            # Collect any remaining audio chunks
            remaining_audio = bytearray()
            if call_id in self.jitter_buffers:
                jitter_buffer = self.jitter_buffers[call_id]
                while not jitter_buffer.empty():
                    chunk = jitter_buffer.get_nowait()
                    if chunk:
                        remaining_audio.extend(chunk)
            
            if remaining_audio:
                mulaw_audio = bytes(remaining_audio)

                # Use fallback playback manager
                fallback_playback_id = await self.fallback_playback_manager.play_audio(
                    call_id,
                    mulaw_audio,
                    "streaming-fallback"
                )
                
                if fallback_playback_id:
                    logger.info("🎵 STREAMING FALLBACK - Switched to file playback",
                               call_id=call_id,
                               stream_id=stream_id,
                               fallback_id=fallback_playback_id)
                else:
                    logger.error("Failed to start fallback file playback",
                               call_id=call_id,
                               stream_id=stream_id)
            
        except Exception as e:
            logger.error("Error in fallback to file playback",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e),
                        exc_info=True)
    
    async def _keepalive_loop(self, call_id: str, stream_id: str) -> None:
        """Keepalive loop to maintain streaming connection."""
        try:
            while call_id in self.active_streams:
                await asyncio.sleep(self.keepalive_interval_ms / 1000.0)
                
                # Check if stream is still active
                if call_id not in self.active_streams:
                    break
                
                # Check for timeout
                stream_info = self.active_streams[call_id]
                time_since_last_chunk = time.time() - stream_info['last_chunk_time']
                _STREAMING_LAST_CHUNK_AGE.labels(call_id).set(max(0.0, time_since_last_chunk))
                _STREAMING_KEEPALIVES_SENT_TOTAL.labels(call_id).inc()
                try:
                    sess = await self.session_store.get_by_call_id(call_id)
                    if sess:
                        sess.streaming_keepalive_sent += 1
                        await self.session_store.upsert_call(sess)
                except Exception:
                    pass
                
                if time_since_last_chunk > (self.connection_timeout_ms / 1000.0):
                    logger.warning("🎵 STREAMING PLAYBACK - Connection timeout",
                                 call_id=call_id,
                                 stream_id=stream_id,
                                 time_since_last_chunk=time_since_last_chunk)
                    _STREAMING_KEEPALIVE_TIMEOUTS_TOTAL.labels(call_id).inc()
                    try:
                        sess = await self.session_store.get_by_call_id(call_id)
                        if sess:
                            sess.streaming_keepalive_timeouts += 1
                            sess.last_streaming_error = f"keepalive-timeout>{time_since_last_chunk:.2f}s"
                            await self.session_store.upsert_call(sess)
                    except Exception:
                        pass
                    await self._fallback_to_file_playback(call_id, stream_id)
                    break
                
                # Send keepalive (placeholder)
                logger.debug("🎵 STREAMING KEEPALIVE - Sending keepalive",
                           call_id=call_id,
                           stream_id=stream_id)
        
        except asyncio.CancelledError:
            logger.debug("Keepalive loop cancelled",
                        call_id=call_id,
                        stream_id=stream_id)
        except Exception as e:
            logger.error("Error in keepalive loop",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e))
    
    async def stop_streaming_playback(self, call_id: str) -> bool:
        """Stop streaming playback for a call."""
        try:
            if call_id not in self.active_streams:
                logger.warning("Cannot stop streaming - no active stream",
                             call_id=call_id)
                return False
            
            stream_info = self.active_streams[call_id]
            stream_id = stream_info['stream_id']
            
            # Cancel streaming task
            if 'streaming_task' in stream_info:
                stream_info['streaming_task'].cancel()
            
            # Cancel keepalive task
            if call_id in self.keepalive_tasks:
                self.keepalive_tasks[call_id].cancel()
                del self.keepalive_tasks[call_id]
            
            # Cleanup
            await self._cleanup_stream(call_id, stream_id)
            
            logger.info("🎵 STREAMING PLAYBACK - Stopped",
                       call_id=call_id,
                       stream_id=stream_id)
            
            return True
            
        except Exception as e:
            logger.error("Error stopping streaming playback",
                        call_id=call_id,
                        error=str(e),
                        exc_info=True)
            return False
    
    async def _cleanup_stream(self, call_id: str, stream_id: str) -> None:
        """Clean up streaming resources."""
        try:
            # Clear TTS gating
            if self.conversation_coordinator:
                await self.conversation_coordinator.on_tts_end(
                    call_id, stream_id, "streaming-ended"
                )
                await self.conversation_coordinator.update_conversation_state(
                    call_id, "listening"
                )
            else:
                await self.session_store.clear_gating_token(call_id, stream_id)
            
            # Remove from active streams
            if call_id in self.active_streams:
                del self.active_streams[call_id]
            
            # Clean up jitter buffer
            if call_id in self.jitter_buffers:
                del self.jitter_buffers[call_id]
            self._startup_ready.pop(call_id, None)
            # Reset metrics
            try:
                _STREAMING_ACTIVE_GAUGE.labels(call_id).set(0)
                _STREAMING_JITTER_DEPTH.labels(call_id).set(0)
                _STREAMING_LAST_CHUNK_AGE.labels(call_id).set(0)
            except Exception:
                pass
            
            # Reset session streaming flags
            try:
                sess = await self.session_store.get_by_call_id(call_id)
                if sess:
                    sess.streaming_started = False
                    sess.current_stream_id = None
                    await self.session_store.upsert_call(sess)
            except Exception:
                pass
            # Clear any remainder buffer, respecting grace period for late provider chunks
            if self.provider_grace_ms:
                await asyncio.sleep(self.provider_grace_ms / 1000.0)
            self.frame_remainders.pop(call_id, None)
            
            # Emit tuning summary for observability
            try:
                sess = await self.session_store.get_by_call_id(call_id)
                if sess:
                    logger.info(
                        "🎛️ STREAMING TUNING SUMMARY",
                        call_id=call_id,
                        stream_id=stream_id,
                        fallback_count=sess.streaming_fallback_count,
                        bytes_sent=sess.streaming_bytes_sent,
                        low_watermark=self.low_watermark_ms,
                        min_start=self.min_start_ms,
                        provider_grace_ms=self.provider_grace_ms,
                    )
            except Exception:
                logger.debug("Streaming tuning summary unavailable", call_id=call_id)
            
            logger.debug("Streaming cleanup completed",
                        call_id=call_id,
                        stream_id=stream_id)
            
        except Exception as e:
            logger.error("Error cleaning up stream",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e))
    
    def _generate_stream_id(self, call_id: str, playback_type: str) -> str:
        """Generate deterministic stream ID."""
        timestamp = int(time.time() * 1000)
        return f"stream:{playback_type}:{call_id}:{timestamp}"
    
    def is_stream_active(self, call_id: str) -> bool:
        """Return True if a streaming playback is active for the call."""
        info = self.active_streams.get(call_id)
        if not info:
            return False
        task = info.get('streaming_task')
        return task is not None and not task.done()

    async def get_active_streams(self) -> Dict[str, Dict[str, Any]]:
        """Get information about active streams."""
        return dict(self.active_streams)
    
    async def cleanup_expired_streams(self, max_age_seconds: float = 300) -> int:
        """Clean up expired streams."""
        current_time = time.time()
        expired_calls = []
        
        for call_id, stream_info in self.active_streams.items():
            age = current_time - stream_info['start_time']
            if age > max_age_seconds:
                expired_calls.append(call_id)
        
        for call_id in expired_calls:
            stream_info = self.active_streams[call_id]
            await self._cleanup_stream(call_id, stream_info['stream_id'])
        
        return len(expired_calls)
