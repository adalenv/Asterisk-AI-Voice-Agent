import asyncio
import json
import audioop
import websockets
from typing import Callable, Optional, List, Dict, Any
import websockets.exceptions

from structlog import get_logger
from prometheus_client import Gauge, Info
from ..audio.resampler import (
    mulaw_to_pcm16le,
    resample_audio,
)
from ..config import LLMConfig
from .base import AIProviderInterface

logger = get_logger(__name__)

_DEEPGRAM_INPUT_RATE = Gauge(
    "ai_agent_deepgram_input_sample_rate_hz",
    "Configured Deepgram input sample rate per call",
    labelnames=("call_id",),
)
_DEEPGRAM_OUTPUT_RATE = Gauge(
    "ai_agent_deepgram_output_sample_rate_hz",
    "Configured Deepgram output sample rate per call",
    labelnames=("call_id",),
)
_DEEPGRAM_SESSION_AUDIO_INFO = Info(
    "ai_agent_deepgram_session_audio",
    "Deepgram session audio encodings/sample rates",
    labelnames=("call_id",),
)

class DeepgramProvider(AIProviderInterface):
    def __init__(self, config: Dict[str, Any], llm_config: LLMConfig, on_event: Callable[[Dict[str, Any]], None]):
        super().__init__(on_event)
        self.config = config
        self.llm_config = llm_config
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._is_audio_flowing = False
        self.request_id: Optional[str] = None
        self.call_id: Optional[str] = None
        self._in_audio_burst: bool = False
        self._first_output_chunk_logged: bool = False
        self._closing: bool = False
        self._closed: bool = False
        # Maintain resample state for smoother conversion
        self._input_resample_state = None
        # Settings/stream readiness
        self._settings_sent: bool = False
        self._ready_to_stream: bool = False
        self._settings_ts: float = 0.0
        self._prestream_queue: list[bytes] = []  # small buffer for early frames
        self._pcm16_accum = bytearray()
        # Settings ACK gating
        self._ack_event: Optional[asyncio.Event] = None
        # Greeting injection guard
        self._greeting_injected: bool = False
        self._greeting_injections: int = 0
        # Cache declared Deepgram input settings
        try:
            self._dg_input_rate = int(getattr(self.config, 'input_sample_rate_hz', 8000) or 8000)
        except Exception:
            self._dg_input_rate = 8000
        # Cache provider output settings for downstream conversion/metadata
        self._dg_output_encoding = (getattr(self.config, 'output_encoding', None) or 'mulaw').lower()
        try:
            self._dg_output_rate = int(getattr(self.config, 'output_sample_rate_hz', 8000) or 8000)
        except Exception:
            self._dg_output_rate = 8000

    @property
    def supported_codecs(self) -> List[str]:
        return ["ulaw"]

    # ------------------------------------------------------------------ #
    # Metrics helpers
    # ------------------------------------------------------------------ #
    def _record_session_audio(
        self,
        *,
        input_encoding: str,
        input_sample_rate_hz: int,
        output_encoding: str,
        output_sample_rate_hz: int,
    ) -> None:
        call_id = self.call_id
        if not call_id:
            return
        try:
            _DEEPGRAM_INPUT_RATE.labels(call_id).set(int(input_sample_rate_hz))
        except Exception:
            pass
        try:
            _DEEPGRAM_OUTPUT_RATE.labels(call_id).set(int(output_sample_rate_hz))
        except Exception:
            pass
        info_payload = {
            "input_encoding": str(input_encoding or ""),
            "input_sample_rate_hz": str(input_sample_rate_hz),
            "output_encoding": str(output_encoding or ""),
            "output_sample_rate_hz": str(output_sample_rate_hz),
        }
        try:
            _DEEPGRAM_SESSION_AUDIO_INFO.labels(call_id).info(info_payload)
        except Exception:
            pass

    def _clear_metrics(self, call_id: Optional[str]) -> None:
        if not call_id:
            return
        for metric in (_DEEPGRAM_INPUT_RATE, _DEEPGRAM_OUTPUT_RATE):
            try:
                metric.remove(call_id)
            except (KeyError, ValueError):
                pass
        try:
            _DEEPGRAM_SESSION_AUDIO_INFO.remove(call_id)
        except (KeyError, ValueError):
            pass

    async def start_session(self, call_id: str):
        ws_url = f"wss://agent.deepgram.com/v1/agent/converse"
        headers = {'Authorization': f'Token {self.config.api_key}'}

        try:
            logger.info("Connecting to Deepgram Voice Agent...", url=ws_url)
            self.websocket = await websockets.connect(ws_url, extra_headers=list(headers.items()))
            logger.info("✅ Successfully connected to Deepgram Voice Agent.")

            # Persist call context for downstream events
            self.call_id = call_id
            # Capture Deepgram request id if provided
            try:
                rid = None
                if hasattr(self.websocket, "response_headers") and self.websocket.response_headers:
                    rid = self.websocket.response_headers.get("x-request-id")
                if rid:
                    self.request_id = rid
                    logger.info("Deepgram request id", call_id=call_id, request_id=rid)
            except Exception:
                logger.debug("Failed to read Deepgram response headers", exc_info=True)

            # Prepare ACK gate and start receiver early to catch server responses
            self._ack_event = asyncio.Event()
            asyncio.create_task(self._receive_loop())

            await self._configure_agent()
            self._keep_alive_task = asyncio.create_task(self._keep_alive())

        except Exception as e:
            logger.error("Failed to connect to Deepgram Voice Agent", exc_info=True)
            if self._keep_alive_task:
                self._keep_alive_task.cancel()
            raise

    async def _configure_agent(self):
        """Builds and sends the V1 Settings message to the Deepgram Voice Agent."""
        # Derive codec settings from config with safe defaults
        input_encoding = getattr(self.config, 'input_encoding', None) or 'linear16'
        input_sample_rate = int(getattr(self.config, 'input_sample_rate_hz', 8000) or 8000)
        output_encoding = getattr(self.config, 'output_encoding', None) or 'mulaw'
        output_sample_rate = int(getattr(self.config, 'output_sample_rate_hz', 8000) or 8000)
        self._dg_output_encoding = output_encoding.lower()
        self._dg_output_rate = output_sample_rate
        # Canonicalize Deepgram V1 audio.format values
        def _canon_fmt(v: str) -> str:
            t = (v or '').strip().lower()
            if t in ('mulaw', 'mu-law', 'g711_ulaw', 'g711ulaw', 'g711-ula', 'ulaw'):
                return 'mulaw'
            if t in ('slin16', 'linear16', 'pcm16'):
                return 'linear16'
            return 'ulaw'
        input_format = _canon_fmt(input_encoding)
        output_format = _canon_fmt(output_encoding)

        # Determine greeting precedence: provider override > global LLM greeting > safe default
        try:
            greeting_val = (getattr(self.config, 'greeting', None) or "").strip()
        except Exception:
            greeting_val = ""
        if not greeting_val:
            try:
                greeting_val = (getattr(self.llm_config, 'initial_greeting', None) or "").strip()
            except Exception:
                greeting_val = ""
        if not greeting_val:
            greeting_val = "Hello, how can I help you today?"

        settings = {
            "type": "Settings",
            "audio": {
                "input": { "encoding": input_format, "sample_rate": int(input_sample_rate) },
                "output": { "encoding": output_format, "sample_rate": int(output_sample_rate) }
            },
            "agent": {
                "greeting": greeting_val,
                "language": "en-US",
                "listen": { "provider": { "type": "deepgram", "model": self.config.model } },
                "think": { "provider": { "type": "open_ai", "model": self.llm_config.model }, "prompt": self.llm_config.prompt },
                "speak": { "provider": { "type": "deepgram", "model": self.config.tts_model } }
            }
        }
        await self.websocket.send(json.dumps(settings))
        # Mark settings sent; readiness only upon server response (ACK) or timeout
        self._settings_sent = True
        try:
            import time as _t
            self._settings_ts = _t.monotonic()
        except Exception:
            self._settings_ts = 0.0
        # Start a fallback timer to avoid indefinite buffering if ACK never arrives
        async def _fallback_ready():
            try:
                await asyncio.sleep(0.3)
                if self.websocket and not self.websocket.closed and not self._ready_to_stream:
                    self._ready_to_stream = True
                    try:
                        logger.warning(
                            "Deepgram settings ACK not received promptly; enabling streaming after fallback delay",
                            call_id=self.call_id,
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        asyncio.create_task(_fallback_ready())

        # Immediately inject greeting once to try to kick off TTS
        async def _inject_greeting_immediate():
            try:
                if self.websocket and not self.websocket.closed and greeting_val and self._greeting_injections < 1:
                    logger.info("Injecting greeting immediately after Settings", call_id=self.call_id)
                    self._greeting_injections += 1
                    try:
                        await self._inject_message_dual(greeting_val)
                    except Exception:
                        logger.debug("Immediate greeting injection failed", exc_info=True)
            except Exception:
                pass
        asyncio.create_task(_inject_greeting_immediate())

        # Wait up to 1.0s for a server response to mark readiness
        try:
            if self._ack_event is not None:
                await asyncio.wait_for(self._ack_event.wait(), timeout=1.0)
                self._ready_to_stream = True
            else:
                logger.debug("ACK gate not initialized; skipping wait")
        except asyncio.TimeoutError:
            logger.warning("Deepgram settings ACK not received within timeout; fallback readiness may be active")
        # If ready and we haven't seen any audio burst within ~1s, inject greeting once to kick off TTS
        async def _inject_greeting_if_quiet():
            try:
                await asyncio.sleep(1.5)
                if self.websocket and not self.websocket.closed and not self._in_audio_burst and greeting_val and self._greeting_injections < 2:
                    logger.info("Injecting greeting via fallback as no AgentAudio detected", call_id=self.call_id)
                    try:
                        self._greeting_injections += 1
                        await self._inject_message_dual(greeting_val)
                    except Exception:
                        logger.debug("Greeting injection failed", exc_info=True)
            except Exception:
                pass
        asyncio.create_task(_inject_greeting_if_quiet())
        summary = {
            "input_encoding": str(input_encoding).lower(),
            "input_sample_rate_hz": int(input_sample_rate),
            "output_encoding": str(output_encoding).lower(),
            "output_sample_rate_hz": int(output_sample_rate),
        }
        self._record_session_audio(**summary)
        logger.info(
            "Deepgram agent configured",
            call_id=self.call_id,
            **summary,
        )

    async def send_audio(self, audio_chunk: bytes):
        """Send caller audio to Deepgram in the declared input format.

        Engine upstream uses AudioSocket with μ-law 8 kHz by default. Convert to
        linear16 at the configured Deepgram input sample rate before sending.
        """
        if self.websocket and audio_chunk:
            try:
                self._is_audio_flowing = True
                chunk_len = len(audio_chunk)
                if chunk_len not in (0, 160, 320):
                    logger.debug(
                        "Deepgram provider unexpected chunk size",
                        bytes=chunk_len,
                    )

                input_encoding = (getattr(self.config, "input_encoding", None) or "linear16").strip().lower()
                target_rate = int(getattr(self.config, "input_sample_rate_hz", 8000) or 8000)
                # Infer actual inbound format and source rate from canonical 20 ms frame sizes
                #  - 160 B ≈ μ-law @ 8 kHz (20 ms)
                #  - 320 B ≈ PCM16 @ 8 kHz (20 ms)
                #  - 640 B ≈ PCM16 @ 16 kHz (20 ms)
                if chunk_len == 160:
                    actual_format = "ulaw"
                    src_rate = 8000
                elif chunk_len == 320:
                    actual_format = "pcm16"
                    src_rate = 8000
                elif chunk_len == 640:
                    actual_format = "pcm16"
                    src_rate = 16000
                else:
                    actual_format = "pcm16" if input_encoding in ("slin16", "linear16", "pcm16") else "ulaw"
                    try:
                        src_rate = int(getattr(self.config, "input_sample_rate_hz", 0) or 0) or (16000 if actual_format == "pcm16" else 8000)
                    except Exception:
                        src_rate = 8000

                payload: bytes = audio_chunk
                pcm_for_rms: Optional[bytes] = None

                if input_encoding in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                    if actual_format == "pcm16":
                        try:
                            payload = audioop.lin2ulaw(audio_chunk, 2)
                        except Exception:
                            logger.warning("Failed to convert PCM to μ-law for Deepgram", exc_info=True)
                            payload = audio_chunk
                    else:
                        payload = audio_chunk

                    pcm_for_rms = mulaw_to_pcm16le(payload)
                    if target_rate and target_rate != 8000:
                        pcm_resampled, self._input_resample_state = resample_audio(
                            pcm_for_rms,
                            8000,
                            target_rate,
                            state=self._input_resample_state,
                        )
                        try:
                            payload = audioop.lin2ulaw(pcm_resampled, 2)
                        except Exception:
                            logger.warning("Failed to convert resampled PCM back to μ-law", exc_info=True)
                            payload = audio_chunk
                        pcm_for_rms = pcm_resampled
                    else:
                        self._input_resample_state = None

                elif input_encoding in ("slin16", "linear16", "pcm16"):
                    # Normalize inbound to PCM16 and resample from detected source rate to target_rate
                    if actual_format == "ulaw":
                        pcm_src = mulaw_to_pcm16le(audio_chunk)
                        src_rate = 8000
                    else:
                        pcm_src = audio_chunk
                    pcm_for_rms = pcm_src
                    if target_rate and target_rate != src_rate:
                        pcm_dst, self._input_resample_state = resample_audio(
                            pcm_src,
                            src_rate,
                            target_rate,
                            state=self._input_resample_state,
                        )
                        payload = pcm_dst
                    else:
                        self._input_resample_state = None
                        payload = pcm_src
                else:
                    logger.warning(
                        "Unsupported Deepgram input_encoding",
                        input_encoding=input_encoding,
                    )
                    payload = audio_chunk
                    pcm_for_rms = None
                    self._input_resample_state = None

                if pcm_for_rms is not None:
                    try:
                        rms = audioop.rms(pcm_for_rms, 2)
                        if rms < 100:
                            logger.warning(
                                "Deepgram provider low RMS detected; possible codec mismatch",
                                rms=rms,
                                input_encoding=input_encoding,
                                bytes=chunk_len,
                            )
                    except Exception:
                        logger.debug("Deepgram RMS check failed", exc_info=True)

                if input_encoding in ("slin16", "linear16", "pcm16"):
                    frame_bytes = (int(target_rate * 0.02) * 2) if target_rate else 640
                    if frame_bytes <= 0:
                        frame_bytes = 640
                    self._pcm16_accum.extend(payload)
                    frames_to_send: list[bytes] = []
                    while len(self._pcm16_accum) >= frame_bytes:
                        frames_to_send.append(bytes(self._pcm16_accum[:frame_bytes]))
                        del self._pcm16_accum[:frame_bytes]

                    if not self._ready_to_stream:
                        try:
                            for fr in frames_to_send:
                                self._prestream_queue.append(fr)
                                if len(self._prestream_queue) > 10:
                                    self._prestream_queue.pop(0)
                        except Exception:
                            pass
                        return

                    if self._prestream_queue:
                        try:
                            for q in self._prestream_queue:
                                await self.websocket.send(q)
                        except Exception:
                            logger.debug("Deepgram prestream flush failed", exc_info=True)
                        finally:
                            self._prestream_queue.clear()

                    for fr in frames_to_send:
                        await self.websocket.send(fr)
                else:
                    if not self._ready_to_stream:
                        try:
                            self._prestream_queue.append(payload)
                            if len(self._prestream_queue) > 10:
                                self._prestream_queue.pop(0)
                        except Exception:
                            pass
                        return

                    if self._prestream_queue:
                        try:
                            for q in self._prestream_queue:
                                await self.websocket.send(q)
                        except Exception:
                            logger.debug("Deepgram prestream flush failed", exc_info=True)
                        finally:
                            self._prestream_queue.clear()

                    await self.websocket.send(payload)
            except websockets.exceptions.ConnectionClosed as e:
                logger.debug("Could not send audio packet: Connection closed.", code=e.code, reason=e.reason)
            except Exception:
                logger.error("An unexpected error occurred while sending audio chunk", exc_info=True)

    async def stop_session(self):
        # Prevent duplicate disconnect logs/ops
        if self._closed or self._closing:
            return
        self._closing = True
        try:
            if self._keep_alive_task:
                self._keep_alive_task.cancel()
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
            if not self._closed:
                logger.info("Disconnected from Deepgram Voice Agent.")
            self._closed = True
        finally:
            self._clear_metrics(self.call_id)
            self.call_id = None
            self._closing = False

    async def _keep_alive(self):
        while True:
            try:
                await asyncio.sleep(10)
                if self.websocket and not self.websocket.closed:
                    if not self._is_audio_flowing:
                        await self.websocket.send(json.dumps({"type": "KeepAlive"}))
                    self._is_audio_flowing = False
                else:
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Error in keep-alive task", exc_info=True)
                break

    def describe_alignment(
        self,
        *,
        audiosocket_format: str,
        streaming_encoding: str,
        streaming_sample_rate: int,
    ) -> List[str]:
        issues: List[str] = []
        cfg_enc = (getattr(self.config, "input_encoding", None) or "").lower()
        try:
            cfg_rate = int(getattr(self.config, "input_sample_rate_hz", 0) or 0)
        except Exception:
            cfg_rate = 0

        if cfg_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
            if cfg_rate and cfg_rate != 8000:
                issues.append(
                    f"Deepgram configuration declares μ-law at {cfg_rate} Hz; μ-law transport must be 8000 Hz."
                )
        if cfg_enc in ("slin16", "linear16", "pcm16") and audiosocket_format != "slin16":
            issues.append(
                f"Deepgram expects PCM16 input but audiosocket.format is {audiosocket_format}. "
                "Set audiosocket.format=slin16 or change deepgram.input_encoding."
            )
        if streaming_encoding not in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
            issues.append(
                f"Streaming manager emits {streaming_encoding} frames but Deepgram output_encoding is μ-law. "
                "Ensure downstream playback converts the provider audio back to μ-law."
            )
        if streaming_sample_rate != 8000:
            issues.append(
                f"Streaming sample rate is {streaming_sample_rate} Hz but Deepgram output_sample_rate is 8000 Hz."
            )
        return issues

    async def _receive_loop(self):
        if not self.websocket:
            return
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    try:
                        event_data = json.loads(message)
                        # Any server message after settings marks stream readiness and ACK
                        self._ready_to_stream = True
                        try:
                            if self._ack_event and not self._ack_event.is_set():
                                self._ack_event.set()
                        except Exception:
                            pass
                        # One-time ACK settings log for effective audio configs (log full payload)
                        try:
                            if getattr(self, "_settings_sent", False) and not getattr(self, "_ack_logged", False):
                                audio_ack = {}
                                if isinstance(event_data, dict):
                                    audio_ack = event_data.get("audio") or {}
                                    # Capture request_id from ACK/Welcome if header was missing
                                    rid = event_data.get("request_id")
                                    if rid and not getattr(self, "request_id", None):
                                        self.request_id = rid
                                        try:
                                            logger.info("Deepgram request id (ack)", call_id=self.call_id, request_id=rid)
                                        except Exception:
                                            pass
                                logger.info(
                                    "Deepgram Agent ACK settings",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                    ack_audio=audio_ack,
                                    event_type=(event_data.get("type") if isinstance(event_data, dict) else None),
                                    ack_raw=event_data,
                                )
                                try:
                                    self._ack_logged = True
                                except Exception:
                                    pass
                        except Exception:
                            logger.debug("Deepgram ACK logging failed", exc_info=True)
                        # Always log control events
                        try:
                            et = event_data.get("type") if isinstance(event_data, dict) else None
                            logger.info(
                                "Deepgram control event",
                                call_id=self.call_id,
                                event_type=et,
                            )
                            if et in ("Error", "Warning"):
                                try:
                                    logger.warning(
                                        "Deepgram control detail",
                                        call_id=self.call_id,
                                        payload=event_data,
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Post-ACK injection when readiness events arrive and audio hasn't started
                        try:
                            et = event_data.get("type") if isinstance(event_data, dict) else None
                            if et in ("SettingsApplied", "Welcome") and not self._in_audio_burst and self._greeting_injections < 2:
                                if self.websocket and not self.websocket.closed:
                                    logger.info("Injecting greeting after ACK", call_id=self.call_id, event_type=et)
                                    self._greeting_injections += 1
                                    try:
                                        await self._inject_message_dual((getattr(self.llm_config, 'initial_greeting', None) or getattr(self.config, 'greeting', None) or "Hello, how can I help you today?").strip())
                                    except Exception:
                                        logger.debug("Post-ACK greeting injection failed", exc_info=True)
                        except Exception:
                            pass
                        # If we were in an audio burst, a JSON control/event frame marks a boundary
                        if self._in_audio_burst and self.on_event:
                            await self.on_event({
                                'type': 'AgentAudioDone',
                                'streaming_done': True,
                                'call_id': self.call_id
                            })
                            self._in_audio_burst = False

                        if self.on_event:
                            await self.on_event(event_data)
                    except json.JSONDecodeError:
                        logger.error("Failed to parse JSON message from Deepgram", message=message)
                elif isinstance(message, bytes):
                    self._ready_to_stream = True
                    # One-time runtime probe: infer output encoding/rate from first bytes
                    try:
                        if not getattr(self, "_dg_output_inferred", False):
                            l = len(message)
                            inferred: Optional[str] = None
                            inferred_rate: Optional[int] = None
                            # Quick structural hints
                            if l % 2 == 1:
                                inferred = "mulaw"
                            else:
                                # Compare RMS treating payload as PCM16 vs μ-law→PCM16
                                try:
                                    rms_pcm = audioop.rms(message[: min(960, l - (l % 2))], 2) if l >= 2 else 0
                                except Exception:
                                    rms_pcm = 0
                                try:
                                    pcm_from_ulaw = mulaw_to_pcm16le(message[: min(320, l)])
                                    rms_ulaw = audioop.rms(pcm_from_ulaw, 2) if pcm_from_ulaw else 0
                                except Exception:
                                    rms_ulaw = 0
                                if rms_ulaw > max(50, int(1.5 * (rms_pcm or 1))):
                                    inferred = "mulaw"
                                else:
                                    inferred = "linear16"
                            # Heuristic rate inference for PCM16: check 20ms multiples
                            if inferred == "linear16":
                                # 20ms frame sizes for PCM16: 320@8k, 640@16k, 960@24k
                                if l % 960 == 0:
                                    inferred_rate = 24000
                                elif l % 640 == 0:
                                    inferred_rate = 16000
                                elif l % 320 == 0:
                                    inferred_rate = 8000
                            if inferred and inferred != self._dg_output_encoding:
                                try:
                                    logger.info(
                                        "Deepgram output encoding inferred from runtime payload",
                                        call_id=self.call_id,
                                        previous_encoding=self._dg_output_encoding,
                                        new_encoding=inferred,
                                        bytes=l,
                                        inferred_rate=inferred_rate,
                                    )
                                except Exception:
                                    pass
                                self._dg_output_encoding = inferred
                                if inferred_rate:
                                    self._dg_output_rate = inferred_rate
                                try:
                                    self._dg_output_inferred = True
                                except Exception:
                                    pass
                    except Exception:
                        logger.debug("Deepgram output inference failed", exc_info=True)
                    audio_event = {
                        'type': 'AgentAudio',
                        'data': message,
                        'streaming_chunk': True,
                        'call_id': self.call_id,
                        'encoding': self._dg_output_encoding,
                        'sample_rate': self._dg_output_rate,
                    }
                    if not self._first_output_chunk_logged:
                        logger.info(
                            "Deepgram AgentAudio first chunk",
                            call_id=self.call_id,
                            bytes=len(message),
                            encoding=self._dg_output_encoding,
                            sample_rate=self._dg_output_rate,
                        )
                        self._first_output_chunk_logged = True
                    self._in_audio_burst = True
                    if self.on_event:
                        await self.on_event(audio_event)
        except websockets.exceptions.ConnectionClosed as e:
            # Only warn once; avoid info duplicate from stop_session
            if not self._closed:
                logger.warning("Deepgram Voice Agent connection closed", reason=str(e))
        except Exception:
            logger.error("Error receiving events from Deepgram Voice Agent", exc_info=True)
        finally:
            # If socket ends mid-burst, close the burst cleanly
            if self._in_audio_burst and self.on_event:
                try:
                    await self.on_event({
                        'type': 'AgentAudioDone',
                        'streaming_done': True,
                        'call_id': self.call_id
                    })
                except Exception:
                    pass
            self._in_audio_burst = False

    async def speak(self, text: str):
        if not text or not self.websocket:
            return
        inject_message = {"type": "InjectAgentMessage", "content": text}
        try:
            await self.websocket.send(json.dumps(inject_message))
        except websockets.exceptions.ConnectionClosed as e:
            logger.error("Failed to send inject agent message: Connection is closed.", exc_info=True, code=e.code, reason=e.reason)

    async def _inject_message_dual(self, text: str):
        if not text or not self.websocket:
            return
        # Primary: V1 shape
        try:
            await self.websocket.send(json.dumps({"type": "InjectAgentMessage", "content": text}))
        except Exception:
            logger.debug("Primary InjectAgentMessage failed", exc_info=True)
        # Fallback: early-access variant for compatibility
        async def _fallback_case():
            try:
                await asyncio.sleep(0.5)
                if self.websocket and not self.websocket.closed and not self._in_audio_burst:
                    try:
                        await self.websocket.send(json.dumps({"type": "Inject Agent Message", "message": {"text": text}}))
                    except Exception:
                        logger.debug("Fallback Inject Agent Message failed", exc_info=True)
            except Exception:
                pass
        asyncio.create_task(_fallback_case())
    
    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about the provider and its capabilities."""
        return {
            "name": "DeepgramProvider",
            "type": "cloud",
            "supported_codecs": self.supported_codecs,
            "model": self.config.model,
            "tts_model": self.config.tts_model
        }
    
    def is_ready(self) -> bool:
        """Check if the provider is ready to process audio."""
        # Configuration readiness: we consider the provider ready when it's properly
        # configured and wired to emit events. A live websocket is only established
        # after start_session(call_id) during an actual call.
        try:
            api_key_ok = bool(getattr(self.config, 'api_key', None))
        except Exception:
            api_key_ok = False
        return api_key_ok and (self.on_event is not None)
