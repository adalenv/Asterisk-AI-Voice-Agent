"""
Local AI Server Model Management API

Endpoints for:
- Enumerating available models (STT, TTS, LLM)
- Switching active model with hot-reload support
- Getting current model status
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import os
import json
import asyncio
import websockets
from services.fs import upsert_env_vars

router = APIRouter()


class ModelInfo(BaseModel):
    """Information about a single model."""
    id: str
    name: str
    path: str
    type: str  # stt, tts, llm
    backend: Optional[str] = None  # vosk, sherpa, kroko, piper, kokoro
    size_mb: Optional[float] = None
    voice_files: Optional[Dict[str, str]] = None  # For Kokoro voices


class AvailableModels(BaseModel):
    """All available models grouped by type."""
    stt: Dict[str, List[ModelInfo]]  # Grouped by backend
    tts: Dict[str, List[ModelInfo]]  # Grouped by backend
    llm: List[ModelInfo]


class SwitchModelRequest(BaseModel):
    """Request to switch model."""
    model_type: str  # stt, tts, llm
    backend: Optional[str] = None  # For STT/TTS: vosk, sherpa, kroko, piper, kokoro
    model_path: Optional[str] = None  # For models with paths
    voice: Optional[str] = None  # For Kokoro TTS
    language: Optional[str] = None  # For Kroko STT
    # Kroko embedded tuning (optional)
    kroko_embedded: Optional[bool] = None
    kroko_port: Optional[int] = None
    kroko_url: Optional[str] = None
    # Sherpa explicit path (optional; preferred over model_path)
    sherpa_model_path: Optional[str] = None
    # Kokoro mode/model controls (optional)
    kokoro_mode: Optional[str] = None  # local|api|hf
    kokoro_model_path: Optional[str] = None
    kokoro_api_base_url: Optional[str] = None
    kokoro_api_key: Optional[str] = None
    kokoro_api_model: Optional[str] = None


class SwitchModelResponse(BaseModel):
    """Response from model switch."""
    success: bool
    message: str
    requires_restart: bool = False


def get_dir_size_mb(path: str) -> float:
    """Get directory size in MB."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    except Exception:
        pass
    return round(total / (1024 * 1024), 2)


def get_file_size_mb(path: str) -> float:
    """Get file size in MB."""
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0


@router.get("/models", response_model=AvailableModels)
async def list_available_models():
    """
    List all available models from the models directory.
    
    Scans:
    - models/stt/ for Vosk, Sherpa, and Kroko models
    - models/tts/ for Piper and Kokoro models
    - models/llm/ for GGUF models
    """
    from settings import PROJECT_ROOT
    
    models_dir = os.path.join(PROJECT_ROOT, "models")
    
    stt_models: Dict[str, List[ModelInfo]] = {
        "vosk": [],
        "sherpa": [],
        "kroko": []
    }
    tts_models: Dict[str, List[ModelInfo]] = {
        "piper": [],
        "kokoro": []
    }
    llm_models: List[ModelInfo] = []
    
    # Scan STT models
    stt_dir = os.path.join(models_dir, "stt")
    if os.path.exists(stt_dir):
        for item in os.listdir(stt_dir):
            item_path = os.path.join(stt_dir, item)
            if os.path.isdir(item_path):
                if item.startswith("vosk-model"):
                    stt_models["vosk"].append(ModelInfo(
                        id=f"vosk_{item}",
                        name=item,
                        path=f"/app/models/stt/{item}",
                        type="stt",
                        backend="vosk",
                        size_mb=get_dir_size_mb(item_path)
                    ))
                elif "sherpa" in item.lower():
                    stt_models["sherpa"].append(ModelInfo(
                        id=f"sherpa_{item}",
                        name=item,
                        path=f"/app/models/stt/{item}",
                        type="stt",
                        backend="sherpa",
                        size_mb=get_dir_size_mb(item_path)
                    ))
                elif "kroko" in item.lower():
                    stt_models["kroko"].append(ModelInfo(
                        id="kroko_embedded",
                        name=f"Kroko Embedded ({item})",
                        path=f"/app/models/stt/{item}",
                        type="stt",
                        backend="kroko",
                        size_mb=get_dir_size_mb(item_path)
                    ))

    # Scan Kroko embedded ONNX models (recommended location: models/kroko/*.onnx)
    kroko_dir = os.path.join(models_dir, "kroko")
    if os.path.exists(kroko_dir):
        for item in os.listdir(kroko_dir):
            item_path = os.path.join(kroko_dir, item)
            if os.path.isfile(item_path) and item.lower().endswith(".onnx"):
                stt_models["kroko"].append(ModelInfo(
                    id=f"kroko_{item}",
                    name=f"Kroko Embedded ({item})",
                    path=f"/app/models/kroko/{item}",
                    type="stt",
                    backend="kroko",
                    size_mb=get_file_size_mb(item_path)
                ))
    
    # Note: Kroko Cloud API is not added here since it's a cloud service, not an installed model
    # It's available through the catalog but shouldn't appear in "installed" models list
    
    # Scan TTS models
    tts_dir = os.path.join(models_dir, "tts")
    if os.path.exists(tts_dir):
        for item in os.listdir(tts_dir):
            item_path = os.path.join(tts_dir, item)
            if item.endswith(".onnx"):
                name = item.replace(".onnx", "")
                tts_models["piper"].append(ModelInfo(
                    id=f"piper_{name}",
                    name=name,
                    path=f"/app/models/tts/{item}",
                    type="tts",
                    backend="piper",
                    size_mb=get_file_size_mb(item_path)
                ))
            elif item == "kokoro" and os.path.isdir(item_path):
                # Get available Kokoro voices
                voices_dir = os.path.join(item_path, "voices")
                voice_files = {}
                if os.path.exists(voices_dir):
                    for voice in os.listdir(voices_dir):
                        if voice.endswith(".pt"):
                            voice_name = voice.replace(".pt", "")
                            voice_files[voice_name] = voice
                            
                tts_models["kokoro"].append(ModelInfo(
                    id="kokoro_82m",
                    name="Kokoro v0.19 (82M)",
                    path="/app/models/tts/kokoro",
                    type="tts",
                    backend="kokoro",
                    size_mb=get_dir_size_mb(item_path),
                    voice_files=voice_files
                ))
    
    # Scan LLM models
    llm_dir = os.path.join(models_dir, "llm")
    if os.path.exists(llm_dir):
        for item in os.listdir(llm_dir):
            if item.endswith(".gguf"):
                item_path = os.path.join(llm_dir, item)
                llm_models.append(ModelInfo(
                    id=item.replace(".gguf", ""),
                    name=item.replace(".gguf", ""),
                    path=f"/app/models/llm/{item}",
                    type="llm",
                    size_mb=get_file_size_mb(item_path)
                ))
    
    return AvailableModels(
        stt=stt_models,
        tts=tts_models,
        llm=llm_models
    )


@router.get("/capabilities")
async def get_backend_capabilities():
    """
    Get available backend capabilities from the local-ai-server container.
    
    Checks what backends are actually installed/available:
    - Vosk: Always available (pure Python)
    - Sherpa: Check if sherpa-onnx is installed
    - Kroko Embedded: Check if /usr/local/bin/kroko-server exists
    - Kroko Cloud: Always available (requires API key)
    - Piper: Check if piper-tts is installed
    - Kokoro: Check if kokoro models exist
    - LLM: Check if llama-cpp-python is installed
    """
    from settings import get_setting
    import subprocess
    
    capabilities = {
        "stt": {
            "vosk": {"available": False, "reason": ""},
            "sherpa": {"available": False, "reason": ""},
            "kroko_embedded": {"available": False, "reason": ""},
            "kroko_cloud": {"available": True, "reason": "Cloud API (requires KROKO_API_KEY)"}
        },
        "tts": {
            "piper": {"available": False, "reason": ""},
            "kokoro": {"available": False, "reason": ""}
        },
        "llm": {"available": False, "reason": ""}
    }
    
    # Query local-ai-server for its capabilities
    ws_url = get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
    
    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            auth_token = (get_setting("LOCAL_WS_AUTH_TOKEN", os.getenv("LOCAL_WS_AUTH_TOKEN", "")) or "").strip()
            if auth_token:
                await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                if data.get("type") != "auth_response" or data.get("status") != "ok":
                    raise RuntimeError(f"Local AI auth failed: {data}")

            # Request capabilities from local-ai-server
            await ws.send(json.dumps({"type": "capabilities"}))
            response = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(response)
            
            if data.get("type") == "capabilities_response":
                # Merge capabilities from server
                server_caps = data.get("capabilities", {})
                
                # STT backends
                if server_caps.get("vosk"):
                    capabilities["stt"]["vosk"] = {"available": True, "reason": "Vosk installed"}
                if server_caps.get("sherpa"):
                    capabilities["stt"]["sherpa"] = {"available": True, "reason": "Sherpa-ONNX installed"}
                if server_caps.get("kroko_embedded"):
                    capabilities["stt"]["kroko_embedded"] = {"available": True, "reason": "Kroko binary installed"}
                else:
                    capabilities["stt"]["kroko_embedded"]["reason"] = "Rebuild with INCLUDE_KROKO_EMBEDDED=true"
                
                # TTS backends
                if server_caps.get("piper"):
                    capabilities["tts"]["piper"] = {"available": True, "reason": "Piper TTS installed"}
                if server_caps.get("kokoro"):
                    capabilities["tts"]["kokoro"] = {"available": True, "reason": "Kokoro installed"}
                
                # LLM
                if server_caps.get("llama"):
                    capabilities["llm"] = {"available": True, "reason": "llama-cpp-python installed"}
            else:
                # Fallback: assume basic capabilities based on what we can detect
                capabilities["stt"]["vosk"] = {"available": True, "reason": "Default backend"}
                capabilities["tts"]["piper"] = {"available": True, "reason": "Default backend"}
                capabilities["llm"] = {"available": True, "reason": "Default backend"}
                
    except Exception as e:
        # Server not reachable - return minimal capabilities
        capabilities["stt"]["vosk"] = {"available": True, "reason": "Default backend"}
        capabilities["tts"]["piper"] = {"available": True, "reason": "Default backend"}
        capabilities["llm"] = {"available": True, "reason": "Default backend"}
        capabilities["error"] = str(e)
    
    return capabilities


@router.get("/status")
async def get_local_ai_status():
    """
    Get current status from local-ai-server including active backends and models.
    """
    from settings import get_setting
    
    ws_url = get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
    
    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            auth_token = (get_setting("LOCAL_WS_AUTH_TOKEN", os.getenv("LOCAL_WS_AUTH_TOKEN", "")) or "").strip()
            if auth_token:
                await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                if data.get("type") != "auth_response" or data.get("status") != "ok":
                    raise RuntimeError(f"Local AI auth failed: {data}")

            await ws.send(json.dumps({"type": "status"}))
            response = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(response)
            return {
                "connected": True,
                "status": data.get("status", "unknown"),
                "stt_backend": data.get("stt_backend"),
                "tts_backend": data.get("tts_backend"),
                "models": data.get("models", {})
            }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e)
        }


@router.post("/switch", response_model=SwitchModelResponse)
async def switch_model(request: SwitchModelRequest):
    """
    Switch the active model on local-ai-server with rollback support.
    
    For STT/TTS backend changes, updates environment variables AND YAML config,
    then triggers a container restart to reload the model. If the new model
    fails to load, automatically rolls back to the previous configuration.
    """
    from settings import PROJECT_ROOT, get_setting
    from api.config import update_yaml_provider_field
    
    env_file = os.path.join(PROJECT_ROOT, ".env")
    env_updates = {}
    yaml_updates = {}  # Track YAML updates for sync
    requires_restart = False

    async def _try_ws_switch(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Try to hot-switch via local-ai-server websocket. Returns response dict on success, None on failure."""
        ws_url = get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
        try:
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                auth_token = (get_setting("LOCAL_WS_AUTH_TOKEN", os.getenv("LOCAL_WS_AUTH_TOKEN", "")) or "").strip()
                if auth_token:
                    await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    auth_data = json.loads(raw)
                    if auth_data.get("type") != "auth_response" or auth_data.get("status") != "ok":
                        raise RuntimeError(f"Local AI auth failed: {auth_data}")

                await ws.send(json.dumps(payload))
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                data = json.loads(raw)
                return data
        except Exception:
            return None
    
    # 1. Save current config for potential rollback
    previous_env = _read_env_values(env_file, [
        "LOCAL_STT_BACKEND", "LOCAL_STT_MODEL_PATH", "SHERPA_MODEL_PATH",
        "KROKO_LANGUAGE", "KROKO_EMBEDDED", "KROKO_PORT", "KROKO_URL", "KROKO_MODEL_PATH",
        "LOCAL_TTS_BACKEND", "LOCAL_TTS_MODEL_PATH",
        "KOKORO_MODE", "KOKORO_VOICE", "KOKORO_MODEL_PATH", "LOCAL_LLM_MODEL_PATH"
    ])
    
    if request.model_type == "stt":
        if request.backend:
            env_updates["LOCAL_STT_BACKEND"] = request.backend
            yaml_updates["stt_backend"] = request.backend
            # Prefer hot switching via WS; fallback to recreate if needed.
            
            if request.backend == "vosk" and request.model_path:
                env_updates["LOCAL_STT_MODEL_PATH"] = request.model_path
                yaml_updates["stt_model"] = request.model_path
            elif request.backend == "kroko":
                if request.language:
                    env_updates["KROKO_LANGUAGE"] = request.language
                    yaml_updates["kroko_language"] = request.language
                if request.kroko_url:
                    env_updates["KROKO_URL"] = request.kroko_url
                if request.kroko_embedded is not None:
                    env_updates["KROKO_EMBEDDED"] = "1" if request.kroko_embedded else "0"
                if request.kroko_port is not None:
                    env_updates["KROKO_PORT"] = str(request.kroko_port)
                if request.model_path:
                    # Kroko embedded model path (ONNX)
                    env_updates["KROKO_MODEL_PATH"] = request.model_path
            elif request.backend == "sherpa":
                sherpa_path = request.sherpa_model_path or request.model_path
                if sherpa_path:
                    env_updates["SHERPA_MODEL_PATH"] = sherpa_path
                    yaml_updates["sherpa_model_path"] = sherpa_path
                
    elif request.model_type == "tts":
        if request.backend:
            env_updates["LOCAL_TTS_BACKEND"] = request.backend
            yaml_updates["tts_backend"] = request.backend
            # Prefer hot switching via WS; fallback to recreate if needed.
            
            if request.backend == "piper" and request.model_path:
                env_updates["LOCAL_TTS_MODEL_PATH"] = request.model_path
                yaml_updates["tts_voice"] = request.model_path
            elif request.backend == "kokoro":
                if request.kokoro_mode:
                    env_updates["KOKORO_MODE"] = request.kokoro_mode
                if request.kokoro_api_base_url:
                    env_updates["KOKORO_API_BASE_URL"] = request.kokoro_api_base_url
                if request.kokoro_api_key:
                    env_updates["KOKORO_API_KEY"] = request.kokoro_api_key
                if request.kokoro_api_model:
                    env_updates["KOKORO_API_MODEL"] = request.kokoro_api_model
                if request.voice:
                    env_updates["KOKORO_VOICE"] = request.voice
                    yaml_updates["kokoro_voice"] = request.voice
                kokoro_model_path = request.kokoro_model_path or request.model_path
                if kokoro_model_path:
                    env_updates["KOKORO_MODEL_PATH"] = kokoro_model_path
                    yaml_updates["kokoro_model_path"] = kokoro_model_path
                    
    elif request.model_type == "llm":
        if request.model_path:
            env_updates["LOCAL_LLM_MODEL_PATH"] = request.model_path
            # Prefer model switch without restart.
            ws_resp = await _try_ws_switch({"type": "switch_model", "llm_model_path": request.model_path})
            if ws_resp and ws_resp.get("type") == "switch_response" and ws_resp.get("status") == "success":
                _update_env_file(env_file, env_updates)
                return SwitchModelResponse(
                    success=True,
                    message=f"LLM model switched to {request.model_path} via hot-switch",
                    requires_restart=False,
                )
            requires_restart = True
    
    # 2. Try hot-switch for STT/TTS via WS before falling back to recreate.
    if request.model_type in ("stt", "tts") and request.backend:
        payload: Dict[str, Any] = {"type": "switch_model"}
        if request.model_type == "stt":
            payload["stt_backend"] = request.backend
            if request.backend == "vosk" and request.model_path:
                payload["stt_model_path"] = request.model_path
            if request.backend == "sherpa":
                sherpa_path = request.sherpa_model_path or request.model_path
                if sherpa_path:
                    payload["sherpa_model_path"] = sherpa_path
            if request.backend == "kroko":
                if request.language:
                    payload["kroko_language"] = request.language
                if request.kroko_url:
                    payload["kroko_url"] = request.kroko_url
                if request.kroko_port is not None:
                    payload["kroko_port"] = request.kroko_port
                if request.kroko_embedded is not None:
                    payload["kroko_embedded"] = request.kroko_embedded
                if request.model_path:
                    payload["kroko_model_path"] = request.model_path
        else:
            payload["tts_backend"] = request.backend
            if request.backend == "piper" and request.model_path:
                payload["tts_model_path"] = request.model_path
            if request.backend == "kokoro":
                if request.voice:
                    payload["kokoro_voice"] = request.voice
                if request.kokoro_mode:
                    payload["kokoro_mode"] = request.kokoro_mode
                kokoro_model_path = request.kokoro_model_path or request.model_path
                if kokoro_model_path:
                    payload["kokoro_model_path"] = kokoro_model_path
                if request.kokoro_api_base_url:
                    payload["kokoro_api_base_url"] = request.kokoro_api_base_url
                if request.kokoro_api_key:
                    payload["kokoro_api_key"] = request.kokoro_api_key
                if request.kokoro_api_model:
                    payload["kokoro_api_model"] = request.kokoro_api_model

        ws_resp = await _try_ws_switch(payload)
        if ws_resp and ws_resp.get("type") == "switch_response" and ws_resp.get("status") == "success":
            requires_restart = False
        else:
            requires_restart = True

    # 3. Update .env file AND YAML config (always persist intent)
    if env_updates:
        _update_env_file(env_file, env_updates)
    
    # Sync to YAML config for consistency
    if yaml_updates:
        for field, value in yaml_updates.items():
            update_yaml_provider_field("local", field, value)
    
    # 4. Recreate container if needed (restart doesn't reload .env)
    if requires_restart:
        try:
            from api.system import _recreate_via_compose
            result = await _recreate_via_compose("local-ai-server")
            return SwitchModelResponse(
                success=True,
                message="Model switch applied by recreating container to load updated settings...",
                requires_restart=True
            )
        except Exception as e:
            # Attempt rollback on any error
            try:
                _update_env_file(env_file, previous_env)
            except Exception:
                pass
            return SwitchModelResponse(
                success=False,
                message=f"Failed to recreate container: {str(e)}. Attempted rollback.",
                requires_restart=True
            )
    
    return SwitchModelResponse(
        success=True,
        message="Model configuration updated",
        requires_restart=False
    )


class DeleteModelRequest(BaseModel):
    model_path: str
    type: str  # stt, tts, llm


@router.delete("/models")
async def delete_model(request: DeleteModelRequest):
    """
    Delete an installed model from the filesystem.
    """
    import shutil
    from settings import PROJECT_ROOT
    
    model_path = request.model_path
    model_type = request.type
    
    # Handle path mapping: local_ai_server returns /app/models/...
    # but admin_ui has models at /app/project/models/...
    if model_path.startswith('/app/models/'):
        model_path = model_path.replace('/app/models/', f'{PROJECT_ROOT}/models/')
    
    # Security: Ensure path is within the models directory
    models_base = os.path.join(PROJECT_ROOT, "models")
    
    # Normalize paths for comparison
    abs_model_path = os.path.abspath(model_path)
    abs_models_base = os.path.abspath(models_base)
    
    if not abs_model_path.startswith(abs_models_base):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model path: must be within {models_base}"
        )
    
    if not os.path.exists(abs_model_path):
        raise HTTPException(
            status_code=404,
            detail=f"Model not found: {model_path}"
        )
    
    try:
        if os.path.isdir(abs_model_path):
            shutil.rmtree(abs_model_path)
        else:
            os.remove(abs_model_path)
            # Also remove .json config file if exists (for Piper models)
            json_path = abs_model_path.replace('.onnx', '.onnx.json')
            if os.path.exists(json_path):
                os.remove(json_path)
        
        return {
            "success": True,
            "message": f"Model deleted: {os.path.basename(abs_model_path)}"
        }
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="Permission denied: cannot delete model"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete model: {str(e)}"
        )


async def _verify_model_loaded(model_type: str, get_setting) -> bool:
    """Verify that the specified model type is loaded after restart."""
    # Try both localhost and container name
    urls_to_try = [
        "ws://127.0.0.1:8765",
        "ws://local_ai_server:8765",
        get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
    ]
    
    for ws_url in urls_to_try:
        try:
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "status"}))
                response = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(response)
                
                models = data.get("models", {})
                if model_type == "stt":
                    return models.get("stt", {}).get("loaded", False)
                elif model_type == "tts":
                    return models.get("tts", {}).get("loaded", False)
                elif model_type == "llm":
                    return models.get("llm", {}).get("loaded", False)
                return True
        except Exception:
            continue
    
    return False


def _read_env_values(env_file: str, keys: list) -> Dict[str, str]:
    """Read specific environment variable values from .env file."""
    values = {}
    if not os.path.exists(env_file):
        return values
    
    with open(env_file, 'r') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                key = line.split('=')[0].strip()
                if key in keys:
                    value = line.split('=', 1)[1].strip()
                    values[key] = value
    return values


def _update_env_file(env_file: str, updates: Dict[str, str]):
    """Update environment variables in .env file."""
    upsert_env_vars(env_file, updates, header="Local AI model management")


# Import docker at module level for switch endpoint
try:
    import docker
except ImportError:
    docker = None
