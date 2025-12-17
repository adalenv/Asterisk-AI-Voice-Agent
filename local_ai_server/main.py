from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import wave
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
from websockets.asyncio.server import serve
import websockets.client as ws_client

from constants import (
    _level_name,
    DEBUG_AUDIO_FLOW,
    SUPPORTED_MODES,
    DEFAULT_MODE,
    ULAW_SAMPLE_RATE,
    PCM16_TARGET_RATE,
    _normalize_text,
)
from optional_imports import VoskModel, KaldiRecognizer, Llama, PiperVoice


from session import SessionContext


class KrokoSTTBackend:
    """
    Kroko ASR streaming STT backend via WebSocket.
    
    Supports both:
    - Hosted API: wss://app.kroko.ai/api/v1/transcripts/streaming
    - On-premise: ws://localhost:6006 (Kroko ONNX server)
    
    Protocol based on official kroko-ai/integration-demos C module analysis.
    Audio format: PCM 32-bit float, 16kHz, mono
    Response format: {"type": "partial"|"final", "text": "...", "segment": N}
    """

    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        language: str = "en-US",
        endpoints: bool = True,
    ):
        self.base_url = url
        self.api_key = api_key
        self.language = language
        self.endpoints = endpoints
        self._subprocess: Optional[asyncio.subprocess.Process] = None

    def build_connection_url(self) -> str:
        """Build WebSocket URL with query parameters."""
        # Check if it's the hosted API or on-premise
        if "app.kroko.ai" in self.base_url:
            # Hosted API format
            params = f"?languageCode={self.language}&endpoints={'true' if self.endpoints else 'false'}"
            if self.api_key:
                params += f"&apiKey={self.api_key}"
            return f"{self.base_url}{params}"
        else:
            # On-premise server - no query params needed
            return self.base_url

    async def connect(self) -> Any:
        """
        Create and return a WebSocket connection to Kroko server.
        
        Returns the websocket object for use in session-specific operations.
        """
        url = self.build_connection_url()
        logging.info("🎤 KROKO - Connecting to %s", url.split("?")[0])  # Don't log API key

        try:
            ws = await ws_client.connect(url)

            # Wait for connected message (hosted API sends this)
            if "app.kroko.ai" in self.base_url:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(msg)
                    if data.get("type") == "connected":
                        logging.info("✅ KROKO - Connected to hosted API, session=%s", data.get("id"))
                except asyncio.TimeoutError:
                    logging.warning("⚠️ KROKO - No connected message received, continuing anyway")
            else:
                logging.info("✅ KROKO - Connected to on-premise server")

            return ws

        except Exception as exc:
            logging.error("❌ KROKO - Connection failed: %s", exc)
            raise

    @staticmethod
    def pcm16_to_float32(pcm16_audio: bytes) -> bytes:
        """
        Convert PCM16 audio to PCM32 float (IEEE-754) for Kroko.
        
        This matches the conversion in the official C module:
        _float32[c] = ((float)_int16[c]) / 32768
        """
        samples = np.frombuffer(pcm16_audio, dtype=np.int16)
        float_samples = samples.astype(np.float32) / 32768.0
        return float_samples.tobytes()

    async def send_audio(self, ws: Any, pcm16_audio: bytes) -> None:
        """
        Send PCM16 audio to Kroko, converting to float32 format.
        
        Args:
            ws: WebSocket connection
            pcm16_audio: Audio data in PCM16 format (16kHz, mono)
        """
        if ws is None:
            logging.warning("🎤 KROKO - Cannot send audio, no WebSocket connection")
            return

        float32_audio = self.pcm16_to_float32(pcm16_audio)

        try:
            await ws.send(float32_audio)
            if DEBUG_AUDIO_FLOW:
                logging.debug(
                    "🎤 KROKO - Sent %d bytes PCM16 → %d bytes float32",
                    len(pcm16_audio),
                    len(float32_audio),
                )
        except Exception as exc:
            logging.error("❌ KROKO - Failed to send audio: %s", exc)
            raise

    async def receive_transcript(self, ws: Any, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """
        Try to receive a transcript result from Kroko.
        
        Returns:
            Dict with keys: type ("partial"|"final"), text, segment, startedAt
            None if no message available within timeout
        """
        if ws is None:
            return None

        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)

            if DEBUG_AUDIO_FLOW:
                logging.debug("🎤 KROKO - Received: %s", data)

            return data

        except asyncio.TimeoutError:
            return None
        except json.JSONDecodeError as exc:
            logging.warning("⚠️ KROKO - Invalid JSON response: %s", exc)
            return None
        except ConnectionClosed:
            logging.warning("⚠️ KROKO - Connection closed")
            return None
        except Exception as exc:
            logging.error("❌ KROKO - Receive error: %s", exc)
            return None

    async def close(self, ws: Any) -> None:
        """Close the WebSocket connection."""
        if ws:
            try:
                await ws.close()
                logging.info("🎤 KROKO - Connection closed")
            except Exception as exc:
                logging.debug("KROKO - Close error (ignored): %s", exc)

    async def start_subprocess(self, model_path: str, port: int = 6006) -> bool:
        """
        Start the Kroko ONNX server as a subprocess (for embedded mode).
        
        Args:
            model_path: Path to the Kroko ONNX model file
            port: Port for the WebSocket server
            
        Returns:
            True if subprocess started successfully
        """
        kroko_binary = "/usr/local/bin/kroko-server"

        if not os.path.exists(kroko_binary):
            logging.warning("⚠️ KROKO - Binary not found at %s, using external server", kroko_binary)
            return False

        if not os.path.exists(model_path):
            logging.error("❌ KROKO - Model not found at %s", model_path)
            return False

        try:
            logging.info("🚀 KROKO - Starting embedded server on port %d", port)

            self._subprocess = await asyncio.create_subprocess_exec(
                kroko_binary,
                f"--model={model_path}",
                f"--port={port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for server to start
            await asyncio.sleep(2.0)

            if self._subprocess.returncode is not None:
                stderr = await self._subprocess.stderr.read()
                logging.error("❌ KROKO - Subprocess failed: %s", stderr.decode())
                return False

            logging.info("✅ KROKO - Embedded server started (PID=%d)", self._subprocess.pid)
            return True

        except Exception as exc:
            logging.error("❌ KROKO - Failed to start subprocess: %s", exc)
            return False

    async def stop_subprocess(self) -> None:
        """Stop the Kroko ONNX server subprocess."""
        if self._subprocess:
            try:
                self._subprocess.terminate()
                await asyncio.wait_for(self._subprocess.wait(), timeout=5.0)
                logging.info("🛑 KROKO - Subprocess stopped")
            except asyncio.TimeoutError:
                self._subprocess.kill()
                logging.warning("⚠️ KROKO - Subprocess killed (timeout)")
            except Exception as exc:
                logging.error("❌ KROKO - Error stopping subprocess: %s", exc)
            finally:
                self._subprocess = None


class SherpaONNXSTTBackend:
    """
    Local streaming STT backend using sherpa-onnx.
    
    Sherpa-onnx is the underlying library that Kroko ASR is built on.
    This provides fully local ASR without needing a separate server process.
    
    Supports streaming (online) recognition with low latency.
    """
    
    def __init__(self, model_path: str, sample_rate: int = 16000):
        """
        Initialize the sherpa-onnx recognizer.
        
        Args:
            model_path: Path to the model directory or .onnx file
            sample_rate: Audio sample rate (default 16000 Hz)
        """
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.recognizer = None
        self._initialized = False
        
    def initialize(self) -> bool:
        """
        Initialize the recognizer with the model.
        
        Returns:
            True if initialization succeeded
        """
        try:
            import sherpa_onnx
            
            # Check if model exists
            if not os.path.exists(self.model_path):
                logging.error("❌ SHERPA - Model not found at %s", self.model_path)
                return False
            
            # Find model files (handles various naming conventions)
            tokens_file = self._find_tokens_file()
            encoder_file = self._find_encoder_file()
            decoder_file = self._find_decoder_file()
            joiner_file = self._find_joiner_file()
            
            if not all([tokens_file, encoder_file, decoder_file, joiner_file]):
                missing = []
                if not tokens_file: missing.append("tokens.txt")
                if not encoder_file: missing.append("encoder*.onnx")
                if not decoder_file: missing.append("decoder*.onnx")
                if not joiner_file: missing.append("joiner*.onnx")
                logging.error("❌ SHERPA - Missing model files: %s", ", ".join(missing))
                return False
            
            logging.info("📁 SHERPA - Model files found:")
            logging.info("   tokens: %s", tokens_file)
            logging.info("   encoder: %s", encoder_file)
            logging.info("   decoder: %s", decoder_file)
            logging.info("   joiner: %s", joiner_file)
            
            # Create recognizer using from_transducer classmethod
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tokens_file,
                encoder=encoder_file,
                decoder=decoder_file,
                joiner=joiner_file,
                num_threads=2,
                sample_rate=self.sample_rate,  # Must be int
                enable_endpoint_detection=True,
                decoding_method="greedy_search",
            )
            self._initialized = True
            logging.info("✅ SHERPA - Recognizer initialized with model %s", self.model_path)
            return True
            
        except ImportError:
            logging.error("❌ SHERPA - sherpa-onnx not installed")
            return False
        except Exception as exc:
            logging.error("❌ SHERPA - Failed to initialize: %s", exc)
            return False
    
    def _find_file_by_pattern(self, directory: str, prefix: str, suffix: str = ".onnx") -> str:
        """Find a file matching prefix*suffix in directory."""
        if not os.path.isdir(directory):
            return ""
        for filename in os.listdir(directory):
            if filename.startswith(prefix) and filename.endswith(suffix):
                return os.path.join(directory, filename)
        return ""
    
    def _find_tokens_file(self) -> str:
        """Find tokens file in model directory."""
        # If model_path is a directory
        if os.path.isdir(self.model_path):
            tokens_path = os.path.join(self.model_path, "tokens.txt")
            if os.path.exists(tokens_path):
                return tokens_path
        # If model_path is a file, check its directory
        model_dir = os.path.dirname(self.model_path)
        tokens_path = os.path.join(model_dir, "tokens.txt")
        if os.path.exists(tokens_path):
            return tokens_path
        return ""
    
    def _find_encoder_file(self) -> str:
        """Find encoder model in directory."""
        search_dir = self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        # First try exact name
        exact = os.path.join(search_dir, "encoder.onnx")
        if os.path.exists(exact):
            return exact
        # Try pattern matching (prefer int8 for speed)
        int8 = self._find_file_by_pattern(search_dir, "encoder", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "encoder", ".onnx")
    
    def _find_decoder_file(self) -> str:
        """Find decoder model in directory."""
        search_dir = self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        exact = os.path.join(search_dir, "decoder.onnx")
        if os.path.exists(exact):
            return exact
        int8 = self._find_file_by_pattern(search_dir, "decoder", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "decoder", ".onnx")
    
    def _find_joiner_file(self) -> str:
        """Find joiner model in directory."""
        search_dir = self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        exact = os.path.join(search_dir, "joiner.onnx")
        if os.path.exists(exact):
            return exact
        int8 = self._find_file_by_pattern(search_dir, "joiner", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "joiner", ".onnx")
    
    def create_stream(self) -> Any:
        """Create a new recognition stream for a session."""
        if not self._initialized or not self.recognizer:
            return None
        return self.recognizer.create_stream()
    
    def process_audio(self, stream: Any, pcm16_audio: bytes) -> Optional[Dict[str, Any]]:
        """
        Process PCM16 audio and return transcript if available.
        
        Args:
            stream: Recognition stream from create_stream()
            pcm16_audio: Audio in PCM16 format, 16kHz mono
            
        Returns:
            Dict with keys: type ("partial"|"final"), text
            None if no result yet
        """
        if stream is None or not self._initialized:
            return None
        
        try:
            # Convert PCM16 bytes to float32 samples
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            
            # Feed audio to recognizer
            stream.accept_waveform(self.sample_rate, float_samples)
            
            # Check if we have results
            if self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)
            
            result = self.recognizer.get_result(stream)
            # Result can be a string or object with .text attribute depending on version
            if isinstance(result, str):
                text = result.strip()
            elif hasattr(result, 'text'):
                text = result.text.strip() if result.text else ""
            else:
                text = str(result).strip() if result else ""
            
            if not text:
                return None
            
            # Check if this is a final result (endpoint detected)
            is_final = self.recognizer.is_endpoint(stream)
            
            if is_final:
                # Reset stream for next utterance
                self.recognizer.reset(stream)
                return {"type": "final", "text": text}
            else:
                return {"type": "partial", "text": text}
                
        except Exception as exc:
            logging.error("❌ SHERPA - Process error: %s", exc)
            return None
    
    def close_stream(self, stream: Any) -> None:
        """Close a recognition stream."""
        # Sherpa streams don't need explicit cleanup
        pass
    
    def shutdown(self) -> None:
        """Shutdown the recognizer."""
        self.recognizer = None
        self._initialized = False
        logging.info("🛑 SHERPA - Recognizer shutdown")


class KokoroTTSBackend:
    """
    Kokoro TTS backend using the kokoro package.
    
    Kokoro is a high-quality, lightweight TTS model (82M params) that delivers
    comparable quality to larger models while being faster and more efficient.
    
    Requires: espeak-ng system package
    Sample rate: 24000 Hz
    """
    
    def __init__(self, voice: str = "af_heart", lang_code: str = "a", model_path: str = None):
        """
        Initialize Kokoro TTS.
        
        Args:
            voice: Voice name (e.g., 'af_heart', 'af_bella', 'am_adam')
            lang_code: Language code ('a' for American English)
            model_path: Path to local model directory (optional, uses HF cache if not provided)
        """
        self.voice = voice
        self.lang_code = lang_code
        self.model_path = model_path
        self.pipeline = None
        self._initialized = False
        self.sample_rate = 24000
    
    def initialize(self) -> bool:
        """
        Initialize the Kokoro pipeline.
        
        Returns:
            True if initialization succeeded
        """
        try:
            from kokoro import KPipeline
            from kokoro.model import KModel
            
            logging.info("🎙️ KOKORO - Initializing TTS (voice=%s, lang=%s)", self.voice, self.lang_code)
            
            # If model_path provided, load from local files
            if self.model_path and os.path.isdir(self.model_path):
                config_path = os.path.join(self.model_path, "config.json")
                model_path = os.path.join(self.model_path, "kokoro-v1_0.pth")
                
                if os.path.exists(config_path) and os.path.exists(model_path):
                    logging.info("🎙️ KOKORO - Loading local model from %s", self.model_path)
                    # Load model directly from local files
                    kmodel = KModel(
                        config=config_path,
                        model=model_path,
                        repo_id="hexgrad/Kokoro-82M",  # Suppress warning
                    )
                    self.pipeline = KPipeline(
                        lang_code=self.lang_code,
                        model=kmodel,
                        repo_id="hexgrad/Kokoro-82M",  # For voice loading
                    )
                else:
                    logging.warning("⚠️ KOKORO - Local model files not found, falling back to HuggingFace")
                    self.pipeline = KPipeline(
                        lang_code=self.lang_code,
                        repo_id="hexgrad/Kokoro-82M",
                    )
            else:
                # Fallback to HuggingFace download
                logging.info("🎙️ KOKORO - Using HuggingFace model (will download if needed)")
                self.pipeline = KPipeline(
                    lang_code=self.lang_code,
                    repo_id="hexgrad/Kokoro-82M",
                )
            
            self._initialized = True
            logging.info("✅ KOKORO - TTS initialized successfully")
            return True
            
        except ImportError:
            logging.error("❌ KOKORO - kokoro package not installed")
            return False
        except Exception as exc:
            logging.error("❌ KOKORO - Failed to initialize: %s", exc)
            return False
    
    def synthesize(self, text: str) -> bytes:
        """
        Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio data as PCM16 bytes at 24kHz
        """
        if not self._initialized or not self.pipeline:
            logging.error("❌ KOKORO - Not initialized")
            return b""
        
        try:
            import numpy as np
            
            # Generate audio using Kokoro pipeline
            audio_chunks = []
            generator = self.pipeline(text, voice=self.voice)
            
            for i, (gs, ps, audio) in enumerate(generator):
                if audio is not None:
                    audio_chunks.append(audio)
            
            if not audio_chunks:
                logging.warning("⚠️ KOKORO - No audio generated")
                return b""
            
            # Concatenate all chunks
            full_audio = np.concatenate(audio_chunks)
            
            # Convert float32 to int16 PCM
            audio_int16 = (full_audio * 32767).astype(np.int16)
            
            logging.debug("🎙️ KOKORO - Generated %d samples at %dHz", len(audio_int16), self.sample_rate)
            return audio_int16.tobytes()
            
        except Exception as exc:
            logging.error("❌ KOKORO - Synthesis failed: %s", exc)
            return b""
    
    def shutdown(self) -> None:
        """Shutdown the TTS pipeline."""
        self.pipeline = None
        self._initialized = False
        logging.info("🛑 KOKORO - TTS shutdown")


class AudioProcessor:
    """Handles audio format conversions for MVP uLaw 8kHz pipeline"""

    @staticmethod
    def resample_audio(input_data: bytes,
                       input_rate: int,
                       output_rate: int,
                       input_format: str = "raw",
                       output_format: str = "raw") -> bytes:
        """Resample audio using sox"""
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as input_file:
                input_file.write(input_data)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(suffix=f".{output_format}", delete=False) as output_file:
                output_path = output_file.name

            # Use sox to resample - specify input format for raw PCM data
            cmd = [
                "sox",
                "-t",
                "raw",
                "-r",
                str(input_rate),
                "-e",
                "signed-integer",
                "-b",
                "16",
                "-c",
                "1",
                input_path,
                "-r",
                str(output_rate),
                "-c",
                "1",
                "-e",
                "signed-integer",
                "-b",
                "16",
                output_path,
            ]

            subprocess.run(cmd, capture_output=True, check=True)

            with open(output_path, "rb") as f:
                resampled_data = f.read()

            os.unlink(input_path)
            os.unlink(output_path)

            return resampled_data

        except Exception as exc:  # pragma: no cover - defensive guard
            logging.error("Audio resampling failed: %s", exc)
            return input_data

    @staticmethod
    def convert_to_ulaw_8k(input_data: bytes, input_rate: int) -> bytes:
        """Convert audio to uLaw 8kHz format for ARI playback"""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as input_file:
                input_file.write(input_data)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(suffix=".ulaw", delete=False) as output_file:
                output_path = output_file.name

            cmd = [
                "sox",
                input_path,
                "-r",
                str(ULAW_SAMPLE_RATE),
                "-c",
                "1",
                "-e",
                "mu-law",
                "-t",
                "raw",
                output_path,
            ]

            subprocess.run(cmd, capture_output=True, check=True)

            with open(output_path, "rb") as f:
                ulaw_data = f.read()

            os.unlink(input_path)
            os.unlink(output_path)

            return ulaw_data

        except Exception as exc:  # pragma: no cover - defensive guard
            logging.error("uLaw conversion failed: %s", exc)
            return input_data


# Backends and audio processor are maintained in separate modules for easier development.
# These imports intentionally shadow the legacy in-file definitions above.
from stt_backends import KrokoSTTBackend, SherpaONNXSTTBackend
from tts_backends import KokoroTTSBackend
from audio_processor import AudioProcessor


class LocalAIServer:
    def __init__(self):
        self.stt_model: Optional[VoskModel] = None
        self.llm_model: Optional[Llama] = None
        self.tts_model: Optional[PiperVoice] = None
        self.audio_processor = AudioProcessor()
        
        # Lock to serialize LLM inference (llama-cpp is NOT thread-safe)
        self._llm_lock = asyncio.Lock()
        # Optional WebSocket auth token for local-ai-server. If set, clients must
        # authenticate with {"type":"auth","auth_token":"..."} before other messages.
        self.ws_auth_token = (os.getenv("LOCAL_WS_AUTH_TOKEN", "") or "").strip()

        # ─────────────────────────────────────────────────────────────────────
        # STT Backend Selection: vosk (default), kroko, or sherpa
        # ─────────────────────────────────────────────────────────────────────
        self.stt_backend = os.getenv("LOCAL_STT_BACKEND", "vosk").lower()
        
        # Kroko STT settings (if using kroko backend)
        self.kroko_backend: Optional[KrokoSTTBackend] = None
        
        # Sherpa-onnx STT settings (if using sherpa backend for local streaming ASR)
        self.sherpa_backend: Optional[SherpaONNXSTTBackend] = None
        self.sherpa_model_path = os.getenv(
            "SHERPA_MODEL_PATH", "/app/models/stt/sherpa"
        )
        self.kroko_url = os.getenv(
            "KROKO_URL",
            "wss://app.kroko.ai/api/v1/transcripts/streaming"  # Default to hosted API
        )
        self.kroko_api_key = os.getenv("KROKO_API_KEY", "")
        self.kroko_language = os.getenv("KROKO_LANGUAGE", "en-US")
        self.kroko_model_path = os.getenv(
            "KROKO_MODEL_PATH", "/app/models/kroko/kroko-en-v1.0.onnx"
        )
        self.kroko_embedded = os.getenv("KROKO_EMBEDDED", "0") == "1"
        self.kroko_port = int(os.getenv("KROKO_PORT", "6006"))

        # Model paths
        self.stt_model_path = os.getenv(
            "LOCAL_STT_MODEL_PATH", "/app/models/stt/vosk-model-en-us-0.22"
        )
        self.llm_model_path = os.getenv(
            "LOCAL_LLM_MODEL_PATH", "/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf"
        )
        self.tts_model_path = os.getenv(
            "LOCAL_TTS_MODEL_PATH", "/app/models/tts/en_US-lessac-medium.onnx"
        )

        # ─────────────────────────────────────────────────────────────────────
        # TTS Backend Selection: piper (default) or kokoro
        # ─────────────────────────────────────────────────────────────────────
        self.tts_backend = os.getenv("LOCAL_TTS_BACKEND", "piper").lower()
        
        # Kokoro TTS settings (if using kokoro backend)
        self.kokoro_backend: Optional[KokoroTTSBackend] = None
        # Back-compat / docs alias: LOCAL_TTS_VOICE is treated as Kokoro voice.
        self.kokoro_voice = os.getenv("KOKORO_VOICE") or os.getenv("LOCAL_TTS_VOICE", "af_heart")
        # Kokoro mode:
        # - "local": prefer local files under KOKORO_MODEL_PATH (falls back to HF if missing)
        # - "hf": force HuggingFace-backed loading (no local model files required)
        # - "api": use a remote Kokoro Web API (OpenAI-compatible audio/speech)
        self.kokoro_mode = (os.getenv("KOKORO_MODE", "local") or "local").strip().lower()
        self.kokoro_lang = os.getenv("KOKORO_LANG", "a")  # 'a' = American English
        self.kokoro_model_path = os.getenv("KOKORO_MODEL_PATH", "/app/models/tts/kokoro")
        self.kokoro_api_base_url = (os.getenv("KOKORO_API_BASE_URL", "") or "").strip()
        self.kokoro_api_key = (os.getenv("KOKORO_API_KEY", "") or "").strip()
        self.kokoro_api_model = (os.getenv("KOKORO_API_MODEL", "model") or "model").strip()

        default_threads = max(1, min(16, os.cpu_count() or 1))
        self.llm_threads = int(os.getenv("LOCAL_LLM_THREADS", str(default_threads)))
        self.llm_context = int(os.getenv("LOCAL_LLM_CONTEXT", "768"))
        self.llm_batch = int(os.getenv("LOCAL_LLM_BATCH", "128"))
        self.llm_max_tokens = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "64"))
        self.llm_temperature = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.4"))
        self.llm_top_p = float(os.getenv("LOCAL_LLM_TOP_P", "0.85"))
        self.llm_repeat_penalty = float(os.getenv("LOCAL_LLM_REPEAT_PENALTY", "1.05"))
        
        # GPU acceleration: 0 = CPU only, -1 = auto-detect, N = specific layer count
        # Auto-detect will use GPU if CUDA is available, otherwise CPU
        self.llm_gpu_layers = int(os.getenv("LOCAL_LLM_GPU_LAYERS", "0"))
        self.llm_system_prompt = os.getenv(
            "LOCAL_LLM_SYSTEM_PROMPT",
            "You are a helpful AI voice assistant. Respond naturally and conversationally to the caller."
        )
        self.llm_stop_tokens = [
            token.strip()
            for token in os.getenv(
                "LOCAL_LLM_STOP_TOKENS",
                "<|user|>,<|assistant|>,<|end|>"
            ).split(",")
            if token.strip()
        ]
        if not self.llm_stop_tokens:
            self.llm_stop_tokens = ["<|user|>", "<|assistant|>", "<|end|>"]
        # Allow disabling mlock by env to avoid startup failures on some hosts
        self.llm_use_mlock = bool(int(os.getenv("LOCAL_LLM_USE_MLOCK", "0")))

        # Audio buffering for STT (20ms chunks need to be buffered for effective STT)
        self.audio_buffer = b""
        self.buffer_size_bytes = PCM16_TARGET_RATE * 2 * 1.0  # 1 second at 16kHz (32000 bytes)
        # Process buffer after N ms of silence (idle finalizer). Configurable via env.
        self.buffer_timeout_ms = int(os.getenv("LOCAL_STT_IDLE_MS", "5000"))

    def _resolve_vosk_model_path(self, path: str) -> str:
        """Resolve the correct Vosk model directory.

        Some archives extract with an extra nesting level. We prefer a directory
        that contains a 'conf' subdirectory which is expected by Vosk.
        """
        try:
            if os.path.isdir(path) and os.path.isdir(os.path.join(path, "conf")):
                return path
            if os.path.isdir(path):
                for entry in os.listdir(path):
                    candidate = os.path.join(path, entry)
                    if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, "conf")):
                        return candidate
        except Exception as exc:  # pragma: no cover - defensive
            logging.debug("Vosk model path resolution skipped", exc_info=True)
        return path

    async def initialize_models(self):
        """Initialize all AI models with proper error handling"""
        logging.info("🚀 Initializing enhanced AI models for MVP...")

        await self._load_stt_model()
        await self._load_llm_model()
        await self.run_startup_latency_check()
        await self._load_tts_model()

        logging.info("✅ All models loaded successfully for MVP pipeline")

    async def _load_stt_model(self):
        """Load STT model based on configured backend (vosk, kroko, or sherpa)."""
        if self.stt_backend == "kroko":
            await self._load_kroko_backend()
        elif self.stt_backend == "sherpa":
            await self._load_sherpa_backend()
        else:
            await self._load_vosk_backend()

    async def _load_vosk_backend(self):
        """Load Vosk STT model with 16kHz support."""
        try:
            if VoskModel is None or KaldiRecognizer is None:
                raise ImportError(
                    "Vosk STT backend requested but vosk is not installed. "
                    "Build with INCLUDE_VOSK=true or install vosk==0.3.45."
                )

            # Resolve nested model directory if needed
            resolved_path = self._resolve_vosk_model_path(self.stt_model_path)
            if not os.path.exists(resolved_path):
                raise FileNotFoundError(f"STT model not found at {resolved_path}")

            # Extra sanity: require 'conf' folder inside the model dir
            if not os.path.isdir(os.path.join(resolved_path, "conf")):
                # Provide a helpful listing for debugging
                try:
                    listing = ", ".join(os.listdir(resolved_path))
                except Exception:
                    listing = "<unavailable>"
                raise FileNotFoundError(
                    f"STT model at {resolved_path} does not appear to be a valid Vosk model (missing 'conf'). Contents: {listing}"
                )

            self.stt_model = VoskModel(resolved_path)
            # Keep the resolved path for reference
            self.stt_model_path = resolved_path
            logging.info("✅ STT backend: Vosk loaded from %s (16kHz native)", self.stt_model_path)
        except Exception as exc:
            logging.error("❌ Failed to load Vosk STT model: %s", exc)
            raise

    async def _load_kroko_backend(self):
        """Initialize Kroko STT backend."""
        try:
            logging.info("🎤 STT backend: Kroko (language=%s)", self.kroko_language)

            # Initialize Kroko backend
            self.kroko_backend = KrokoSTTBackend(
                url=self.kroko_url,
                api_key=self.kroko_api_key if self.kroko_api_key else None,
                language=self.kroko_language,
                endpoints=True,
            )

            # If embedded mode, start the Kroko ONNX server subprocess
            if self.kroko_embedded:
                # Update URL to local server
                self.kroko_url = f"ws://127.0.0.1:{self.kroko_port}"
                self.kroko_backend.base_url = self.kroko_url

                success = await self.kroko_backend.start_subprocess(
                    self.kroko_model_path, self.kroko_port
                )
                if not success:
                    raise RuntimeError("Failed to start embedded Kroko server")
                logging.info("✅ STT backend: Kroko embedded server started on port %d", self.kroko_port)
            else:
                # Test connection to external server
                try:
                    test_ws = await self.kroko_backend.connect()
                    await self.kroko_backend.close(test_ws)
                    logging.info("✅ STT backend: Kroko connected to %s", self.kroko_url.split("?")[0])
                except Exception as conn_exc:
                    logging.warning(
                        "⚠️ STT backend: Kroko connection test failed (%s), will retry on first request",
                        conn_exc
                    )

        except Exception as exc:
            logging.error("❌ Failed to initialize Kroko STT backend: %s", exc)
            raise

    async def _load_sherpa_backend(self):
        """Initialize Sherpa-onnx STT backend for local streaming ASR."""
        try:
            logging.info("🎤 STT backend: Sherpa-onnx (local streaming ASR)")

            self.sherpa_backend = SherpaONNXSTTBackend(
                model_path=self.sherpa_model_path,
                sample_rate=PCM16_TARGET_RATE,
            )

            if not self.sherpa_backend.initialize():
                raise RuntimeError("Failed to initialize Sherpa-onnx recognizer")

            logging.info("✅ STT backend: Sherpa-onnx initialized with model %s", self.sherpa_model_path)

        except Exception as exc:
            logging.error("❌ Failed to initialize Sherpa STT backend: %s", exc)
            raise

    def _detect_gpu_layers(self) -> int:
        """Detect GPU availability and return appropriate layer count.
        
        Returns:
            0 if no GPU or GPU disabled
            Number of layers to offload if GPU available
        """
        if self.llm_gpu_layers == 0:
            return 0  # Explicitly disabled
        
        if self.llm_gpu_layers > 0:
            return self.llm_gpu_layers  # User specified exact count
        
        # Auto-detect (-1): check if CUDA is available
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                logging.info("🎮 GPU detected: %s (%.1f GB)", gpu_name, gpu_mem)
                # Use 35 layers for most models (good balance)
                return 35
        except ImportError:
            logging.debug("PyTorch not available for GPU detection")
        except Exception as e:
            logging.debug("GPU detection failed: %s", e)
        
        return 0  # Default to CPU

    async def _load_llm_model(self):
        """Load LLM model with optimized parameters for faster inference"""
        if Llama is None:
            logging.warning(
                "🤖 LLM backend disabled: llama-cpp-python not installed. "
                "Build with INCLUDE_LLAMA=true or install llama-cpp-python==0.3.16."
            )
            self.llm_model = None
            return

        try:
            if not os.path.exists(self.llm_model_path):
                raise FileNotFoundError(f"LLM model not found at {self.llm_model_path}")

            # Determine GPU layers
            gpu_layers = self._detect_gpu_layers()
            gpu_status = f"GPU ({gpu_layers} layers)" if gpu_layers > 0 else "CPU only"

            self.llm_model = Llama(
                model_path=self.llm_model_path,
                n_ctx=self.llm_context,
                n_threads=self.llm_threads,
                n_batch=self.llm_batch,
                n_gpu_layers=gpu_layers,
                verbose=False,
                use_mmap=True,
                use_mlock=self.llm_use_mlock,
                add_bos=False,
            )
            logging.info("✅ LLM model loaded: %s (%s)", self.llm_model_path, gpu_status)
            logging.info(
                "📊 LLM Config: ctx=%s, threads=%s, batch=%s, max_tokens=%s, temp=%s, gpu_layers=%s",
                self.llm_context,
                self.llm_threads,
                self.llm_batch,
                self.llm_max_tokens,
                self.llm_temperature,
                gpu_layers,
            )
        except Exception as exc:
            logging.error("❌ Failed to load LLM model: %s", exc)
            raise

    async def run_startup_latency_check(self) -> None:
        """Run a lightweight LLM inference at startup to log baseline latency."""
        if not self.llm_model:
            return

        try:
            session = SessionContext(call_id="startup-latency")
            sample_text = "Hello, can you hear me?"
            prompt, prompt_tokens, truncated, raw_tokens = self._prepare_llm_prompt(
                session, sample_text
            )
            loop = asyncio.get_running_loop()
            started = loop.time()
            logging.info(
                "🧪 LLM WARMUP START - Running startup latency check (prompt_tokens=%s raw_tokens=%s model=%s ctx=%s batch=%s max_tokens<=%s)",
                prompt_tokens,
                raw_tokens,
                os.path.basename(self.llm_model_path),
                self.llm_context,
                self.llm_batch,
                min(self.llm_max_tokens, 32),
            )

            # Heartbeat: log progress while the warm-up runs so users see activity
            done = asyncio.Event()

            async def _heartbeat():
                elapsed = 0
                interval = 5
                try:
                    while not done.is_set():
                        await asyncio.sleep(interval)
                        elapsed += interval
                        logging.info(
                            "⏳ LLM WARMUP - In progress (~%ss elapsed, model=%s ctx=%s batch=%s)",
                            elapsed,
                            os.path.basename(self.llm_model_path),
                            self.llm_context,
                            self.llm_batch,
                        )
                except asyncio.CancelledError:
                    pass

            hb_task = asyncio.create_task(_heartbeat())

            await asyncio.to_thread(
                self.llm_model,
                prompt,
                max_tokens=min(self.llm_max_tokens, 32),
                stop=self.llm_stop_tokens,
                echo=False,
                temperature=self.llm_temperature,
                top_p=self.llm_top_p,
                repeat_penalty=self.llm_repeat_penalty,
            )

            latency_ms = round((loop.time() - started) * 1000.0, 2)
            done.set()
            try:
                hb_task.cancel()
            except Exception:
                pass
            logging.info(
                "🤖 LLM STARTUP LATENCY - %.2f ms (prompt_tokens=%s raw_tokens=%s truncated=%s)",
                latency_ms,
                prompt_tokens,
                raw_tokens,
                truncated,
            )
        except Exception as exc:  # pragma: no cover - best-effort metric
            logging.warning(
                "🤖 LLM STARTUP LATENCY CHECK FAILED: %s",
                exc,
                exc_info=True,
            )

    async def _load_tts_model(self):
        """Load TTS model based on configured backend (piper or kokoro)."""
        if self.tts_backend == "kokoro":
            await self._load_kokoro_backend()
        else:
            await self._load_piper_backend()

    async def _load_piper_backend(self):
        """Load Piper TTS model with 22kHz support."""
        try:
            if PiperVoice is None:
                raise ImportError(
                    "Piper TTS backend requested but piper-tts is not installed. "
                    "Build with INCLUDE_PIPER=true or install piper-tts==1.2.0."
                )

            if not os.path.exists(self.tts_model_path):
                raise FileNotFoundError(f"TTS model not found at {self.tts_model_path}")
            
            # Piper requires both .onnx model AND .onnx.json config file
            config_path = self.tts_model_path + ".json"
            if not os.path.exists(config_path):
                raise FileNotFoundError(
                    f"Piper TTS config file not found at {config_path}. "
                    f"Piper requires both the .onnx model AND the .onnx.json config file. "
                    f"Please re-download the model from the Models page to get both files."
                )

            self.tts_model = PiperVoice.load(self.tts_model_path)
            logging.info("✅ TTS backend: Piper loaded from %s (22kHz native)", self.tts_model_path)
        except Exception as exc:
            logging.error("❌ Failed to load Piper TTS model: %s", exc)
            raise

    async def _load_kokoro_backend(self):
        """Initialize Kokoro TTS backend."""
        try:
            logging.info(
                "🎙️ TTS backend: Kokoro (voice=%s, mode=%s)", self.kokoro_voice, self.kokoro_mode
            )

            # Check if local model exists (unless explicitly forcing HF mode)
            model_path = None
            if self.kokoro_mode == "api":
                # API mode does not require local model files or kokoro package initialization.
                if not self.kokoro_api_base_url:
                    raise RuntimeError(
                        "KOKORO_MODE=api requires KOKORO_API_BASE_URL (e.g. https://voice-generator.pages.dev/api/v1)"
                    )
                logging.info("🌐 KOKORO - Using remote Web API at %s", self.kokoro_api_base_url)
                self.kokoro_backend = None
                return
            if self.kokoro_mode == "hf":
                logging.info("🌐 KOKORO - Forcing HuggingFace-backed model loading")
            elif os.path.isdir(self.kokoro_model_path):
                logging.info(
                    "📁 KOKORO - Found local model at %s", self.kokoro_model_path
                )
                model_path = self.kokoro_model_path
            else:
                logging.warning(
                    "⚠️ KOKORO - Local mode requested but model directory not found at %s; falling back to HuggingFace",
                    self.kokoro_model_path,
                )

            self.kokoro_backend = KokoroTTSBackend(
                voice=self.kokoro_voice,
                lang_code=self.kokoro_lang,
                model_path=model_path,
            )

            if not self.kokoro_backend.initialize():
                raise RuntimeError("Failed to initialize Kokoro TTS")

            logging.info("✅ TTS backend: Kokoro initialized (24kHz native)")

        except Exception as exc:
            logging.error("❌ Failed to initialize Kokoro TTS backend: %s", exc)
            raise

    async def _cleanup_kroko_backend(self) -> None:
        """Stop any embedded Kroko subprocess and clear backend state."""
        if not self.kroko_backend:
            return
        try:
            await self.kroko_backend.stop_subprocess()
        except Exception as exc:  # pragma: no cover
            logging.debug("Kroko backend cleanup failed: %s", exc, exc_info=True)
        finally:
            self.kroko_backend = None

    async def shutdown(self) -> None:
        """Best-effort cleanup on server shutdown."""
        logging.info("🛑 Shutting down Local AI Server...")
        await self._cleanup_kroko_backend()
        if self.sherpa_backend:
            try:
                self.sherpa_backend.shutdown()
            except Exception as exc:  # pragma: no cover
                logging.debug("Sherpa backend shutdown failed: %s", exc, exc_info=True)
            self.sherpa_backend = None
        if self.kokoro_backend:
            try:
                self.kokoro_backend.shutdown()
            except Exception as exc:  # pragma: no cover
                logging.debug("Kokoro backend shutdown failed: %s", exc, exc_info=True)
            self.kokoro_backend = None
        self.stt_model = None
        self.tts_model = None
        self.llm_model = None

    async def reload_models(self):
        """Hot reload all models without restarting the server"""
        logging.info("🔄 Hot reloading models...")
        try:
            await self._cleanup_kroko_backend()
            await self.initialize_models()
            logging.info("✅ Models reloaded successfully")
        except Exception as exc:
            logging.error("❌ Model reload failed: %s", exc)
            raise

    async def reload_llm_only(self):
        """Hot reload only the LLM model with optimized parameters"""
        logging.info("🔄 Hot reloading LLM model with optimizations...")
        async with self._llm_lock:
            try:
                if self.llm_model:
                    del self.llm_model
                    self.llm_model = None
                    logging.info("🗑️ Previous LLM model unloaded")

                await self._load_llm_model()
                logging.info("✅ LLM model reloaded with optimizations")
                logging.info(
                    "📊 Optimized: ctx=%s, batch=%s, temp=%s, max_tokens=%s",
                    self.llm_context,
                    self.llm_batch,
                    self.llm_temperature,
                    self.llm_max_tokens,
                )
            except Exception as exc:
                logging.error("❌ LLM reload failed: %s", exc)
                raise

    async def process_stt_buffered(self, audio_data: bytes) -> str:
        """Process STT with buffering for 20ms chunks"""
        try:
            if not self.stt_model:
                logging.error("STT model not loaded")
                return ""
            if KaldiRecognizer is None:
                logging.error("Vosk STT backend unavailable (vosk not installed)")
                return ""

            self.audio_buffer += audio_data
            logging.debug(
                "🎵 STT BUFFER - Added %s bytes, buffer now %s bytes",
                len(audio_data),
                len(self.audio_buffer),
            )

            if len(self.audio_buffer) < self.buffer_size_bytes:
                logging.debug(
                    "🎵 STT BUFFER - Not enough audio yet (%s/%s bytes)",
                    len(self.audio_buffer),
                    self.buffer_size_bytes,
                )
                return ""

            logging.info("🎵 STT PROCESSING - Processing buffered audio: %s bytes", len(self.audio_buffer))

            recognizer = KaldiRecognizer(self.stt_model, PCM16_TARGET_RATE)

            if recognizer.AcceptWaveform(self.audio_buffer):
                result = json.loads(recognizer.Result())
            else:
                result = json.loads(recognizer.FinalResult())

            transcript = result.get("text", "").strip()
            if transcript:
                logging.info("📝 STT RESULT - Transcript: '%s'", transcript)
            else:
                logging.debug("📝 STT RESULT - Transcript empty after buffering")

            self.audio_buffer = b""
            return transcript

        except Exception as exc:
            logging.error("Buffered STT processing failed: %s", exc, exc_info=True)
            return ""

    async def process_stt(self, audio_data: bytes, input_rate: int = PCM16_TARGET_RATE) -> str:
        """Process STT with Vosk only - optimized for telephony audio"""
        try:
            if not self.stt_model:
                logging.error("STT model not loaded")
                return ""
            if KaldiRecognizer is None:
                logging.error("Vosk STT backend unavailable (vosk not installed)")
                return ""

            logging.debug("🎤 STT INPUT - %s bytes at %s Hz", len(audio_data), input_rate)

            if input_rate != PCM16_TARGET_RATE:
                logging.debug(
                    "🎵 STT INPUT - Resampling %s Hz → %s Hz: %s bytes",
                    input_rate,
                    PCM16_TARGET_RATE,
                    len(audio_data),
                )
                resampled_audio = await asyncio.to_thread(
                    self.audio_processor.resample_audio,
                    audio_data,
                    input_rate,
                    PCM16_TARGET_RATE,
                    "raw",
                    "raw",
                )
            else:
                resampled_audio = audio_data

            recognizer = KaldiRecognizer(self.stt_model, PCM16_TARGET_RATE)

            if recognizer.AcceptWaveform(resampled_audio):
                result = json.loads(recognizer.Result())
            else:
                result = json.loads(recognizer.FinalResult())

            transcript = result.get("text", "").strip()
            if transcript:
                logging.info(
                    "📝 STT RESULT - Vosk transcript: '%s' (length: %s)",
                    transcript,
                    len(transcript),
                )
            else:
                logging.debug("📝 STT RESULT - Vosk transcript empty")
            return transcript

        except Exception as exc:
            logging.error("STT processing failed: %s", exc, exc_info=True)
            return ""

    async def process_llm(self, prompt: str) -> str:
        """Run LLM inference using the prepared Phi-style prompt.
        
        Uses a lock to serialize inference calls - llama-cpp is NOT thread-safe
        and will segfault if multiple threads try to use the model simultaneously.
        """
        # Acquire lock to prevent concurrent LLM calls (causes segfault in libggml)
        async with self._llm_lock:
            try:
                if not self.llm_model:
                    logging.warning("LLM model not loaded, using fallback")
                    return "I'm here to help you. How can I assist you today?"

                loop = asyncio.get_running_loop()
                started = loop.time()
                output = await asyncio.to_thread(
                    self.llm_model,
                    prompt,
                    max_tokens=self.llm_max_tokens,
                    stop=self.llm_stop_tokens,
                    echo=False,
                    temperature=self.llm_temperature,
                    top_p=self.llm_top_p,
                    repeat_penalty=self.llm_repeat_penalty,
                )

                choices = output.get("choices", []) if isinstance(output, dict) else []
                if not choices:
                    logging.warning("🤖 LLM RESULT - No choices returned, using fallback response")
                    return "I'm here to help you. How can I assist you today?"

                response = choices[0].get("text", "").strip()
                latency_ms = round((loop.time() - started) * 1000.0, 2)
                logging.info(
                    "🤖 LLM RESULT - Completed in %s ms tokens=%s",
                    latency_ms,
                    len(response.split()),
                )
                return response

            except Exception as exc:
                logging.error("LLM processing failed: %s", exc, exc_info=True)
                return "I'm here to help you. How can I assist you today?"

    def _count_prompt_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self.llm_model and hasattr(self.llm_model, "tokenize"):
            try:
                tokens = self.llm_model.tokenize(text.encode("utf-8"), add_bos=False)
                return len(tokens)
            except Exception as exc:  # pragma: no cover - defensive guard
                logging.debug("Tokenization failed, falling back to whitespace split: %s", exc)
        return len(text.split())

    def _build_phi_prompt(self, user_text: str) -> str:
        user_text = (user_text or "").strip()
        segments = ["<|system|>", self.llm_system_prompt.strip(), "<|user|>"]
        segments.append(user_text if user_text else "Hello")
        segments.append("<|assistant|>")
        return "\n".join(segments) + "\n"

    @staticmethod
    def _strip_leading_bos(prompt: str) -> str:
        if not prompt:
            return prompt
        cleaned = prompt.lstrip()
        for marker in ("<s>", "<|bos|>"):
            while cleaned.startswith(marker):
                cleaned = cleaned[len(marker):].lstrip()
        return cleaned

    def _prepare_llm_prompt(
        self, session: SessionContext, new_turn: str
    ) -> Tuple[str, int, bool, int]:
        """Append a user turn, trim history to fit context, and report token counts."""
        candidate_turns = list(session.llm_user_turns) + [new_turn]
        raw_user_text = "\n\n".join(candidate_turns).strip()
        raw_prompt = self._build_phi_prompt(raw_user_text)
        raw_tokens = self._count_prompt_tokens(raw_prompt)

        max_prompt_tokens = max(self.llm_context - self.llm_max_tokens - 64, 128)
        trimmed_turns = list(candidate_turns)
        truncated = False
        while trimmed_turns and self._count_prompt_tokens(
            self._build_phi_prompt("\n\n".join(trimmed_turns).strip())
        ) > max_prompt_tokens:
            trimmed_turns.pop(0)
            truncated = True

        trimmed_user_text = "\n\n".join(trimmed_turns).strip()
        prompt_text = self._build_phi_prompt(trimmed_user_text)
        prompt_text = self._strip_leading_bos(prompt_text)
        prompt_tokens = self._count_prompt_tokens(prompt_text)
        session.llm_user_turns = trimmed_turns
        return prompt_text, prompt_tokens, truncated, raw_tokens

    async def process_tts(self, text: str) -> bytes:
        """Process TTS with 8kHz uLaw generation - routes to appropriate backend."""
        if self.tts_backend == "kokoro":
            return await self._process_tts_kokoro(text)
        else:
            return await self._process_tts_piper(text)

    async def _process_tts_piper(self, text: str) -> bytes:
        """Process TTS using Piper backend (22kHz output)."""
        try:
            if not self.tts_model:
                logging.error("Piper TTS model not loaded")
                return b""

            logging.debug("🔊 TTS INPUT - Generating 22kHz audio for: '%s'", text)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name

            # Write WAV data either by letting Piper stream into the wave writer
            # or by consuming a generator for backward compatibility.
            with wave.open(wav_path, "wb") as wav_file:
                # Mono, 16-bit, 22.05 kHz (typical Piper voice rate)
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                try:
                    # Newer Piper API: synthesize(text, wav_file)
                    self.tts_model.synthesize(text, wav_file)
                except TypeError:
                    # Fallback: older API returns a generator of frames
                    audio_generator = self.tts_model.synthesize(text)
                    for chunk in audio_generator:
                        if isinstance(chunk, (bytes, bytearray)):
                            wav_file.writeframes(chunk)
                        else:
                            data = getattr(chunk, "audio_int16_bytes", None)
                            if data:
                                wav_file.writeframes(data)

            with open(wav_path, "rb") as wav_file:
                wav_data = wav_file.read()

            ulaw_data = await asyncio.to_thread(
                self.audio_processor.convert_to_ulaw_8k, wav_data, 22050
            )
            os.unlink(wav_path)

            logging.info("🔊 TTS RESULT - Piper generated uLaw 8kHz audio: %s bytes", len(ulaw_data))
            return ulaw_data

        except Exception as exc:
            logging.error("Piper TTS processing failed: %s", exc, exc_info=True)
            return b""

    async def _process_tts_kokoro(self, text: str) -> bytes:
        """Process TTS using Kokoro backend (24kHz output)."""
        try:
            if self.kokoro_mode == "api":
                return await self._process_tts_kokoro_api(text)

            if not self.kokoro_backend:
                logging.error("Kokoro TTS backend not initialized")
                return b""

            logging.debug("🔊 TTS INPUT - Generating 24kHz audio for: '%s'", text)

            # Get PCM16 audio at 24kHz from Kokoro
            pcm16_data = self.kokoro_backend.synthesize(text)
            
            if not pcm16_data:
                logging.warning("⚠️ Kokoro returned empty audio")
                return b""

            # Write to temp WAV file for conversion
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = wav_file.name

            with wave.open(wav_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(pcm16_data)

            with open(wav_path, "rb") as wav_file:
                wav_data = wav_file.read()

            # Convert 24kHz WAV to 8kHz uLaw
            ulaw_data = await asyncio.to_thread(
                self.audio_processor.convert_to_ulaw_8k, wav_data, 24000
            )
            os.unlink(wav_path)

            logging.info("🔊 TTS RESULT - Kokoro generated uLaw 8kHz audio: %s bytes", len(ulaw_data))
            return ulaw_data

        except Exception as exc:
            logging.error("Kokoro TTS processing failed: %s", exc, exc_info=True)
            return b""

    def _kokoro_api_speech_request(self, text: str) -> bytes:
        """Blocking HTTP request to Kokoro Web API (OpenAI-compatible audio/speech)."""
        base_url = self.kokoro_api_base_url.rstrip("/")
        url = f"{base_url}/audio/speech"

        payload = {
            "model": self.kokoro_api_model,
            "voice": self.kokoro_voice,
            "input": text,
            # Request WAV so we can feed it directly into sox for ulaw conversion.
            "response_format": "wav",
            "speed": 1.0,
        }

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        }

        # OpenAPI shows bearerAuth but examples use "no-key"; treat token as optional.
        token = self.kokoro_api_key.strip() if self.kokoro_api_key else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = "Bearer no-key"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            raise RuntimeError(
                f"Kokoro Web API HTTP {e.code}: {body[:500].decode('utf-8', errors='ignore')}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Kokoro Web API request failed: {e}") from e

    async def _process_tts_kokoro_api(self, text: str) -> bytes:
        """Process TTS using Kokoro Web API, returning 8kHz µ-law bytes."""
        try:
            if not self.kokoro_api_base_url:
                logging.error("Kokoro API mode selected but KOKORO_API_BASE_URL is empty")
                return b""

            logging.debug(
                "🔊 TTS INPUT - Kokoro API (%s) voice=%s text='%s'",
                self.kokoro_api_base_url,
                self.kokoro_voice,
                text,
            )

            wav_data = await asyncio.to_thread(self._kokoro_api_speech_request, text)
            if not wav_data:
                return b""

            ulaw_data = await asyncio.to_thread(
                self.audio_processor.convert_to_ulaw_8k, wav_data, 24000
            )
            logging.info(
                "🔊 TTS RESULT - Kokoro API generated uLaw 8kHz audio: %s bytes",
                len(ulaw_data),
            )
            return ulaw_data
        except Exception as exc:
            logging.error("Kokoro API TTS processing failed: %s", exc, exc_info=True)
            return b""

    def _cancel_idle_timer(self, session: SessionContext) -> None:
        if session.idle_task and not session.idle_task.done():
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None
            if session.idle_task is not current_task:
                session.idle_task.cancel()
        session.idle_task = None

    def _reset_stt_session(self, session: SessionContext, last_text: str = "") -> None:
        """Clear recognizer state after emitting a final transcript."""
        self._cancel_idle_timer(session)
        session.recognizer = None
        session.last_partial = ""
        session.partial_emitted = False
        session.audio_buffer = b""
        session.last_request_meta.clear()
        session.last_final_text = last_text
        session.last_final_norm = _normalize_text(last_text)
        session.last_final_at = monotonic()
        # Note: Kroko WebSocket is kept open for session reuse, closed on disconnect

    async def _close_kroko_session(self, session: SessionContext) -> None:
        """Close Kroko WebSocket connection for a session."""
        if session.kroko_ws and self.kroko_backend:
            await self.kroko_backend.close(session.kroko_ws)
            session.kroko_ws = None
            session.kroko_connected = False

    def _ensure_stt_recognizer(self, session: SessionContext) -> Optional[KaldiRecognizer]:
        if not self.stt_model:
            logging.error("STT model not loaded")
            return None
        if KaldiRecognizer is None:
            logging.error("Vosk STT backend unavailable (vosk not installed)")
            return None

        if session.recognizer is None:
            session.recognizer = KaldiRecognizer(self.stt_model, PCM16_TARGET_RATE)
            session.last_partial = ""
            session.partial_emitted = False
        return session.recognizer

    async def _process_stt_stream(
        self,
        session: SessionContext,
        audio_data: bytes,
        input_rate: int,
    ) -> List[Dict[str, Any]]:
        """Feed audio into the session recognizer and return transcript updates."""
        # Route to appropriate backend
        if self.stt_backend == "kroko":
            return await self._process_stt_stream_kroko(session, audio_data, input_rate)
        elif self.stt_backend == "sherpa":
            return await self._process_stt_stream_sherpa(session, audio_data, input_rate)
        else:
            return await self._process_stt_stream_vosk(session, audio_data, input_rate)

    async def _process_stt_stream_kroko(
        self,
        session: SessionContext,
        audio_data: bytes,
        input_rate: int,
    ) -> List[Dict[str, Any]]:
        """Feed audio into Kroko ASR and return transcript updates."""
        if not self.kroko_backend:
            logging.error("Kroko backend not initialized")
            return []

        # Resample to 16kHz if needed
        if input_rate != PCM16_TARGET_RATE:
            audio_bytes = await asyncio.to_thread(
                self.audio_processor.resample_audio,
                audio_data,
                input_rate,
                PCM16_TARGET_RATE,
                "raw",
                "raw",
            )
        else:
            audio_bytes = audio_data

        updates: List[Dict[str, Any]] = []

        try:
            session.last_audio_at = asyncio.get_running_loop().time()
        except RuntimeError:
            session.last_audio_at = 0.0

        # Ensure Kroko WebSocket connection exists for this session
        if not session.kroko_connected or session.kroko_ws is None:
            try:
                session.kroko_ws = await self.kroko_backend.connect()
                session.kroko_connected = True
            except Exception as exc:
                logging.error("❌ KROKO - Failed to connect: %s", exc)
                return []

        # Send audio to Kroko
        try:
            await self.kroko_backend.send_audio(session.kroko_ws, audio_bytes)
        except Exception as exc:
            logging.error("❌ KROKO - Failed to send audio: %s", exc)
            session.kroko_connected = False
            return []

        # Try to receive transcript results (non-blocking)
        while True:
            result = await self.kroko_backend.receive_transcript(session.kroko_ws, timeout=0.05)
            if result is None:
                break

            result_type = result.get("type", "")
            text = (result.get("text") or "").strip()

            if result_type == "final":
                logging.info("📝 STT RESULT - Kroko final transcript: '%s'", text)
                updates.append({
                    "text": text,
                    "is_final": True,
                    "is_partial": False,
                    "confidence": None,
                })
            elif result_type == "partial":
                if text != session.last_partial:
                    session.last_partial = text
                    logging.debug("📝 STT PARTIAL - Kroko: '%s'", text)
                    updates.append({
                        "text": text,
                        "is_final": False,
                        "is_partial": True,
                        "confidence": None,
                    })

        return updates

    async def _process_stt_stream_sherpa(
        self,
        session: SessionContext,
        audio_data: bytes,
        input_rate: int,
    ) -> List[Dict[str, Any]]:
        """Feed audio into Sherpa-onnx and return transcript updates."""
        if not self.sherpa_backend:
            logging.error("Sherpa backend not initialized")
            return []

        # Resample to 16kHz if needed
        if input_rate != PCM16_TARGET_RATE:
            audio_bytes = await asyncio.to_thread(
                self.audio_processor.resample_audio,
                audio_data,
                input_rate,
                PCM16_TARGET_RATE,
                "raw",
                "raw",
            )
        else:
            audio_bytes = audio_data

        updates: List[Dict[str, Any]] = []

        try:
            session.last_audio_at = asyncio.get_running_loop().time()
        except RuntimeError:
            session.last_audio_at = 0.0

        # Ensure sherpa stream exists for this session
        if session.sherpa_stream is None:
            session.sherpa_stream = self.sherpa_backend.create_stream()
            if session.sherpa_stream is None:
                logging.error("❌ SHERPA - Failed to create stream")
                return []

        # Process audio and get results
        result = self.sherpa_backend.process_audio(session.sherpa_stream, audio_bytes)
        
        if result:
            result_type = result.get("type", "")
            text = (result.get("text") or "").strip()

            if result_type == "final":
                logging.info("📝 STT RESULT - Sherpa final transcript: '%s'", text)
                updates.append({
                    "text": text,
                    "is_final": True,
                    "is_partial": False,
                    "confidence": None,
                })
                session.last_partial = ""
            elif result_type == "partial":
                if text != session.last_partial:
                    session.last_partial = text
                    logging.debug("📝 STT PARTIAL - Sherpa: '%s'", text)
                    updates.append({
                        "text": text,
                        "is_final": False,
                        "is_partial": True,
                        "confidence": None,
                    })

        return updates

    async def _process_stt_stream_vosk(
        self,
        session: SessionContext,
        audio_data: bytes,
        input_rate: int,
    ) -> List[Dict[str, Any]]:
        """Feed audio into Vosk recognizer and return transcript updates."""
        recognizer = self._ensure_stt_recognizer(session)
        if not recognizer:
            return []

        if input_rate != PCM16_TARGET_RATE:
            logging.debug(
                "🎵 STT INPUT - Resampling %s Hz → %s Hz: %s bytes",
                input_rate,
                PCM16_TARGET_RATE,
                len(audio_data),
            )
            audio_bytes = await asyncio.to_thread(
                self.audio_processor.resample_audio,
                audio_data,
                input_rate,
                PCM16_TARGET_RATE,
                "raw",
                "raw",
            )
        else:
            audio_bytes = audio_data

        updates: List[Dict[str, Any]] = []

        try:
            session.last_audio_at = asyncio.get_running_loop().time()
        except RuntimeError:
            session.last_audio_at = 0.0
        
        # Calculate RMS to detect silent audio (only in debug mode)
        if DEBUG_AUDIO_FLOW:
            try:
                import struct
                import math
                samples = struct.unpack(f"{len(audio_bytes)//2}h", audio_bytes)
                squared_sum = sum(s*s for s in samples)
                rms = math.sqrt(squared_sum / len(samples)) if samples else 0
                logging.debug(
                    "🎤 FEEDING VOSK call_id=%s bytes=%d samples=%d rms=%.2f",
                    session.call_id or "unknown",
                    len(audio_bytes),
                    len(samples),
                    rms,
                )
            except Exception as rms_exc:
                logging.debug("RMS calculation failed: %s", rms_exc)

        try:
            has_final = recognizer.AcceptWaveform(audio_bytes)
            if DEBUG_AUDIO_FLOW:
                logging.debug(
                    "🎤 VOSK PROCESSED call_id=%s has_final=%s",
                    session.call_id or "unknown",
                    has_final,
                )
        except Exception as exc:  # pragma: no cover - defensive guard
            logging.error("STT recognition failed: %s", exc, exc_info=True)
            return updates

        if has_final:
            try:
                result = json.loads(recognizer.Result() or "{}")
            except json.JSONDecodeError:
                result = {}
            text = (result.get("text") or "").strip()
            confidence = result.get("confidence")
            logging.info(
                "📝 STT RESULT - Vosk final transcript: '%s'",
                text,
            )
            updates.append(
                {
                    "text": text,
                    "is_final": True,
                    "is_partial": False,
                    "confidence": confidence,
                }
            )
            return updates

        # Emit partial result to mirror remote streaming providers.
        try:
            partial_payload = json.loads(recognizer.PartialResult() or "{}")
        except json.JSONDecodeError:
            partial_payload = {}
        partial_text = (partial_payload.get("partial") or "").strip()
        if partial_text != session.last_partial or not session.partial_emitted:
            session.last_partial = partial_text
            session.partial_emitted = True
            logging.debug(
                "📝 STT PARTIAL - '%s'",
                partial_text,
            )
            updates.append(
                {
                    "text": partial_text,
                    "is_final": False,
                    "is_partial": True,
                    "confidence": None,
                }
            )

        return updates

    def _normalize_mode(self, data_mode: Optional[str], session: SessionContext) -> str:
        if data_mode and data_mode in SUPPORTED_MODES:
            session.mode = data_mode
            return data_mode
        return session.mode

    async def _send_json(self, websocket, payload: Dict[str, Any]) -> bool:
        try:
            await websocket.send(json.dumps(payload))
            return True
        except ConnectionClosed:
            logging.warning(
                "🌐 WS CLOSED - Failed to send JSON payload type=%s", payload.get("type")
            )
            return False

    async def _send_bytes(self, websocket, data: bytes) -> bool:
        if not data:
            return True
        try:
            await websocket.send(data)
            return True
        except ConnectionClosed:
            logging.warning("🌐 WS CLOSED - Failed to send binary payload (%s bytes)", len(data))
            return False

    async def _emit_stt_result(
        self,
        websocket,
        transcript: str,
        session: SessionContext,
        request_id: Optional[str],
        *,
        source_mode: str,
        is_final: bool,
        is_partial: bool,
        confidence: Optional[float],
    ) -> bool:
        payload = {
            "type": "stt_result",
            "text": transcript,
            "call_id": session.call_id,
            "mode": source_mode,
            "is_final": is_final,
            "is_partial": is_partial,
        }
        if confidence is not None:
            payload["confidence"] = confidence
        if request_id:
            payload["request_id"] = request_id
        return await self._send_json(websocket, payload)

    def _strip_tool_calls_for_tts(self, text: str) -> str:
        """
        Strip tool call markup from text before TTS to avoid speaking tags.
        Returns the clean spoken text without <tool_call>...</tool_call> blocks.
        """
        import re
        if not text:
            return ""
        
        # Remove <tool_call>...</tool_call> blocks (including newlines inside)
        clean = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Also handle potential JSON tool calls without tags
        # e.g., {"name": "hangup_call", ...}
        clean = re.sub(r'\{["\']name["\']\s*:\s*["\'](?:hangup_call|transfer|request_transcript)["\'].*?\}', '', clean, flags=re.DOTALL)
        
        # Clean up extra whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        if clean != text.strip():
            logging.info("🔇 TTS - Stripped tool call markup: original=%d chars, clean=%d chars", 
                        len(text), len(clean))
        
        return clean

    async def _emit_llm_response(
        self,
        websocket,
        llm_response: str,
        session: SessionContext,
        request_id: Optional[str],
        *,
        source_mode: str,
    ) -> bool:
        text = (llm_response or "").strip()
        if not text:
            logging.info(
                "🤖 LLM RESULT - Empty response, using fallback call_id=%s mode=%s",
                session.call_id,
                source_mode,
            )
            text = "I'm here to help you."
        payload = {
            "type": "llm_response",
            "text": text,
            "call_id": session.call_id,
            "mode": source_mode,
        }
        if request_id:
            payload["request_id"] = request_id
        return await self._send_json(websocket, payload)

    async def _emit_tts_audio(
        self,
        websocket,
        audio_bytes: bytes,
        session: SessionContext,
        request_id: Optional[str],
        *,
        source_mode: str,
    ) -> None:
        if request_id:
            # Milestone7: emit metadata event for selective TTS while keeping binary transport.
            metadata = {
                "type": "tts_audio",
                "call_id": session.call_id,
                "mode": source_mode,
                "request_id": request_id,
                "encoding": "mulaw",
                "sample_rate_hz": ULAW_SAMPLE_RATE,
                "byte_length": len(audio_bytes or b""),
            }
            if not await self._send_json(websocket, metadata):
                return
        if audio_bytes:
            await self._send_bytes(websocket, audio_bytes)

    async def _handle_final_transcript(
        self,
        websocket,
        session: SessionContext,
        request_id: Optional[str],
        *,
        mode: str,
        text: str,
        confidence: Optional[float],
        idle_promoted: bool = False,
    ) -> None:
        clean_text = (text or "").strip()
        normalized_text = _normalize_text(clean_text)
        last_final_text = session.last_final_text
        last_final_norm = session.last_final_norm
        last_final_at = session.last_final_at
        recent_empty = (
            last_final_text == ""
            and last_final_at > 0.0
            and monotonic() - last_final_at < 0.5
        )
        if not clean_text:
            reason = "idle-timeout" if idle_promoted else "recognizer-final"
            if mode == "stt":
                if recent_empty or (idle_promoted and last_final_text == ""):
                    logging.info(
                        "📝 STT FINAL SUPPRESSED - Repeated empty transcript call_id=%s mode=%s",
                        session.call_id,
                        mode,
                    )
                    return
                # For STT mode, emit an empty final so the engine adapter can complete cleanly.
                logging.info(
                    "📝 STT FINAL - Emitting empty transcript call_id=%s mode=%s reason=%s",
                    session.call_id,
                    mode,
                    reason,
                )
                if await self._emit_stt_result(
                    websocket,
                    "",
                    session,
                    request_id,
                    source_mode=mode,
                    is_final=True,
                    is_partial=False,
                    confidence=confidence,
                ):
                    self._reset_stt_session(session, "")
                return
            # For llm/full modes, continue suppressing empty finals to avoid downstream work
            logging.info(
                "📝 STT FINAL SUPPRESSED - Empty transcript call_id=%s mode=%s reason=%s",
                session.call_id,
                mode,
                reason,
            )
            return

        if idle_promoted and normalized_text and normalized_text == last_final_norm:
            logging.info(
                "📝 STT FINAL SUPPRESSED - Duplicate idle transcript call_id=%s mode=%s text=%s",
                session.call_id,
                mode,
                clean_text[:80],
            )
            return

        reason = "idle-timeout" if idle_promoted else "recognizer-final"
        logging.info(
            "📝 STT FINAL - Emitting transcript call_id=%s mode=%s reason=%s confidence=%s preview=%s",
            session.call_id,
            mode,
            reason,
            confidence,
            clean_text[:80],
        )

        stt_sent = await self._emit_stt_result(
            websocket,
            clean_text,
            session,
            request_id,
            source_mode=mode,
            is_final=True,
            is_partial=False,
            confidence=confidence,
        )

        if stt_sent:
            self._reset_stt_session(session, clean_text)

        if mode == "stt":
            return

        # LLM path for llm/full modes: instrument, guard with timeout, and fallback on failure
        if normalized_text and session.llm_user_turns:
            last_turn_norm = _normalize_text(session.llm_user_turns[-1])
            if normalized_text == last_turn_norm:
                logging.info(
                    "🧠 LLM SKIPPED - Duplicate final transcript call_id=%s mode=%s text=%s",
                    session.call_id,
                    mode,
                    clean_text[:80],
                )
                return

        prompt_text, prompt_tokens, truncated, raw_tokens = self._prepare_llm_prompt(
            session, clean_text
        )
        logging.info(
            "🧠 LLM PROMPT - call_id=%s tokens=%s raw_tokens=%s max_ctx=%s turns=%s truncated=%s preview=%s",
            session.call_id,
            prompt_tokens,
            raw_tokens,
            self.llm_context,
            len(session.llm_user_turns),
            truncated,
            prompt_text[:120],
        )

        infer_timeout = float(os.getenv("LOCAL_LLM_INFER_TIMEOUT_SEC", "20.0"))
        try:
            logging.info(
                "🧠 LLM START - Generating response call_id=%s mode=%s preview=%s",
                session.call_id,
                mode,
                prompt_text[:80],
            )
            llm_response = await asyncio.wait_for(
                self.process_llm(prompt_text), timeout=infer_timeout
            )
        except asyncio.TimeoutError:
            logging.warning(
                "🧠 LLM TIMEOUT - Using fallback call_id=%s mode=%s timeout=%.1fs",
                session.call_id,
                mode,
                infer_timeout,
            )
            llm_response = "I'm here to help you. Could you please repeat that?"
        except Exception as exc:
            logging.error(
                "🧠 LLM ERROR - Using fallback call_id=%s mode=%s error=%s",
                session.call_id,
                mode,
                str(exc),
                exc_info=True,
            )
            llm_response = "I'm here to help you. Could you please repeat that?"

        if not await self._emit_llm_response(
            websocket,
            llm_response,
            session,
            request_id,
            source_mode=mode if mode != "full" else "llm",
        ):
            return

        if mode == "full" and llm_response:
            # Strip tool call markup before TTS to avoid speaking <tool_call>...</tool_call>
            tts_text = self._strip_tool_calls_for_tts(llm_response)
            if tts_text:
                audio_response = await self.process_tts(tts_text)
            else:
                audio_response = b""  # No spoken text, just tool call
            await self._emit_tts_audio(
                websocket,
                audio_response,
                session,
                request_id,
                source_mode="full",
            )

    def _schedule_idle_finalizer(
        self,
        websocket,
        session: SessionContext,
        request_id: Optional[str],
        mode: str,
    ) -> None:
        self._cancel_idle_timer(session)
        session.last_request_meta = {"mode": mode, "request_id": request_id}

        async def _idle_promote() -> None:
            try:
                timeout_sec = max(self.buffer_timeout_ms / 1000.0, 0.1)
                await asyncio.sleep(timeout_sec)
                recognizer = session.recognizer
                if recognizer is None:
                    return
                try:
                    result = json.loads(recognizer.FinalResult() or "{}")
                except json.JSONDecodeError:
                    result = {}
                text = (result.get("text") or "").strip()
                confidence = result.get("confidence")
                logging.info(
                    "📝 STT IDLE FINALIZER - Triggering final after %s ms silence call_id=%s mode=%s preview=%s",
                    self.buffer_timeout_ms,
                    session.call_id,
                    mode,
                    text[:80],
                )
                await self._handle_final_transcript(
                    websocket,
                    session,
                    request_id,
                    mode=mode,
                    text=text,
                    confidence=confidence,
                    idle_promoted=True,
                )
            except asyncio.CancelledError:
                return
            finally:
                session.idle_task = None

        session.idle_task = asyncio.create_task(_idle_promote())

    async def _handle_audio_payload(
        self,
        websocket,
        session: SessionContext,
        data: Dict[str, Any],
        *,
        incoming_bytes: Optional[bytes] = None,
    ) -> None:
        """
        Decode audio payload and route it through the pipeline according to the requested mode.
        """
        mode = self._normalize_mode(data.get("mode"), session)
        request_id = data.get("request_id")
        call_id = data.get("call_id")
        if call_id:
            session.call_id = call_id
        
        if DEBUG_AUDIO_FLOW:
            logging.debug(
                "🎤 AUDIO PAYLOAD RECEIVED call_id=%s mode=%s request_id=%s",
                call_id or "unknown",
                mode,
                request_id or "none",
            )

        if incoming_bytes is None:
            encoded_audio = data.get("data", "")
            if not encoded_audio:
                logging.warning("Audio payload missing 'data'")
                return
            try:
                audio_bytes = base64.b64decode(encoded_audio)
                if DEBUG_AUDIO_FLOW:
                    logging.debug(
                        "🎤 AUDIO DECODED call_id=%s bytes=%d base64_len=%d",
                        call_id or "unknown",
                        len(audio_bytes),
                        len(encoded_audio),
                    )
            except Exception as exc:
                logging.warning("Failed to decode base64 audio payload: %s", exc)
                return
        else:
            audio_bytes = incoming_bytes
            logging.info(
                "🎤 AUDIO (binary) call_id=%s bytes=%d",
                call_id or "unknown",
                len(audio_bytes),
            )

        if not audio_bytes:
            logging.debug("Audio payload empty after decoding")
            return

        input_rate = int(data.get("rate", PCM16_TARGET_RATE))
        if DEBUG_AUDIO_FLOW:
            logging.debug(
                "🎤 ROUTING TO STT call_id=%s mode=%s bytes=%d rate=%d",
                call_id or "unknown",
                mode,
                len(audio_bytes),
                input_rate,
            )

        stt_modes = {"stt", "llm", "full"}
        if mode in stt_modes:
            session.last_request_meta = {"mode": mode, "request_id": request_id}
            stt_events = await self._process_stt_stream(session, audio_bytes, input_rate)

            final_emitted = False
            partial_seen = False

            for event in stt_events:
                text = event.get("text", "")
                confidence = event.get("confidence")
                if event.get("is_partial"):
                    partial_seen = True
                    await self._emit_stt_result(
                        websocket,
                        text,
                        session,
                        request_id,
                        source_mode=mode,
                        is_final=False,
                        is_partial=True,
                        confidence=confidence,
                    )
                    continue

                if event.get("is_final"):
                    final_emitted = True
                    await self._handle_final_transcript(
                        websocket,
                        session,
                        request_id,
                        mode=mode,
                        text=text,
                        confidence=confidence,
                        idle_promoted=False,
                    )

            if final_emitted:
                return

            # No final yet; keep an idle finalizer running so short utterances resolve.
            if session.recognizer is not None or partial_seen:
                self._schedule_idle_finalizer(websocket, session, request_id, mode)
            return

        if mode == "tts":
            logging.warning("Received audio payload with mode=tts; expected text request. Skipping.")
            return

    async def _handle_tts_request(
        self,
        websocket,
        session: SessionContext,
        data: Dict[str, Any],
    ) -> None:
        text = data.get("text", "").strip()
        call_id = data.get("call_id", session.call_id)
        logging.info("📢 TTS request received call_id=%s text_preview=%s", call_id, text[:50] if text else "(empty)")
        if not text:
            logging.warning("TTS request missing 'text'")
            return

        mode = self._normalize_mode(data.get("mode"), session)
        if mode not in {"tts", "full"}:
            # Milestone7: allow callers to force binary TTS even outside default 'tts' mode.
            logging.debug("Overriding session mode to 'tts' for explicit TTS request")
            mode = "tts"

        request_id = data.get("request_id")
        call_id = data.get("call_id")
        if call_id:
            session.call_id = call_id

        audio_response = await self.process_tts(text)
        
        # Check if this is a direct TTS request (expects tts_response with base64)
        # vs streaming mode which uses binary frames
        response_format = data.get("response_format", "json")  # "json" or "binary"
        
        if response_format == "json" or data.get("type") == "tts_request":
            # Send JSON response with base64-encoded audio for direct TTS calls
            # This is what LocalProvider.text_to_speech expects
            audio_b64 = base64.b64encode(audio_response).decode("utf-8") if audio_response else ""
            response = {
                "type": "tts_response",
                "text": text,
                "call_id": session.call_id,
                "audio_data": audio_b64,
                "encoding": "mulaw",
                "sample_rate_hz": ULAW_SAMPLE_RATE,
                "byte_length": len(audio_response or b""),
            }
            if request_id:
                response["request_id"] = request_id
            await self._send_json(websocket, response)
            logging.info("📢 TTS response sent call_id=%s audio_bytes=%d", session.call_id, len(audio_response or b""))
        else:
            # Legacy binary streaming mode
            await self._emit_tts_audio(
                websocket,
                audio_response,
                session,
                request_id,
                source_mode=mode,
            )

    async def _handle_llm_request(
        self,
        websocket,
        session: SessionContext,
        data: Dict[str, Any],
    ) -> None:
        text = data.get("text", "").strip()
        if not text:
            logging.warning("LLM request missing 'text'")
            return

        mode = self._normalize_mode(data.get("mode"), session)
        request_id = data.get("request_id")
        call_id = data.get("call_id")
        if call_id:
            session.call_id = call_id

        logging.info(
            "🧠 LLM REQUEST - Received call_id=%s mode=%s preview=%s",
            session.call_id,
            mode or "llm",
            text[:80],
        )

        infer_timeout = float(os.getenv("LOCAL_LLM_INFER_TIMEOUT_SEC", "20.0"))
        try:
            logging.info(
                "🧠 LLM START - Generating response call_id=%s mode=%s",
                session.call_id,
                mode or "llm",
            )
            llm_response = await asyncio.wait_for(
                self.process_llm(text), timeout=infer_timeout
            )
        except asyncio.TimeoutError:
            logging.warning(
                "🧠 LLM TIMEOUT - Using fallback call_id=%s mode=%s timeout=%.1fs",
                session.call_id,
                mode or "llm",
                infer_timeout,
            )
            llm_response = "I'm here to help you. Could you please repeat that?"
        except Exception as exc:
            logging.error(
                "🧠 LLM ERROR - Using fallback call_id=%s mode=%s error=%s",
                session.call_id,
                mode or "llm",
                str(exc),
                exc_info=True,
            )
            llm_response = "I'm here to help you. Could you please repeat that?"

        await self._emit_llm_response(
            websocket,
            llm_response,
            session,
            request_id,
            source_mode=mode or "llm",
        )

    async def _handle_json_message(self, websocket, session: SessionContext, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logging.warning("❓ Invalid JSON message: %s", message)
            return

        msg_type = data.get("type")
        if not msg_type:
            logging.warning("JSON payload missing 'type': %s", data)
            return

        # Optional auth gate.
        if msg_type == "auth":
            token = (data.get("auth_token") or data.get("token") or "").strip()
            call_id = data.get("call_id")
            if call_id:
                session.call_id = call_id
            if not self.ws_auth_token or token == self.ws_auth_token:
                session.authenticated = True
                await self._send_json(websocket, {"type": "auth_response", "status": "ok"})
                logging.info("🔐 WS AUTH - Authenticated session call_id=%s", session.call_id)
            else:
                await self._send_json(
                    websocket,
                    {
                        "type": "auth_response",
                        "status": "error",
                        "message": "invalid_auth_token",
                    },
                )
                logging.warning(
                    "🔐 WS AUTH - Invalid token call_id=%s", session.call_id
                )
            return

        if self.ws_auth_token and not session.authenticated:
            await self._send_json(
                websocket,
                {
                    "type": "auth_response",
                    "status": "error",
                    "message": "authentication_required",
                },
            )
            logging.warning(
                "🔐 WS AUTH - Message rejected before auth type=%s call_id=%s",
                msg_type,
                session.call_id,
            )
            return

        if msg_type == "set_mode":
            # Milestone7: allow clients to pre-select default mode for subsequent binary frames.
            requested = data.get("mode", DEFAULT_MODE)
            if requested in SUPPORTED_MODES:
                session.mode = requested
                logging.info("Session mode updated to %s", session.mode)
            else:
                logging.warning("Unsupported mode requested: %s", requested)
            call_id = data.get("call_id")
            if call_id:
                session.call_id = call_id
            response = {
                "type": "mode_ready",
                "mode": session.mode,
                "call_id": session.call_id,
            }
            await self._send_json(websocket, response)
            return

        if msg_type == "audio":
            await self._handle_audio_payload(websocket, session, data)
            return

        if msg_type == "tts_request":
            await self._handle_tts_request(websocket, session, data)
            return

        if msg_type == "llm_request":
            await self._handle_llm_request(websocket, session, data)
            return

        if msg_type == "reload_models":
            logging.info("🔄 RELOAD REQUEST - Hot reloading all models...")
            await self.reload_models()
            response = {
                "type": "reload_response",
                "status": "success",
                "message": "All models reloaded successfully",
            }
            await self._send_json(websocket, response)
            return

        if msg_type == "reload_llm":
            logging.info("🔄 LLM RELOAD REQUEST - Hot reloading LLM with optimizations...")
            requested_path = data.get("llm_model_path") or data.get("model_path")
            if requested_path:
                self.llm_model_path = requested_path
            await self.reload_llm_only()
            response = {
                "type": "reload_response",
                "status": "success",
                "message": (
                    "LLM model reloaded with optimizations (ctx="
                    f"{self.llm_context}, batch={self.llm_batch}, temp={self.llm_temperature}, "
                    f"max_tokens={self.llm_max_tokens})"
                ),
            }
            await self._send_json(websocket, response)
            return

        if msg_type == "switch_model":
            # Switch to a different model without container restart
            # Supported:
            # - STT: stt_backend, stt_model_path (vosk), sherpa_model_path, kroko_{embedded,port,language,url,model_path}
            # - LLM: llm_model_path
            # - TTS: tts_backend, tts_model_path (piper), kokoro_{voice,mode,model_path}
            logging.info("🔄 MODEL SWITCH REQUEST - Switching model configuration...")
            try:
                changed = []
                
                # STT backend switch
                if "stt_backend" in data:
                    new_backend = data["stt_backend"].lower()
                    if new_backend in ["vosk", "sherpa", "kroko"]:
                        self.stt_backend = new_backend
                        changed.append(f"stt_backend={new_backend}")
                
                # STT model path
                if "stt_model_path" in data:
                    stt_path = data["stt_model_path"]
                    # Route model path based on backend (explicit or current)
                    if self.stt_backend == "sherpa":
                        self.sherpa_model_path = stt_path
                        changed.append(f"sherpa_model_path={os.path.basename(stt_path)}")
                    elif self.stt_backend == "kroko":
                        self.kroko_model_path = stt_path
                        changed.append(f"kroko_model_path={os.path.basename(stt_path)}")
                    else:
                        self.stt_model_path = stt_path
                        changed.append(f"stt_model_path={os.path.basename(stt_path)}")

                if "sherpa_model_path" in data:
                    self.sherpa_model_path = data["sherpa_model_path"]
                    changed.append(
                        f"sherpa_model_path={os.path.basename(data['sherpa_model_path'])}"
                    )

                if "kroko_model_path" in data:
                    self.kroko_model_path = data["kroko_model_path"]
                    changed.append(
                        f"kroko_model_path={os.path.basename(data['kroko_model_path'])}"
                    )

                if "kroko_language" in data:
                    self.kroko_language = data["kroko_language"]
                    changed.append(f"kroko_language={data['kroko_language']}")

                if "kroko_url" in data:
                    self.kroko_url = data["kroko_url"]
                    changed.append("kroko_url=updated")

                if "kroko_port" in data:
                    try:
                        self.kroko_port = int(data["kroko_port"])
                        changed.append(f"kroko_port={self.kroko_port}")
                    except Exception:
                        pass

                if "kroko_embedded" in data:
                    raw = data["kroko_embedded"]
                    if isinstance(raw, str):
                        raw = raw.strip().lower() in ("1", "true", "yes", "y", "on")
                    self.kroko_embedded = bool(raw)
                    changed.append(
                        f"kroko_embedded={'1' if self.kroko_embedded else '0'}"
                    )
                
                # LLM model path
                if "llm_model_path" in data:
                    self.llm_model_path = data["llm_model_path"]
                    changed.append(f"llm_model_path={os.path.basename(data['llm_model_path'])}")
                
                # TTS backend switch
                if "tts_backend" in data:
                    new_backend = data["tts_backend"].lower()
                    if new_backend in ["piper", "kokoro"]:
                        self.tts_backend = new_backend
                        changed.append(f"tts_backend={new_backend}")
                
                # TTS model path
                if "tts_model_path" in data:
                    if self.tts_backend == "piper":
                        self.tts_model_path = data["tts_model_path"]
                        changed.append(
                            f"tts_model_path={os.path.basename(data['tts_model_path'])}"
                        )
                    else:
                        # Back-compat: if someone sends tts_model_path while using kokoro, treat it as kokoro_model_path.
                        self.kokoro_model_path = data["tts_model_path"]
                        changed.append(
                            f"kokoro_model_path={os.path.basename(data['tts_model_path'])}"
                        )
                
                # Kokoro settings
                if "kokoro_voice" in data:
                    self.kokoro_voice = data["kokoro_voice"]
                    changed.append(f"kokoro_voice={data['kokoro_voice']}")

                if "kokoro_mode" in data:
                    self.kokoro_mode = (data["kokoro_mode"] or "local").strip().lower()
                    changed.append(f"kokoro_mode={self.kokoro_mode}")

                if "kokoro_model_path" in data:
                    self.kokoro_model_path = data["kokoro_model_path"]
                    changed.append(
                        f"kokoro_model_path={os.path.basename(data['kokoro_model_path'])}"
                    )

                if "kokoro_api_base_url" in data:
                    self.kokoro_api_base_url = (data["kokoro_api_base_url"] or "").strip()
                    changed.append("kokoro_api_base_url=updated")

                if "kokoro_api_key" in data:
                    self.kokoro_api_key = (data["kokoro_api_key"] or "").strip()
                    changed.append("kokoro_api_key=updated")

                if "kokoro_api_model" in data:
                    self.kokoro_api_model = (data["kokoro_api_model"] or "model").strip()
                    changed.append(f"kokoro_api_model={self.kokoro_api_model}")
                
                if changed:
                    logging.info("📝 Configuration updated: %s", ", ".join(changed))
                    # Reload affected models
                    await self.reload_models()
                    response = {
                        "type": "switch_response",
                        "status": "success",
                        "message": f"Models switched and reloaded: {', '.join(changed)}",
                        "changed": changed,
                    }
                else:
                    response = {
                        "type": "switch_response",
                        "status": "no_change",
                        "message": "No valid model parameters provided",
                    }
            except Exception as e:
                logging.error("❌ Model switch failed: %s", e)
                response = {
                    "type": "switch_response",
                    "status": "error",
                    "message": str(e),
                }
            await self._send_json(websocket, response)
            return

        if msg_type == "status":
            # Determine STT loaded state based on active backend
            stt_loaded = False
            stt_path = None
            stt_display = None
            if self.stt_backend == "vosk":
                stt_loaded = self.stt_model is not None
                stt_path = self.stt_model_path
                stt_display = os.path.basename(self.stt_model_path)
            elif self.stt_backend == "kroko":
                stt_loaded = self.kroko_backend is not None
                stt_path = self.kroko_model_path if self.kroko_embedded else self.kroko_url
                stt_display = (
                    f"Kroko (embedded, port {self.kroko_port})"
                    if self.kroko_embedded
                    else f"Kroko ({self.kroko_language})"
                )
            elif self.stt_backend == "sherpa":
                stt_loaded = self.sherpa_backend is not None
                stt_path = self.sherpa_model_path
                stt_display = os.path.basename(self.sherpa_model_path)
            
            # Determine TTS loaded state based on active backend
            tts_loaded = False
            tts_path = None
            tts_display = None
            if self.tts_backend == "piper":
                tts_loaded = self.tts_model is not None
                tts_path = self.tts_model_path
                tts_display = os.path.basename(self.tts_model_path)
            elif self.tts_backend == "kokoro":
                if self.kokoro_mode == "api":
                    tts_loaded = bool(self.kokoro_api_base_url)
                    tts_path = self.kokoro_api_base_url
                    tts_display = f"Kokoro Web API ({self.kokoro_voice})"
                else:
                    tts_loaded = self.kokoro_backend is not None
                    tts_path = (
                        self.kokoro_model_path
                        if self.kokoro_mode != "hf"
                        else "hf://hexgrad/Kokoro-82M"
                    )
                    tts_display = f"Kokoro ({self.kokoro_voice})"

            llm_display = os.path.basename(self.llm_model_path)
            
            response = {
                "type": "status_response",
                "status": "ok",
                "stt_backend": self.stt_backend,
                "tts_backend": self.tts_backend,
                "models": {
                    "stt": {
                        "backend": self.stt_backend,
                        "loaded": stt_loaded,
                        "path": stt_path,
                        "display": stt_display,
                    },
                    "llm": {
                        "loaded": self.llm_model is not None,
                        "path": self.llm_model_path,
                        "display": llm_display,
                        "config": {
                            "context": self.llm_context,
                            "threads": self.llm_threads,
                            "batch": self.llm_batch,
                        }
                    },
                    "tts": {
                        "backend": self.tts_backend,
                        "loaded": tts_loaded,
                        "path": tts_path,
                        "display": tts_display,
                    }
                },
                "kroko": {
                    "embedded": self.kroko_embedded,
                    "port": self.kroko_port,
                    "language": self.kroko_language,
                    "url": self.kroko_url,
                    "model_path": self.kroko_model_path,
                },
                "kokoro": {
                    "mode": self.kokoro_mode,
                    "voice": self.kokoro_voice,
                    "model_path": self.kokoro_model_path,
                    "api_base_url": self.kokoro_api_base_url,
                    "api_key_set": bool(self.kokoro_api_key),
                },
                "config": {
                    "log_level": _level_name,
                    "debug_audio": DEBUG_AUDIO_FLOW,
                }
            }
            await self._send_json(websocket, response)
            return

        if msg_type == "capabilities":
            # Report what backends are available in this container
            capabilities = {
                "vosk": True,  # Always available (pure Python via vosk package)
                "sherpa": False,
                "kroko_embedded": False,
                "piper": True,  # Always available via piper-tts
                "kokoro": False,
                "llama": True,  # Always available via llama-cpp-python
            }
            
            # Check if sherpa-onnx is available
            try:
                import sherpa_onnx
                capabilities["sherpa"] = True
            except ImportError:
                pass
            
            # Check if Kroko binary is installed
            kroko_binary = "/usr/local/bin/kroko-server"
            if os.path.exists(kroko_binary):
                capabilities["kroko_embedded"] = True
            
            # Check if Kokoro is available
            try:
                import kokoro  # noqa: F401
                capabilities["kokoro"] = True
            except ImportError:
                # Kokoro can also be "available" in API mode (no local package needed)
                kokoro_mode = (os.environ.get("KOKORO_MODE", "local") or "local").strip().lower()
                if kokoro_mode == "api" and (os.environ.get("KOKORO_API_BASE_URL") or "").strip():
                    capabilities["kokoro"] = True
                else:
                    # As a fallback, treat an on-disk Kokoro model directory as "available"
                    kokoro_model_path = os.environ.get(
                        "KOKORO_MODEL_PATH", "/app/models/tts/kokoro"
                    )
                    if os.path.exists(kokoro_model_path):
                        capabilities["kokoro"] = True
            
            response = {
                "type": "capabilities_response",
                "capabilities": capabilities
            }
            await self._send_json(websocket, response)
            return

        logging.warning("❓ Unknown message type: %s", msg_type)

    async def _handle_binary_message(self, websocket, session: SessionContext, message: bytes) -> None:
        if self.ws_auth_token and not session.authenticated:
            await self._send_json(
                websocket,
                {
                    "type": "auth_response",
                    "status": "error",
                    "message": "authentication_required",
                },
            )
            logging.warning(
                "🔐 WS AUTH - Dropping binary audio before auth call_id=%s bytes=%d",
                session.call_id,
                len(message),
            )
            return
        logging.info("🎵 AUDIO INPUT - Received binary audio: %s bytes", len(message))
        await self._handle_audio_payload(
            websocket,
            session,
            data={"mode": session.mode},
            incoming_bytes=message,
        )

    async def handler(self, websocket):
        """Enhanced WebSocket handler with MVP pipeline and hot reloading"""
        logging.debug("🔌 New connection established: %s", websocket.remote_address)
        
        # SECURITY: Fail-closed for remote connections without auth token configured
        # If server is bound to non-localhost AND no auth token is set, reject all connections
        bind_host = os.getenv("LOCAL_WS_HOST", "127.0.0.1")
        is_remote_bind = bind_host not in ("127.0.0.1", "localhost", "::1")
        if is_remote_bind and not self.ws_auth_token:
            logging.error(
                "🚨 SECURITY: Rejecting connection - server bound to %s but LOCAL_WS_AUTH_TOKEN not set",
                bind_host
            )
            await websocket.close(1008, "Server misconfigured: auth token required for remote access")
            return
        
        session = SessionContext()
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self._handle_binary_message(websocket, session, message)
                else:
                    await self._handle_json_message(websocket, session, message)
        except ConnectionClosedError:
            # Expected when client disconnects without close frame (call ended)
            logging.debug("🔌 Client disconnected (no close frame)")
        except ConnectionClosedOK:
            # Normal close
            logging.debug("🔌 Client disconnected normally")
        except Exception as exc:
            logging.error("❌ WebSocket handler error: %s", exc, exc_info=True)
        finally:
            self._reset_stt_session(session)
            logging.debug("🔌 Connection closed: %s", websocket.remote_address)


async def main():
    """Main server function"""
    server = LocalAIServer()
    try:
        await server.initialize_models()

        # SECURITY: Default to localhost. Set LOCAL_WS_HOST=0.0.0.0 for remote access.
        # If binding non-localhost, LOCAL_WS_AUTH_TOKEN should be set (enforced in handler).
        host = os.getenv("LOCAL_WS_HOST", "127.0.0.1")
        port = int(os.getenv("LOCAL_WS_PORT", "8765"))
        
        # SECURITY: Fail-closed for non-localhost bind without auth token
        # Treat 0.0.0.0, ::, ::0, and any non-localhost as remote-accessible
        auth_token = os.getenv("LOCAL_WS_AUTH_TOKEN", "").strip()
        
        def is_loopback_address(addr: str) -> bool:
            """Check if address is loopback (127.0.0.0/8, localhost, ::1)"""
            if addr in ("localhost", "::1"):
                return True
            # Check IPv4 loopback range 127.0.0.0/8
            if addr.startswith("127."):
                return True
            return False
        
        is_loopback = is_loopback_address(host)
        
        if not is_loopback and not auth_token:
            logging.error(
                "🚨 SECURITY: LOCAL_WS_HOST=%s (non-loopback) but LOCAL_WS_AUTH_TOKEN is not set. "
                "Refusing to start - set LOCAL_WS_AUTH_TOKEN or bind to 127.0.0.1.",
                host
            )
            sys.exit(1)

        async with serve(
            server.handler,
            host,
            port,
            ping_interval=60,
            ping_timeout=120,
            max_size=None,
            origins=None,  # Allow connections from other containers/browsers
        ):
            logging.info("🚀 Enhanced Local AI Server started on ws://%s:%s", host, port)
            logging.info(
                "📋 Pipeline: ExternalMedia (8kHz) → STT (16kHz) → LLM → TTS (8kHz µ-law) "
                "| Supports selective STT/TTS modes"
            )
            await asyncio.Future()  # Run forever
    finally:
        await server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
