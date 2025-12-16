import docker
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import os
import yaml
import subprocess
import stat
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
from settings import ENV_PATH, CONFIG_PATH, ensure_env_file, PROJECT_ROOT
from services.fs import upsert_env_vars, atomic_write_text
from api.models_catalog import (
    get_full_catalog, get_models_by_language, get_available_languages,
    LANGUAGE_NAMES, REGION_NAMES, VOSK_STT_MODELS, SHERPA_STT_MODELS,
    KROKO_STT_MODELS, PIPER_TTS_MODELS, KOKORO_TTS_MODELS, LLM_MODELS
)

router = APIRouter()


def setup_host_symlink() -> dict:
    """Create /app/project symlink on host for Docker path resolution.
    
    The admin_ui container uses PROJECT_ROOT=/app/project internally.
    When docker-compose runs from inside the container, the docker daemon
    (on the host) resolves paths like /app/project/models on the HOST.
    This symlink ensures the host's /app/project points to the actual project.
    """
    results = {"success": True, "messages": [], "errors": []}
    
    try:
        client = docker.from_env()
        
        # Create symlink on host: /app/project -> actual project path
        # We detect the actual host path from the admin_ui container's mount
        admin_container = client.containers.get("admin_ui")
        mounts = admin_container.attrs.get("Mounts", [])
        
        # Find the mount for /app/project
        host_project_path = None
        for mount in mounts:
            if mount.get("Destination") == "/app/project":
                host_project_path = mount.get("Source")
                break
        
        if host_project_path:
            # Run alpine container to create symlink on host
            symlink_script = f'''
                mkdir -p /app 2>/dev/null || true
                if [ -L /app/project ]; then
                    # Symlink exists, check if pointing to correct path
                    CURRENT=$(readlink /app/project)
                    if [ "$CURRENT" = "{host_project_path}" ]; then
                        echo "Symlink already correct"
                        exit 0
                    fi
                fi
                rm -rf /app/project 2>/dev/null || true
                ln -sfn {host_project_path} /app/project
                echo "Created symlink /app/project -> {host_project_path}"
            '''
            output = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", symlink_script],
                volumes={"/app": {"bind": "/app", "mode": "rw"}},
                remove=True,
            )
            results["messages"].append(output.decode().strip() if output else "Symlink setup complete")
        else:
            results["messages"].append("Could not detect host project path from mounts")
            
    except Exception as e:
        results["errors"].append(f"Symlink setup error: {e}")
    
    return results


def setup_media_paths() -> dict:
    """Setup media directories and symlink for Asterisk playback.
    
    Mirrors the setup_media_paths() function from install.sh to ensure
    the wizard provides the same out-of-box experience.
    """
    results = {
        "success": True,
        "messages": [],
        "errors": []
    }
    
    # First, ensure host symlink exists for Docker path resolution
    symlink_result = setup_host_symlink()
    results["messages"].extend(symlink_result.get("messages", []))
    results["errors"].extend(symlink_result.get("errors", []))
    
    # Path inside container (mounted from host)
    container_media_dir = "/mnt/asterisk_media/ai-generated"
    # Path on host (PROJECT_ROOT is mounted from host)
    host_media_dir = os.path.join(PROJECT_ROOT, "asterisk_media", "ai-generated")
    
    # 1. Create directories with proper permissions (775 = rwxrwxr-x)
    try:
        os.makedirs(host_media_dir, mode=0o775, exist_ok=True)
        # Ensure parent also has correct permissions
        os.chmod(os.path.dirname(host_media_dir), 0o775)
        os.chmod(host_media_dir, 0o775)
        results["messages"].append(f"Created media directory: {host_media_dir}")
    except Exception as e:
        results["errors"].append(f"Failed to create media directory: {e}")
        results["success"] = False
    
    # 2. Try to create symlink on host via docker exec on host system
    # This runs a privileged command to create the symlink
    try:
        # Check if we can access docker socket
        client = docker.from_env()
        
        # Get the actual host path for PROJECT_ROOT
        # The symlink should be: /var/lib/asterisk/sounds/ai-generated -> {PROJECT_ROOT}/asterisk_media/ai-generated
        # We need to detect the actual host path
        
        # Run a command on host to create the symlink
        # Using alpine image with host volume mounts
        symlink_script = f'''
            mkdir -p /mnt/asterisk_media/ai-generated 2>/dev/null || true
            chmod 775 /mnt/asterisk_media/ai-generated 2>/dev/null || true
            chmod 775 /mnt/asterisk_media 2>/dev/null || true
            if [ -L /var/lib/asterisk/sounds/ai-generated ] || [ -e /var/lib/asterisk/sounds/ai-generated ]; then
                rm -rf /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
            fi
            ln -sfn /mnt/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
            if [ -d /var/lib/asterisk/sounds/ai-generated ]; then
                echo "SUCCESS: Symlink created"
            else
                echo "FALLBACK: Creating alternative symlink"
                # Try alternative path if /mnt/asterisk_media doesn't exist
                PROJ_MEDIA="{PROJECT_ROOT}/asterisk_media/ai-generated"
                if [ -d "$PROJ_MEDIA" ]; then
                    ln -sfn "$PROJ_MEDIA" /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
                fi
            fi
        '''
        
        # Run on host via privileged container
        container = client.containers.run(
            "alpine:latest",
            command=["sh", "-c", symlink_script],
            volumes={
                "/var/lib/asterisk/sounds": {"bind": "/var/lib/asterisk/sounds", "mode": "rw"},
                "/mnt/asterisk_media": {"bind": "/mnt/asterisk_media", "mode": "rw"},
                PROJECT_ROOT: {"bind": PROJECT_ROOT, "mode": "rw"},
            },
            remove=True,
            detach=False,
        )
        output = container.decode() if isinstance(container, bytes) else str(container)
        results["messages"].append(f"Symlink setup: {output.strip()}")
        
    except docker.errors.ImageNotFound:
        results["messages"].append("Alpine image not found, will pull on next attempt")
        try:
            client.images.pull("alpine:latest")
            results["messages"].append("Pulled alpine image")
        except:
            results["errors"].append("Could not pull alpine image for symlink setup")
    except Exception as e:
        # Symlink creation failed, provide manual instructions
        results["messages"].append(f"Auto symlink setup skipped: {e}")
        results["messages"].append(
            "Manual setup required: Run on host:\n"
            f"  sudo ln -sfn {PROJECT_ROOT}/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated"
        )
    
    return results


@router.post("/init-env")
async def init_env():
    """Initialize .env from .env.example on first wizard step.
    
    Called when user clicks Next from step 1 (provider selection).
    This ensures .env exists with default values before proceeding.
    """
    created = ensure_env_file()
    return {"created": created, "env_path": ENV_PATH}


@router.get("/load-config")
async def load_existing_config():
    """Load existing configuration from .env file.
    
    Used to pre-populate wizard fields if config already exists.
    """
    from dotenv import dotenv_values
    
    config = {}
    
    # Load from .env if it exists
    if os.path.exists(ENV_PATH):
        env_values = dotenv_values(ENV_PATH)
        config = {
            "asterisk_host": env_values.get("ASTERISK_HOST", "127.0.0.1"),
            "asterisk_username": env_values.get("ASTERISK_ARI_USERNAME", ""),
            "asterisk_password": env_values.get("ASTERISK_ARI_PASSWORD", ""),
            "asterisk_port": int(env_values.get("ASTERISK_ARI_PORT", "8088")),
            "asterisk_scheme": env_values.get("ASTERISK_ARI_SCHEME", "http"),
            "asterisk_app": env_values.get("ASTERISK_ARI_APP", "asterisk-ai-voice-agent"),
            "openai_key": env_values.get("OPENAI_API_KEY", ""),
            "groq_key": env_values.get("GROQ_API_KEY", ""),
            "deepgram_key": env_values.get("DEEPGRAM_API_KEY", ""),
            "google_key": env_values.get("GOOGLE_API_KEY", ""),
            "elevenlabs_key": env_values.get("ELEVENLABS_API_KEY", ""),
        }
    
    # Load AI config from YAML if it exists
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                yaml_config = yaml.safe_load(f)
            
            # Get default context settings
            default_ctx = yaml_config.get("contexts", {}).get("default", {})
            config["ai_name"] = default_ctx.get("ai_name", "Asterisk Agent")
            config["ai_role"] = default_ctx.get("ai_role", "")
            config["greeting"] = default_ctx.get("greeting", "")
            
            # Try to detect provider from config
            if default_ctx.get("provider"):
                config["provider"] = default_ctx.get("provider")
        except:
            pass
    
    return config


@router.get("/engine-status")
async def get_engine_status():
    """Check if ai-engine container is running.
    
    Used in wizard completion step to determine if user needs
    to start the engine (first time) or if it's already running.
    """
    try:
        client = docker.from_env()
        try:
            container = client.containers.get("ai_engine")
            return {
                "running": container.status == "running",
                "status": container.status,
                "exists": True
            }
        except docker.errors.NotFound:
            return {
                "running": False,
                "status": "not_found",
                "exists": False
            }
    except Exception as e:
        return {
            "running": False,
            "status": "error",
            "exists": False,
            "error": str(e)
        }


@router.post("/setup-media-paths")
async def setup_media_paths_endpoint():
    """Setup media directories and symlinks for Asterisk audio playback.
    
    This endpoint ensures the AI Engine can write audio files that Asterisk
    can read for playback. Creates directories and symlinks as needed.
    """
    result = setup_media_paths()
    return result


@router.post("/start-engine")
async def start_engine(action: str = "start"):
    """Start, restart, or rebuild the ai-engine container.
    
    Args:
        action: "start" (default), "restart", or "rebuild"
    
    Uses docker compose (installed in container) to manage containers.
    Returns detailed progress and error information.
    """
    import subprocess
    from settings import PROJECT_ROOT
    
    print(f"DEBUG: AI Engine action={action} from PROJECT_ROOT={PROJECT_ROOT}")
    
    # Setup media paths first
    media_setup = setup_media_paths()
    
    steps = []
    
    def add_step(name: str, status: str, message: str = ""):
        steps.append({"name": name, "status": status, "message": message})
        print(f"DEBUG: Step '{name}': {status} - {message}")
    
    try:
        # Step 1: Check Docker availability
        add_step("check_docker", "running", "Checking Docker availability...")
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            add_step("check_docker", "error", "Docker Compose not available")
            return {
                "success": False,
                "action": "error",
                "message": "Docker Compose not available in container",
                "steps": steps,
                "media_setup": media_setup
            }
        add_step("check_docker", "complete", f"Docker Compose available")
        
        # Step 2: Check current container status
        add_step("check_container", "running", "Checking container status...")
        client = docker.from_env()
        container_exists = False
        container_running = False
        try:
            container = client.containers.get("ai_engine")
            container_exists = True
            container_running = container.status == "running"
            add_step("check_container", "complete", f"Container exists, status: {container.status}")
        except docker.errors.NotFound:
            add_step("check_container", "complete", "Container does not exist")
        
        # Step 3: Determine action
        if action == "rebuild":
            add_step("rebuild", "running", "Rebuilding AI Engine image...")
            result = subprocess.run(
                ["docker", "compose", "build", "--no-cache", "ai-engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                add_step("rebuild", "error", result.stderr[:500] if result.stderr else "Build failed")
                return {
                    "success": False,
                    "action": "error",
                    "message": f"Failed to rebuild: {result.stderr[:200] if result.stderr else 'Unknown error'}",
                    "steps": steps,
                    "media_setup": media_setup
                }
            add_step("rebuild", "complete", "Image rebuilt successfully")
        
        # Step 4: Build image if container doesn't exist (docker compose handles image naming)
        if not container_exists:
            add_step("build", "running", "Building AI Engine image (this may take 1-2 minutes)...")
            build_result = subprocess.run(
                ["docker", "compose", "build", "ai-engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=300  # 5 min timeout for build
            )
            if build_result.returncode != 0:
                error_msg = build_result.stderr or build_result.stdout or "Build failed"
                add_step("build", "error", error_msg[:500])
                return {
                    "success": False,
                    "action": "error",
                    "message": f"Failed to build AI Engine image: {error_msg[:200]}",
                    "steps": steps,
                    "stdout": build_result.stdout,
                    "stderr": build_result.stderr,
                    "media_setup": media_setup
                }
            add_step("build", "complete", "Image built successfully")
        
        # Step 5: Start/restart container using docker compose
        if action == "restart" and container_running:
            add_step("restart", "running", "Restarting AI Engine...")
            result = subprocess.run(
                ["docker", "compose", "restart", "ai-engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=60
            )
        else:
            add_step("start", "running", "Starting AI Engine container...")
            # Use up -d with --force-recreate if container exists
            cmd = ["docker", "compose", "up", "-d"]
            if container_exists:
                cmd.append("--force-recreate")
            cmd.append("ai-engine")
            
            result = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=60  # Container start should be quick after build
            )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            add_step("start" if action != "restart" else "restart", "error", error_msg[:500])
            return {
                "success": False,
                "action": "error",
                "message": f"Failed to start AI Engine: {error_msg[:200]}",
                "steps": steps,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "media_setup": media_setup
            }
        
        add_step("start" if action != "restart" else "restart", "complete", "Container started")
        
        # Step 5: Wait for health check
        add_step("health_check", "running", "Waiting for AI Engine to be ready...")
        import httpx
        import asyncio
        
        health_url = "http://127.0.0.1:15000/health"
        max_attempts = 30
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=2.0) as http_client:
                    resp = await http_client.get(health_url)
                    if resp.status_code == 200:
                        health_data = resp.json()
                        add_step("health_check", "complete", f"AI Engine healthy - {len(health_data.get('providers', {}))} providers loaded")
                        return {
                            "success": True,
                            "action": action,
                            "message": "AI Engine started successfully",
                            "steps": steps,
                            "health": health_data,
                            "media_setup": media_setup
                        }
            except Exception:
                pass
            await asyncio.sleep(1)
        
        add_step("health_check", "warning", "Health check timed out but container is running")
        return {
            "success": True,
            "action": action,
            "message": "AI Engine started (health check pending)",
            "steps": steps,
            "media_setup": media_setup
        }
        
    except subprocess.TimeoutExpired as e:
        add_step("timeout", "error", f"Operation timed out after {e.timeout}s")
        return {
            "success": False,
            "action": "timeout",
            "message": f"Operation timed out. Check container logs.",
            "steps": steps,
            "media_setup": media_setup
        }
    except Exception as e:
        add_step("error", "error", str(e))
        return {
            "success": False,
            "action": "error",
            "message": str(e),
            "steps": steps,
            "media_setup": media_setup
        }

# ============== Local AI Server Setup ==============

# Model catalog - now imported from models_catalog.py for multi-language support
# Use get_full_catalog() to get the complete catalog with all language models


@router.get("/local/available-models")
async def get_available_models(language: Optional[str] = None):
    """Return catalog of available models with system recommendations.
    
    Args:
        language: Optional language code to filter models (e.g., 'en-US', 'fr-FR')
    """
    import psutil
    import subprocess
    
    # Get system info for recommendations
    ram_gb = psutil.virtual_memory().total // (1024**3)
    cpu_cores = psutil.cpu_count() or 1

    # Best-effort GPU detection (works when tooling is available in the container)
    gpu_detected = False
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=2)
        gpu_detected = result.returncode == 0
    except Exception:
        gpu_detected = False
    
    # Get the full catalog or filtered by language
    if language:
        full_catalog = get_models_by_language(language)
        # Add LLM models (language-independent)
        full_catalog["llm"] = LLM_MODELS
    else:
        full_catalog = get_full_catalog()
    
    # Add recommendation flags based on system
    catalog = {}
    for category, models in full_catalog.items():
        catalog[category] = []
        for model in models:
            model_copy = model.copy()
            # Mark as system-recommended based on RAM + basic CPU/GPU heuristics.
            recommended_ram = int(model.get("recommended_ram_gb", 0) or 0)
            meets_ram = ram_gb >= recommended_ram

            system_recommended = False
            if category == "llm":
                model_id = model.get("id")
                if model_id == "tinyllama":
                    system_recommended = meets_ram and cpu_cores >= 2
                elif model_id == "phi3_mini":
                    system_recommended = meets_ram and cpu_cores >= 4
                elif model_id == "llama32_3b":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 6)
                elif model_id == "mistral_7b_instruct":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 12)
                elif model_id == "llama3_8b_instruct":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 16)
                else:
                    system_recommended = bool(model.get("recommended")) and meets_ram
            else:
                system_recommended = bool(model.get("recommended")) and meets_ram

            if system_recommended:
                model_copy["system_recommended"] = True
            catalog[category].append(model_copy)
    
    return {
        "catalog": catalog,
        "system_ram_gb": ram_gb,
        "system_cpu_cores": cpu_cores,
        "system_gpu_detected": gpu_detected,
        "languages": get_available_languages(),
        "language_names": LANGUAGE_NAMES,
        "region_names": REGION_NAMES
    }


@router.get("/local/available-languages")
async def get_languages():
    """Return list of all available languages for STT and TTS models."""
    return {
        "languages": get_available_languages(),
        "language_names": LANGUAGE_NAMES,
        "region_names": REGION_NAMES
    }


@router.get("/local/detect-tier")
async def detect_local_tier():
    """Detect system tier for local AI models based on CPU/RAM/GPU."""
    import subprocess
    from settings import PROJECT_ROOT
    
    try:
        # Get system info
        import psutil
        cpu_count = psutil.cpu_count()
        ram_gb = psutil.virtual_memory().total // (1024**3)
        
        # Check for GPU
        gpu_detected = False
        try:
            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
            if result.returncode == 0:
                gpu_detected = True
        except:
            pass
        
        # Determine tier
        if gpu_detected:
            if ram_gb >= 32 and cpu_count >= 8:
                tier = "HEAVY_GPU"
            elif ram_gb >= 16 and cpu_count >= 4:
                tier = "MEDIUM_GPU"
            else:
                tier = "LIGHT_CPU"
        else:
            if ram_gb >= 32 and cpu_count >= 16:
                tier = "HEAVY_CPU"
            elif ram_gb >= 16 and cpu_count >= 8:
                tier = "MEDIUM_CPU"
            elif ram_gb >= 8 and cpu_count >= 4:
                tier = "LIGHT_CPU"
            else:
                tier = "LIGHT_CPU"
        
        # Tier descriptions
        tier_info = {
            "LIGHT_CPU": {
                "models": "TinyLlama 1.1B + Vosk Small + Piper Medium",
                "performance": "25-40 seconds per turn",
                "download_size": "~1.5 GB"
            },
            "MEDIUM_CPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium",
                "performance": "20-30 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "HEAVY_CPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium",
                "performance": "25-35 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "MEDIUM_GPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium (GPU)",
                "performance": "8-12 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "HEAVY_GPU": {
                "models": "Llama-2 13B + Vosk 0.22 + Piper High (GPU)",
                "performance": "10-15 seconds per turn",
                "download_size": "~10 GB"
            }
        }
        
        return {
            "cpu_cores": cpu_count,
            "ram_gb": ram_gb,
            "gpu_detected": gpu_detected,
            "tier": tier,
            "tier_info": tier_info.get(tier, {})
        }
    except Exception as e:
        return {"error": str(e)}


_download_process = None
_download_output = []
_download_status = {"running": False, "completed": False, "error": None}
_download_progress = {"bytes_downloaded": 0, "total_bytes": 0, "percent": 0, "speed_bps": 0, "eta_seconds": None, "start_time": None}

@router.post("/local/download-models")
async def download_local_models(tier: str = "auto"):
    """Start model download in background. Returns immediately."""
    import subprocess
    import threading
    from settings import PROJECT_ROOT
    
    global _download_process, _download_output, _download_status
    
    # Reset state
    _download_output = []
    _download_status = {"running": True, "completed": False, "error": None}
    
    try:
        # Run model_setup.sh with --assume-yes
        cmd = ["bash", "scripts/model_setup.sh", "--assume-yes"]
        if tier != "auto":
            cmd.extend(["--tier", tier])
        
        def run_download():
            global _download_process, _download_output, _download_status
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                _download_process = process
                
                for line in iter(process.stdout.readline, ''):
                    if line:
                        _download_output.append(line.strip())
                        # Keep last 50 lines
                        if len(_download_output) > 50:
                            _download_output.pop(0)
                
                process.wait()
                _download_status["running"] = False
                _download_status["completed"] = process.returncode == 0
                if process.returncode != 0:
                    _download_status["error"] = f"Download failed with code {process.returncode}"
            except Exception as e:
                _download_status["running"] = False
                _download_status["error"] = str(e)
        
        # Start download thread
        thread = threading.Thread(target=run_download, daemon=True)
        thread.start()
        
        return {
            "status": "started",
            "message": "Model download started. This may take several minutes."
        }
    except Exception as e:
        _download_status = {"running": False, "completed": False, "error": str(e)}
        return {"status": "error", "message": str(e)}


@router.get("/local/download-progress")
async def get_download_progress():
    """Get current download progress and output."""
    global _download_output, _download_status, _download_progress
    
    return {
        "running": _download_status.get("running", False),
        "completed": _download_status.get("completed", False),
        "error": _download_status.get("error"),
        "output": _download_output[-20:] if _download_output else [],  # Last 20 lines
        # Detailed progress info
        "bytes_downloaded": _download_progress.get("bytes_downloaded", 0),
        "total_bytes": _download_progress.get("total_bytes", 0),
        "percent": _download_progress.get("percent", 0),
        "speed_bps": _download_progress.get("speed_bps", 0),
        "eta_seconds": _download_progress.get("eta_seconds"),
        "current_file": _download_progress.get("current_file", "")
    }


class SingleModelDownload(BaseModel):
    model_id: str
    type: str  # stt, tts, llm
    download_url: str
    model_path: Optional[str] = None
    config_url: Optional[str] = None  # For TTS models that need JSON config
    voice_files: Optional[Dict[str, str]] = None  # For Kokoro TTS voice files


@router.post("/local/download-model")
async def download_single_model(request: SingleModelDownload):
    """Download a single model from the catalog."""
    import threading
    import urllib.request
    import zipfile
    import tarfile
    import shutil
    import time
    from settings import PROJECT_ROOT
    
    global _download_output, _download_status, _download_progress
    
    # Reset state
    _download_output = []
    _download_status = {"running": True, "completed": False, "error": None}
    _download_progress = {"bytes_downloaded": 0, "total_bytes": 0, "percent": 0, "speed_bps": 0, "eta_seconds": None, "start_time": time.time(), "current_file": request.model_id}
    
    # Determine target directory based on type
    # Special case: Kroko embedded models go to models/kroko/
    is_kroko_embedded = request.model_id and request.model_id.startswith("kroko_") and request.model_id != "kroko_cloud"
    
    if is_kroko_embedded:
        target_dir = os.path.join(PROJECT_ROOT, "models", "kroko")
    elif request.type == "stt":
        target_dir = os.path.join(PROJECT_ROOT, "models", "stt")
    elif request.type == "tts":
        target_dir = os.path.join(PROJECT_ROOT, "models", "tts")
    elif request.type == "llm":
        target_dir = os.path.join(PROJECT_ROOT, "models", "llm")
    else:
        return {"status": "error", "message": f"Invalid model type: {request.type}"}
    
    # Ensure target directory exists
    os.makedirs(target_dir, exist_ok=True)
    
    def download_worker():
        global _download_output, _download_status, _download_progress
        try:
            _download_output.append(f"📥 Starting download: {request.model_id}")
            _download_output.append(f"   URL: {request.download_url}")
            
            # Determine file extension
            url_lower = request.download_url.lower()
            if '.zip' in url_lower:
                ext = '.zip'
                is_archive = True
            elif '.tar.gz' in url_lower or '.tgz' in url_lower:
                ext = '.tar.gz'
                is_archive = True
            elif '.tar.bz2' in url_lower:
                ext = '.tar.bz2'
                is_archive = True
            elif '.tar' in url_lower:
                ext = '.tar'
                is_archive = True
            else:
                # Single file (e.g., ONNX model)
                ext = os.path.splitext(request.download_url)[1] or ''
                is_archive = False
            
            # Download to temp file
            temp_file = os.path.join(target_dir, f"temp_download{ext}")
            start_time = time.time()
            last_update_time = start_time
            
            def progress_hook(block_num, block_size, total_size):
                nonlocal last_update_time
                global _download_progress, _download_output
                
                bytes_downloaded = block_num * block_size
                current_time = time.time()
                elapsed = current_time - start_time
                
                if total_size > 0:
                    percent = min(100, (bytes_downloaded * 100) // total_size)
                    speed_bps = bytes_downloaded / elapsed if elapsed > 0 else 0
                    remaining_bytes = total_size - bytes_downloaded
                    eta_seconds = remaining_bytes / speed_bps if speed_bps > 0 else None
                    
                    _download_progress["bytes_downloaded"] = bytes_downloaded
                    _download_progress["total_bytes"] = total_size
                    _download_progress["percent"] = percent
                    _download_progress["speed_bps"] = int(speed_bps)
                    _download_progress["eta_seconds"] = int(eta_seconds) if eta_seconds else None
                    
                    # Update output every 2 seconds
                    if current_time - last_update_time >= 2:
                        last_update_time = current_time
                        mb_done = bytes_downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        speed_mbps = speed_bps / (1024 * 1024)
                        eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s" if eta_seconds else "calculating..."
                        _download_output.append(f"   {mb_done:.1f}/{mb_total:.1f} MB ({percent}%) - {speed_mbps:.2f} MB/s - ETA: {eta_str}")
                        if len(_download_output) > 50:
                            _download_output.pop(0)
            
            urllib.request.urlretrieve(request.download_url, temp_file, progress_hook)
            _download_output.append("✅ Download complete, extracting...")
            _download_progress["percent"] = 100
            
            if is_archive:
                # Extract archive
                if ext == '.zip':
                    with zipfile.ZipFile(temp_file, 'r') as zf:
                        # Get the root folder name from the archive
                        names = zf.namelist()
                        root_folder = names[0].split('/')[0] if names else None
                        zf.extractall(target_dir)
                        _download_output.append(f"✅ Extracted to {target_dir}/{root_folder}")
                elif ext in ['.tar.gz', '.tar', '.tgz', '.tar.bz2']:
                    with tarfile.open(temp_file, 'r:*') as tf:
                        names = tf.getnames()
                        root_folder = names[0].split('/')[0] if names else None
                        tf.extractall(target_dir)
                        _download_output.append(f"✅ Extracted to {target_dir}/{root_folder}")
                
                # Clean up archive file after extraction
                os.remove(temp_file)
                _download_output.append("🧹 Cleaned up archive file")
            else:
                # Single file - rename to model_path or keep original name
                # Special handling for Kokoro which uses a directory structure
                if request.model_id == "kokoro_82m":
                    kokoro_dir = os.path.join(target_dir, "kokoro")
                    os.makedirs(kokoro_dir, exist_ok=True)
                    final_path = os.path.join(kokoro_dir, "kokoro-v1_0.pth")
                elif request.model_path:
                    final_path = os.path.join(target_dir, request.model_path)
                else:
                    final_path = os.path.join(target_dir, os.path.basename(request.download_url))
                
                if temp_file != final_path:
                    shutil.move(temp_file, final_path)
                _download_output.append(f"✅ Saved to {final_path}")
                
                # Download config file for TTS models (e.g., Piper .onnx.json)
                if request.config_url and request.type == "tts":
                    # For Kokoro, config goes in the model directory; for Piper, next to .onnx
                    if request.model_id == "kokoro_82m":
                        kokoro_dir = os.path.dirname(final_path)
                        config_dest = os.path.join(kokoro_dir, "config.json")
                    else:
                        config_dest = final_path + ".json"
                    _download_output.append(f"📥 Downloading config file...")
                    try:
                        urllib.request.urlretrieve(request.config_url, config_dest)
                        _download_output.append(f"✅ Config saved to {config_dest}")
                    except Exception as config_err:
                        _download_output.append(f"⚠️ Config download failed: {config_err}")
                
                # Download voice files for Kokoro TTS
                if request.voice_files and request.type == "tts":
                    kokoro_dir = os.path.dirname(final_path)
                    voices_dir = os.path.join(kokoro_dir, "voices")
                    os.makedirs(voices_dir, exist_ok=True)
                    _download_output.append(f"📥 Downloading voice files...")
                    for voice_name, voice_url in request.voice_files.items():
                        try:
                            voice_dest = os.path.join(voices_dir, f"{voice_name}.pt")
                            urllib.request.urlretrieve(voice_url, voice_dest)
                            _download_output.append(f"✅ Voice '{voice_name}' saved")
                        except Exception as voice_err:
                            _download_output.append(f"⚠️ Voice '{voice_name}' download failed: {voice_err}")
            
            _download_status["running"] = False
            _download_status["completed"] = True
            _download_output.append(f"🎉 Model {request.model_id} installed successfully!")
            
        except Exception as e:
            _download_status["running"] = False
            _download_status["error"] = str(e)
            _download_output.append(f"❌ Error: {str(e)}")
            # Clean up partial download on error
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
    
    # Start download thread
    thread = threading.Thread(target=download_worker, daemon=True)
    thread.start()
    
    return {
        "status": "started",
        "message": f"Downloading {request.model_id}..."
    }


class ModelSelection(BaseModel):
    stt: str  # backend name (e.g., "vosk")
    llm: str  # model id (e.g., "phi-3-mini")
    tts: str  # backend name (e.g., "piper")
    language: Optional[str] = "en-US"
    kroko_embedded: Optional[bool] = False
    kokoro_mode: Optional[str] = "local"
    kokoro_api_base_url: Optional[str] = None
    kokoro_api_key: Optional[str] = None
    # New fields for exact model selection
    stt_model_id: Optional[str] = None  # exact model id (e.g., "vosk_en_us_small")
    tts_model_id: Optional[str] = None  # exact model id (e.g., "piper_en_us_lessac_medium")
    # Optional: custom LLM GGUF URL download (advanced)
    llm_download_url: Optional[str] = None
    llm_model_path: Optional[str] = None  # optional filename under models/llm/
    llm_name: Optional[str] = None  # optional display name for logs


@router.post("/local/download-selected-models")
async def download_selected_models(selection: ModelSelection):
    """Download user-selected models from the catalog."""
    import subprocess
    import threading
    import urllib.request
    import zipfile
    import shutil
    import time
    from settings import PROJECT_ROOT
    
    global _download_output, _download_status, _download_progress
    
    # Reset state
    _download_output = []
    _download_status = {"running": True, "completed": False, "error": None}
    _download_progress = {
        "bytes_downloaded": 0,
        "total_bytes": 0,
        "percent": 0,
        "speed_bps": 0,
        "eta_seconds": None,
        "start_time": time.time(),
        "current_file": "",
    }
    
    # Get full catalog
    catalog = get_full_catalog()
    
    # Find appropriate model - prefer exact model_id, fallback to backend+language
    def find_stt_model(backend: str, language: str, model_id: str = None):
        """Find the best STT model. Prefers exact model_id match."""
        # First try exact model ID match
        if model_id:
            for model in catalog["stt"]:
                if model.get("id") == model_id:
                    return model
        # Fallback to backend + language match
        for model in catalog["stt"]:
            if model.get("backend") == backend and model.get("language") == language:
                return model
        # Fallback to English if language not available
        for model in catalog["stt"]:
            if model.get("backend") == backend and model.get("language") == "en-US":
                return model
        # Final fallback to any model with that backend
        for model in catalog["stt"]:
            if model.get("backend") == backend:
                return model
        return None
    
    def find_tts_model(backend: str, language: str, model_id: str = None):
        """Find the best TTS model. Prefers exact model_id match."""
        # First try exact model ID match
        if model_id:
            for model in catalog["tts"]:
                if model.get("id") == model_id:
                    return model
        # Fallback to backend + language match
        for model in catalog["tts"]:
            if model.get("backend") == backend and model.get("language") == language:
                return model
        # Fallback to English if language not available
        for model in catalog["tts"]:
            if model.get("backend") == backend and model.get("language") == "en-US":
                return model
        # Final fallback to any model with that backend
        for model in catalog["tts"]:
            if model.get("backend") == backend:
                return model
        return None
    
    # Get model info from catalog - prefer exact model_id if provided
    stt_model = find_stt_model(selection.stt, selection.language, selection.stt_model_id)
    llm_model = next((m for m in catalog["llm"] if m.get("id") == selection.llm), None)
    tts_model = find_tts_model(selection.tts, selection.language, selection.tts_model_id)

    # Support custom GGUF LLM downloads (Wizard advanced path)
    if selection.llm == "custom_gguf_url":
        url = (selection.llm_download_url or "").strip()
        if not url:
            return {"status": "error", "message": "Custom LLM selected but llm_download_url is empty"}
        filename = (selection.llm_model_path or "").strip()
        if not filename:
            filename = os.path.basename(url.split("?", 1)[0])
        if not filename.endswith(".gguf"):
            return {"status": "error", "message": "Custom LLM filename must end with .gguf"}
        llm_model = {
            "id": "custom_gguf_url",
            "name": selection.llm_name or filename,
            "download_url": url,
            "model_path": filename,
            "size_mb": 0,
            "size_display": "Custom",
        }
    
    _download_output.append(f"🌍 Selected language: {selection.language}")
    
    if not stt_model:
        return {"status": "error", "message": f"Unknown STT model: {selection.stt}"}
    if not llm_model:
        return {"status": "error", "message": f"Unknown LLM model: {selection.llm}"}
    if not tts_model:
        return {"status": "error", "message": f"Unknown TTS model: {selection.tts}"}

    kokoro_mode = (selection.kokoro_mode or "local").lower()
    skip_kokoro_download = tts_model.get("backend") == "kokoro" and kokoro_mode in ("api", "hf")
    
    def download_file(url: str, dest_path: str, label: str):
        """Download a file with progress reporting."""
        global _download_output, _download_progress
        try:
            _download_output.append(f"⬇️ Downloading {label}...")
            _download_progress["current_file"] = label
            
            # Create directory if needed
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            # Download with progress
            start_time = time.time()
            last_update = start_time
            def report_progress(block_num, block_size, total_size):
                nonlocal last_update
                bytes_done = block_num * block_size
                if total_size > 0:
                    now = time.time()
                    percent = int(min(100, (bytes_done * 100) // total_size))
                    elapsed = max(0.001, now - start_time)
                    speed_bps = int(bytes_done / elapsed)
                    remaining = max(0, total_size - bytes_done)
                    eta = int(remaining / speed_bps) if speed_bps > 0 else None

                    _download_progress["bytes_downloaded"] = int(bytes_done)
                    _download_progress["total_bytes"] = int(total_size)
                    _download_progress["percent"] = percent
                    _download_progress["speed_bps"] = speed_bps
                    _download_progress["eta_seconds"] = eta

                    if now - last_update >= 2:
                        last_update = now
                        mb_done = bytes_done / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        _download_output.append(f"   {label}: {mb_done:.1f}/{mb_total:.1f} MB ({percent}%)")
            
            urllib.request.urlretrieve(url, dest_path, report_progress)
            _download_output.append(f"✅ {label} downloaded successfully")
            return True
        except Exception as e:
            _download_output.append(f"❌ Failed to download {label}: {e}")
            return False
    
    def run_downloads():
        global _download_status, _download_output
        models_dir = os.path.join(PROJECT_ROOT, "models")
        success = True
        
        try:
            # Download STT model
            if stt_model.get("download_url"):
                stt_dir = os.path.join(models_dir, "stt")
                os.makedirs(stt_dir, exist_ok=True)
                
                if stt_model.get("backend") == "vosk":
                    # Vosk is a zip file
                    zip_path = os.path.join(stt_dir, "vosk-model.zip")
                    if download_file(stt_model["download_url"], zip_path, "Vosk STT Model"):
                        _download_output.append("📦 Extracting Vosk model...")
                        with zipfile.ZipFile(zip_path, 'r') as zf:
                            zf.extractall(stt_dir)
                        os.remove(zip_path)
                        _download_output.append("✅ Vosk model extracted")
                    else:
                        success = False
                elif stt_model.get("backend") == "sherpa":
                    # Sherpa is a tar.bz2 archive
                    import tarfile
                    archive_path = os.path.join(stt_dir, "sherpa-model.tar.bz2")
                    if download_file(stt_model["download_url"], archive_path, "Sherpa STT Model"):
                        _download_output.append("📦 Extracting Sherpa model...")
                        with tarfile.open(archive_path, 'r:bz2') as tf:
                            tf.extractall(stt_dir)
                        os.remove(archive_path)
                        _download_output.append("✅ Sherpa model extracted")
                    else:
                        success = False
                elif stt_model.get("backend") == "kroko" and stt_model.get("embedded"):
                    # Kroko embedded ONNX models go to models/kroko/
                    kroko_dir = os.path.join(models_dir, "kroko")
                    os.makedirs(kroko_dir, exist_ok=True)
                    dest = os.path.join(kroko_dir, stt_model["model_path"])
                    if not download_file(stt_model["download_url"], dest, "Kroko Embedded STT Model"):
                        success = False
                else:
                    # Single file model
                    dest = os.path.join(stt_dir, stt_model["model_path"])
                    if not download_file(stt_model["download_url"], dest, "STT Model"):
                        success = False
            else:
                _download_output.append(f"ℹ️ STT: {stt_model['name']} (no download needed)")
            
            # Download LLM model
            if llm_model.get("download_url"):
                llm_dir = os.path.join(models_dir, "llm")
                os.makedirs(llm_dir, exist_ok=True)
                dest = os.path.join(llm_dir, llm_model["model_path"])
                if not download_file(llm_model["download_url"], dest, "LLM Model"):
                    success = False
            else:
                _download_output.append(f"ℹ️ LLM: {llm_model['name']} (no download needed)")
            
            # Download TTS model
            if skip_kokoro_download:
                _download_output.append(
                    f"ℹ️ TTS: {tts_model['name']} (no download needed for Kokoro mode={kokoro_mode})"
                )
            elif tts_model.get("download_url"):
                tts_dir = os.path.join(models_dir, "tts")
                os.makedirs(tts_dir, exist_ok=True)
                
                if tts_model.get("backend") == "kokoro":
                    # Kokoro has multiple files: model, config, and voice files
                    kokoro_dir = os.path.join(tts_dir, "kokoro")
                    os.makedirs(kokoro_dir, exist_ok=True)
                    voices_dir = os.path.join(kokoro_dir, "voices")
                    os.makedirs(voices_dir, exist_ok=True)
                    
                    # Download main model
                    model_dest = os.path.join(kokoro_dir, "kokoro-v1_0.pth")
                    if not download_file(tts_model["download_url"], model_dest, "Kokoro TTS Model"):
                        success = False
                    
                    # Download config
                    if tts_model.get("config_url"):
                        config_dest = os.path.join(kokoro_dir, "config.json")
                        download_file(tts_model["config_url"], config_dest, "Kokoro Config")
                    
                    # Download voice files
                    if tts_model.get("voice_files"):
                        for voice_name, voice_url in tts_model["voice_files"].items():
                            voice_dest = os.path.join(voices_dir, f"{voice_name}.pt")
                            download_file(voice_url, voice_dest, f"Kokoro Voice: {voice_name}")
                else:
                    # Standard single-file TTS model (Piper)
                    dest = os.path.join(tts_dir, tts_model["model_path"])
                    if not download_file(tts_model["download_url"], dest, "TTS Model"):
                        success = False
                    
                    # Also download config file if present
                    if tts_model.get("config_url"):
                        config_dest = dest + ".json"
                        download_file(tts_model["config_url"], config_dest, "TTS Config")
            else:
                _download_output.append(f"ℹ️ TTS: {tts_model['name']} (no download needed)")
            
            # Update .env with selected models
            _download_output.append("📝 Updating configuration...")
            env_updates = []
            
            # Persist backend selections (even if no download needed)
            env_updates.append(f"LOCAL_STT_BACKEND={stt_model.get('backend') or selection.stt}")
            env_updates.append(f"LOCAL_TTS_BACKEND={tts_model.get('backend') or selection.tts}")

            # Kroko toggle (embedded vs cloud)
            if (stt_model.get("backend") or selection.stt) == "kroko":
                env_updates.append(f"KROKO_EMBEDDED={'1' if selection.kroko_embedded else '0'}")
                env_updates.append(f"KROKO_LANGUAGE={selection.language or 'en-US'}")
            
            # Set model paths
            if stt_model.get("model_path") and stt_model.get("download_url"):
                stt_path = os.path.join("/app/models/stt", stt_model["model_path"])
                if stt_model.get("backend") == "sherpa":
                    env_updates.append(f"SHERPA_MODEL_PATH={stt_path}")
                else:
                    env_updates.append(f"LOCAL_STT_MODEL_PATH={stt_path}")
            
            if llm_model.get("model_path") and llm_model.get("download_url"):
                llm_path = os.path.join("/app/models/llm", llm_model["model_path"])
                env_updates.append(f"LOCAL_LLM_MODEL_PATH={llm_path}")
            
            if tts_model.get("model_path") and tts_model.get("download_url"):
                tts_path = os.path.join("/app/models/tts", tts_model["model_path"])
                if tts_model.get("backend") == "kokoro":
                    env_updates.append(f"KOKORO_MODEL_PATH={tts_path}")
                else:
                    env_updates.append(f"LOCAL_TTS_MODEL_PATH={tts_path}")

            # Kokoro mode: local vs api/hf (no local files required)
            if (tts_model.get("backend") or selection.tts) == "kokoro":
                mode = kokoro_mode
                if mode not in ("local", "api", "hf"):
                    mode = "local"
                env_updates.append(f"KOKORO_MODE={mode}")
                env_updates.append("KOKORO_VOICE=af_heart")
                if mode == "api":
                    if selection.kokoro_api_base_url:
                        env_updates.append(f"KOKORO_API_BASE_URL={selection.kokoro_api_base_url}")
                    if selection.kokoro_api_key:
                        env_updates.append(f"KOKORO_API_KEY={selection.kokoro_api_key}")
            
            # Write to .env
            if env_updates:
                env_path = os.path.join(PROJECT_ROOT, ".env")
                updates_dict = {}
                for update in env_updates:
                    if "=" not in update:
                        continue
                    k, v = update.split("=", 1)
                    updates_dict[k.strip()] = v.strip()

                upsert_env_vars(
                    env_path,
                    updates_dict,
                    header="Model selections from wizard",
                )
                
                _download_output.append("✅ Configuration updated")
            
            _download_status["completed"] = success
            _download_status["running"] = False
            
            if success:
                _download_output.append("🎉 All models downloaded successfully!")
            else:
                _download_status["error"] = "Some downloads failed"
                
        except Exception as e:
            _download_status["running"] = False
            _download_status["error"] = str(e)
            _download_output.append(f"❌ Error: {e}")
    
    # Start download thread
    thread = threading.Thread(target=run_downloads, daemon=True)
    thread.start()
    
    total_mb = (
        stt_model.get("size_mb", 0)
        + llm_model.get("size_mb", 0)
        + (0 if skip_kokoro_download else tts_model.get("size_mb", 0))
    )
    
    return {
        "status": "started",
        "message": f"Downloading {total_mb} MB of models...",
        "models": {
            "stt": stt_model["name"],
            "llm": llm_model["name"],
            "tts": tts_model["name"]
        }
    }


@router.get("/local/models-status")
async def check_models_status():
    """Check if required models are downloaded.
    
    Detects all supported STT/TTS backends:
    - STT: Vosk (vosk-model*), Sherpa-ONNX (sherpa*), Kroko (kroko*)
    - TTS: Piper (*.onnx), Kokoro (kokoro/voices/*.pt)
    - LLM: GGUF models (*.gguf)
    """
    from settings import PROJECT_ROOT
    import os
    
    models_dir = os.path.join(PROJECT_ROOT, "models")
    
    # STT models grouped by backend
    stt_backends = {
        "vosk": [],
        "sherpa": [],
        "kroko": []
    }
    
    # TTS models grouped by backend
    tts_backends = {
        "piper": [],
        "kokoro": []
    }
    
    # LLM models
    llm_models = []
    
    stt_dir = os.path.join(models_dir, "stt")
    llm_dir = os.path.join(models_dir, "llm")
    tts_dir = os.path.join(models_dir, "tts")
    
    # Scan STT models
    if os.path.exists(stt_dir):
        for item in os.listdir(stt_dir):
            item_path = os.path.join(stt_dir, item)
            if item.startswith("vosk-model") and os.path.isdir(item_path):
                stt_backends["vosk"].append(item)
            elif "sherpa" in item.lower() and os.path.isdir(item_path):
                stt_backends["sherpa"].append(item)
    
    # Check for Kroko models (separate directory)
    kroko_dir = os.path.join(models_dir, "kroko")
    if os.path.exists(kroko_dir):
        for item in os.listdir(kroko_dir):
            if item.endswith(".onnx"):
                stt_backends["kroko"].append(item)
    
    # Scan LLM models
    if os.path.exists(llm_dir):
        for item in os.listdir(llm_dir):
            if item.endswith(".gguf"):
                llm_models.append(item)
    
    # Scan TTS models
    if os.path.exists(tts_dir):
        for item in os.listdir(tts_dir):
            item_path = os.path.join(tts_dir, item)
            if item.endswith(".onnx"):
                tts_backends["piper"].append(item)
            elif item == "kokoro" and os.path.isdir(item_path):
                # Check for Kokoro voice files
                voices_dir = os.path.join(item_path, "voices")
                if os.path.exists(voices_dir):
                    for voice in os.listdir(voices_dir):
                        if voice.endswith(".pt"):
                            tts_backends["kokoro"].append(voice.replace(".pt", ""))
                # Also check for model files directly in kokoro dir
                if not tts_backends["kokoro"]:
                    # Fall back to checking for .pt files in kokoro dir
                    for f in os.listdir(item_path):
                        if f.endswith(".pt"):
                            tts_backends["kokoro"].append(f.replace(".pt", ""))
    
    # Compute ready state: at least one STT backend, one TTS backend, and LLM
    stt_ready = any(stt_backends.values())
    tts_ready = any(tts_backends.values())
    llm_ready = len(llm_models) > 0
    ready = stt_ready and tts_ready and llm_ready
    
    # Flatten for backward compatibility
    stt_models = (
        stt_backends["vosk"] + 
        [f"sherpa:{m}" for m in stt_backends["sherpa"]] +
        [f"kroko:{m}" for m in stt_backends["kroko"]]
    )
    tts_models = (
        tts_backends["piper"] +
        [f"kokoro:{v}" for v in tts_backends["kokoro"]]
    )
    
    return {
        "ready": ready,
        "stt_models": stt_models,
        "llm_models": llm_models,
        "tts_models": tts_models,
        # New detailed breakdown by backend
        "stt_backends": stt_backends,
        "tts_backends": tts_backends,
        "status": {
            "stt_ready": stt_ready,
            "llm_ready": llm_ready,
            "tts_ready": tts_ready
        }
    }


@router.post("/local/start-server")
async def start_local_ai_server():
    """Start the local-ai-server container.
    
    Also sets up media paths for audio playback to work correctly.
    Uses --force-recreate to handle cases where container is already running.
    """
    import subprocess
    from settings import PROJECT_ROOT
    
    # Setup media paths first (same as start_engine)
    print("DEBUG: Setting up media paths for local AI server...")
    media_setup = setup_media_paths()
    print(f"DEBUG: Media setup result: {media_setup}")
    
    # Check if container is already running
    already_running = False
    try:
        client = docker.from_env()
        try:
            container = client.containers.get("local_ai_server")
            already_running = container.status == "running"
            print(f"DEBUG: local_ai_server container status: {container.status}")
        except docker.errors.NotFound:
            print("DEBUG: local_ai_server container not found, will create")
    except Exception as e:
        print(f"DEBUG: Could not check container status: {e}")
    
    try:
        # Use --force-recreate if already running to ensure fresh start
        cmd = ["docker", "compose", "up", "-d"]
        
        # Explicitly remove container if it exists to avoid "Conflict" errors
        try:
            client = docker.from_env()
            try:
                old_container = client.containers.get("local_ai_server")
                print(f"DEBUG: Removing existing local_ai_server container ({old_container.status})")
                old_container.remove(force=True)
            except docker.errors.NotFound:
                pass
        except Exception as e:
            print(f"DEBUG: Error removing container: {e}")

        if already_running:
            cmd.append("--force-recreate")
            print("DEBUG: Container already running, using --force-recreate")
        cmd.append("local-ai-server")
        
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "Local AI Server started successfully" + (" (recreated)" if already_running else ""),
                "media_setup": media_setup,
                "recreated": already_running
            }
        else:
            return {
                "success": False,
                "message": f"Failed to start: {result.stderr or result.stdout}",
                "media_setup": media_setup
            }
    except Exception as e:
        return {"success": False, "message": str(e), "media_setup": media_setup}


@router.get("/local/server-logs")
async def get_local_server_logs():
    """Get local-ai-server container logs."""
    import subprocess
    
    try:
        # Get recent logs for display
        result = subprocess.run(
            ["docker", "logs", "--tail", "30", "local_ai_server"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logs = result.stdout or result.stderr
        lines = logs.strip().split('\n') if logs else []
        
        # Check if server is ready by looking at ALL logs (not just tail)
        # The startup message might be pushed out by connection logs
        ready_result = subprocess.run(
            ["docker", "logs", "local_ai_server"],
            capture_output=True,
            text=True,
            timeout=10
        )
        all_logs = (ready_result.stdout or "") + (ready_result.stderr or "")
        
        # Check for ready indicators in full log history
        ready = "Enhanced Local AI Server started" in all_logs or \
                "All models loaded successfully" in all_logs or \
                "models loaded" in all_logs.lower()
        
        return {
            "logs": lines[-20:],
            "ready": ready
        }
    except subprocess.TimeoutExpired:
        return {"logs": [], "ready": False, "error": "Timeout getting logs"}
    except Exception as e:
        return {"logs": [], "ready": False, "error": str(e)}


@router.get("/local/server-status")
async def get_local_server_status():
    """Check if local-ai-server is running and healthy."""
    import docker
    import httpx
    
    try:
        client = docker.from_env()
        try:
            container = client.containers.get("local_ai_server")
            running = container.status == "running"
        except:
            running = False
        
        # Try health check
        healthy = False
        if running:
            try:
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get("http://127.0.0.1:8000/health", timeout=5.0)
                    healthy = response.status_code == 200
            except:
                pass
        
        return {
            "running": running,
            "healthy": healthy
        }
    except Exception as e:
        return {"running": False, "healthy": False, "error": str(e)}


class ModelSwitchRequest(BaseModel):
    stt_backend: Optional[str] = None  # vosk, sherpa, kroko
    stt_model_path: Optional[str] = None
    llm_model_path: Optional[str] = None
    tts_backend: Optional[str] = None  # piper, kokoro
    tts_model_path: Optional[str] = None
    kokoro_voice: Optional[str] = None


@router.post("/local/switch-model")
async def switch_local_model(request: ModelSwitchRequest):
    """Switch models on the running local-ai-server without restart.
    
    Sends a WebSocket message to the local AI server to switch models dynamically.
    Also updates .env for persistence across restarts.
    """
    import websockets
    import json
    from settings import PROJECT_ROOT
    
    # Build the switch request
    switch_data = {"type": "switch_model"}
    env_updates = []
    
    if request.stt_backend:
        switch_data["stt_backend"] = request.stt_backend
        env_updates.append(f"LOCAL_STT_BACKEND={request.stt_backend}")
    
    if request.stt_model_path:
        switch_data["stt_model_path"] = request.stt_model_path
        env_updates.append(f"LOCAL_STT_MODEL_PATH={request.stt_model_path}")
    
    if request.llm_model_path:
        switch_data["llm_model_path"] = request.llm_model_path
        env_updates.append(f"LOCAL_LLM_MODEL_PATH={request.llm_model_path}")
    
    if request.tts_backend:
        switch_data["tts_backend"] = request.tts_backend
        env_updates.append(f"LOCAL_TTS_BACKEND={request.tts_backend}")
    
    if request.tts_model_path:
        switch_data["tts_model_path"] = request.tts_model_path
        env_updates.append(f"LOCAL_TTS_MODEL_PATH={request.tts_model_path}")
    
    if request.kokoro_voice:
        switch_data["kokoro_voice"] = request.kokoro_voice
        env_updates.append(f"KOKORO_VOICE={request.kokoro_voice}")
    
    # Update .env for persistence
    if env_updates:
        try:
            env_path = os.path.join(PROJECT_ROOT, ".env")
            updates_dict = {}
            for update in env_updates:
                if "=" not in update:
                    continue
                k, v = update.split("=", 1)
                updates_dict[k.strip()] = v.strip()

            upsert_env_vars(
                env_path,
                updates_dict,
                header="Model switch from Dashboard",
            )
        except Exception as e:
            return {"success": False, "message": f"Failed to update .env: {e}"}
    
    # Send switch command to local AI server via WebSocket
    try:
        async with websockets.connect("ws://127.0.0.1:8765", ping_interval=None) as ws:
            auth_token = (os.getenv("LOCAL_WS_AUTH_TOKEN", "") or "").strip()
            if auth_token:
                await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                auth_data = json.loads(raw)
                if auth_data.get("type") != "auth_response" or auth_data.get("status") != "ok":
                    raise RuntimeError(f"Local AI auth failed: {auth_data}")

            await ws.send(json.dumps(switch_data))
            response = await ws.recv()
            result = json.loads(response)
            
            if result.get("status") == "success":
                return {
                    "success": True,
                    "message": result.get("message", "Models switched successfully"),
                    "changed": result.get("changed", []),
                    "env_updated": env_updates
                }
            else:
                return {
                    "success": False,
                    "message": result.get("message", "Switch failed"),
                }
    except Exception as e:
        return {
            "success": False, 
            "message": f"Could not connect to local AI server: {e}. Restart the server for changes to take effect.",
            "env_updated": env_updates
        }


class ApiKeyValidation(BaseModel):
    provider: str
    api_key: str
    agent_id: Optional[str] = None  # Required for ElevenLabs Conversational AI

class AsteriskConnection(BaseModel):
    host: str
    username: str
    password: str
    port: int = 8088
    scheme: str = "http"
    app: str = "asterisk-ai-voice-agent"

@router.post("/validate-key")
async def validate_api_key(validation: ApiKeyValidation):
    """Validate an API key by testing it against the provider's API"""
    try:
        import httpx
        
        provider = validation.provider.lower()
        api_key = validation.api_key.strip() if validation.api_key else ""
        
        if not api_key:
            return {"valid": False, "error": "API key is empty"}
        
        logger.info(f"Validating {provider} API key (length: {len(api_key)})")
        
        async with httpx.AsyncClient() as client:
            if provider == "openai":
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "OpenAI API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}

            elif provider == "groq":
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "Groq API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                    
            elif provider == "deepgram":
                response = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={"Authorization": f"Token {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "Deepgram API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                    
            elif provider == "google":
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    timeout=10.0
                )
                if response.status_code == 200:
                    # Check if the required model with bidiGenerateContent is available
                    data = response.json()
                    models = data.get("models", [])
                    
                    # Find models that support bidiGenerateContent (required for Live API)
                    live_models = []
                    for model in models:
                        methods = model.get("supportedGenerationMethods", [])
                        if "bidiGenerateContent" in methods:
                            model_name = model.get("name", "").replace("models/", "")
                            live_models.append(model_name)
                    
                    if not live_models:
                        return {
                            "valid": False, 
                            "error": "API key valid but no Live API models available. Your API key doesn't have access to Gemini Live models (bidiGenerateContent). Try creating a new key at aistudio.google.com"
                        }
                    
                    # Check if our preferred models are available (in order of preference)
                    preferred_models = [
                        "gemini-2.5-flash-native-audio-preview-12-2025",  # Latest native audio model
                        "gemini-2.0-flash-live-001",  # Stable live model
                        "gemini-2.0-flash-exp",  # Experimental
                    ]
                    
                    for preferred_model in preferred_models:
                        if preferred_model in live_models:
                            return {
                                "valid": True, 
                                "message": f"Google API key is valid. Live model '{preferred_model}' is available."
                            }
                    
                    # No preferred model found, but we have some live models
                    return {
                        "valid": True, 
                        "message": f"Google API key is valid. Available Live models: {', '.join(live_models[:3])}"
                    }
                elif response.status_code in [400, 403]:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
            
            elif provider == "elevenlabs":
                # For ElevenLabs Conversational AI, validate using agent endpoint
                # Agent-scoped API keys don't have user_read permission
                agent_id = validation.agent_id
                
                if agent_id:
                    # Validate by fetching agent details (works with agent-scoped keys)
                    response = await client.get(
                        f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                        headers={"xi-api-key": api_key},
                        timeout=10.0
                    )
                    logger.info(f"ElevenLabs agent API response: {response.status_code}")
                    if response.status_code == 200:
                        agent_data = response.json()
                        agent_name = agent_data.get("name", "Unknown")
                        return {"valid": True, "message": f"ElevenLabs API key valid. Agent: {agent_name}"}
                    elif response.status_code == 401:
                        error_detail = response.json().get("detail", {})
                        error_msg = error_detail.get("message", "Invalid API key") if isinstance(error_detail, dict) else "Invalid API key"
                        return {"valid": False, "error": error_msg}
                    elif response.status_code == 404:
                        return {"valid": False, "error": "Agent not found. Check your Agent ID."}
                    else:
                        return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                else:
                    # Fallback: try user endpoint (for full-access keys)
                    response = await client.get(
                        "https://api.elevenlabs.io/v1/user",
                        headers={"xi-api-key": api_key},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        return {"valid": True, "message": "ElevenLabs API key is valid"}
                    elif response.status_code == 401:
                        error_detail = response.json().get("detail", {})
                        error_msg = error_detail.get("message", "Invalid API key") if isinstance(error_detail, dict) else "Invalid API key"
                        # Hint about agent_id if it's a permissions issue
                        if "missing_permissions" in str(error_detail):
                            error_msg = "API key valid but agent-scoped. Please provide Agent ID for validation."
                        return {"valid": False, "error": error_msg}
                    else:
                        return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
            
            else:
                return {"valid": False, "error": f"Unknown provider: {provider}"}
                
    except httpx.TimeoutException:
        return {"valid": False, "error": "Connection timeout"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

@router.post("/validate-connection")
async def validate_asterisk_connection(conn: AsteriskConnection):
    """Test Asterisk ARI connection"""
    try:
        import httpx
        
        # Try to connect to ARI interface
        base_url = f"{conn.scheme}://{conn.host}:{conn.port}/ari"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/asterisk/info",
                auth=(conn.username, conn.password),
                timeout=5.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "valid": True,
                    "message": f"Connected to Asterisk {data.get('system', {}).get('version', 'Unknown')}"
                }
            elif response.status_code == 401:
                return {"valid": False, "error": "Invalid username or password"}
            else:
                return {"valid": False, "error": f"Connection failed: HTTP {response.status_code}"}
                
    except httpx.ConnectError:
        return {"valid": False, "error": f"Cannot connect to {conn.host}:{conn.port} - Is Asterisk running?"}
    except httpx.TimeoutException:
        return {"valid": False, "error": "Connection timeout"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

@router.get("/status")
async def get_setup_status():
    """
    Check if initial setup has been completed
    Returns configured: true if .env exists with required keys
    """
    try:
        if not os.path.exists(ENV_PATH):
            return {"configured": False, "message": "Environment file not found"}
        
        # Read .env and check for minimal required config
        with open(ENV_PATH, 'r') as f:
            content = f.read()
            has_asterisk_host = "ASTERISK_HOST=" in content
            has_username = "ASTERISK_ARI_USERNAME=" in content
            
            if has_asterisk_host and has_username:
                return {"configured": True, "message": "Setup complete"}
            else:
                return {"configured": False, "message": "Incomplete configuration"}
                
    except Exception as e:
        return {"configured": False, "message": str(e)}

class SetupConfig(BaseModel):
    provider: str = "openai_realtime"
    asterisk_host: str
    asterisk_username: str
    asterisk_password: str
    asterisk_port: int = 8088
    asterisk_scheme: str = "http"
    asterisk_app: str = "asterisk-ai-voice-agent"
    openai_key: Optional[str] = None
    groq_key: Optional[str] = None
    deepgram_key: Optional[str] = None
    google_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    elevenlabs_agent_id: Optional[str] = None
    cartesia_key: Optional[str] = None
    greeting: str
    ai_name: str
    ai_role: str
    hybrid_llm_provider: Optional[str] = None

# ... (keep existing endpoints) ...

@router.post("/save")
async def save_setup_config(config: SetupConfig):
    # Validation: Check for required keys based on provider
    if config.provider == "openai_realtime" and not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for OpenAI Realtime provider")
    if config.provider == "deepgram":
        if not config.deepgram_key:
            raise HTTPException(status_code=400, detail="Deepgram API Key is required for Deepgram provider")
        if not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for Deepgram Think stage")
    if config.provider == "google_live" and not config.google_key:
            raise HTTPException(status_code=400, detail="Google API Key is required for Google Live provider")
    # Local hybrid uses a cloud LLM (Groq/OpenAI) or Ollama
    if config.provider == "local_hybrid":
        llm_provider = (config.hybrid_llm_provider or "groq").lower()
        if llm_provider == "openai" and not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for Local Hybrid pipeline when using OpenAI")
        if llm_provider == "groq" and not config.groq_key:
            raise HTTPException(status_code=400, detail="Groq API Key is required for Local Hybrid pipeline when using Groq")
    if config.provider == "elevenlabs_agent":
        if not config.elevenlabs_key:
            raise HTTPException(status_code=400, detail="ElevenLabs API Key is required for ElevenLabs Conversational provider")
        if not config.elevenlabs_agent_id:
            raise HTTPException(status_code=400, detail="ElevenLabs Agent ID is required for ElevenLabs Conversational provider")

    try:
        import shutil
        import datetime
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # Backup existing files
        if os.path.exists(ENV_PATH):
            shutil.copy2(ENV_PATH, f"{ENV_PATH}.bak.{timestamp}")
            
        if os.path.exists(CONFIG_PATH):
            shutil.copy2(CONFIG_PATH, f"{CONFIG_PATH}.bak.{timestamp}")

        # 1. Update .env
        env_updates = {
            "ASTERISK_HOST": config.asterisk_host,
            "ASTERISK_ARI_USERNAME": config.asterisk_username,
            "ASTERISK_ARI_PASSWORD": config.asterisk_password,
            "ASTERISK_ARI_PORT": str(config.asterisk_port),
            "ASTERISK_ARI_SCHEME": config.asterisk_scheme,
            "ASTERISK_APP_NAME": config.asterisk_app,
            "AI_NAME": config.ai_name,
            "AI_ROLE": config.ai_role,
            "GREETING": config.greeting,
        }
        
        if config.openai_key:
            env_updates["OPENAI_API_KEY"] = config.openai_key
        if config.groq_key:
            env_updates["GROQ_API_KEY"] = config.groq_key
        if config.deepgram_key:
            env_updates["DEEPGRAM_API_KEY"] = config.deepgram_key
        if config.google_key:
            env_updates["GOOGLE_API_KEY"] = config.google_key
        if config.elevenlabs_key:
            env_updates["ELEVENLABS_API_KEY"] = config.elevenlabs_key
        if config.elevenlabs_agent_id:
            env_updates["ELEVENLABS_AGENT_ID"] = config.elevenlabs_agent_id
        if config.cartesia_key:
            env_updates["CARTESIA_API_KEY"] = config.cartesia_key

        upsert_env_vars(ENV_PATH, env_updates, header="Setup Wizard")

        # 2. Update ai-agent.yaml - APPEND MODE
        # If provider already exists, just enable it and update greeting
        # If provider doesn't exist, create full config
        # Don't auto-disable other providers (user manages via Dashboard)
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                yaml_config = yaml.safe_load(f)
            
            yaml_config.setdefault("providers", {})
            providers = yaml_config["providers"]
            
            # Helper to check if provider already exists with config
            def provider_exists(name: str) -> bool:
                return name in providers and len(providers[name]) > 1  # More than just 'enabled'
            
            # Full agent providers - clear active_pipeline when setting as default
            if config.provider in ["openai_realtime", "deepgram", "google_live", "elevenlabs_agent", "local"]:
                yaml_config["default_provider"] = config.provider
                yaml_config["active_pipeline"] = None  # Full agents don't use pipelines
            
            if config.provider == "openai_realtime":
                providers.setdefault("openai_realtime", {})["enabled"] = True
                # Only set full config if provider doesn't exist yet
                if not provider_exists("openai_realtime"):
                    providers["openai_realtime"].update({
                        "model": "gpt-4o-realtime-preview-2024-12-17",
                        "voice": "alloy",
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "target_encoding": "mulaw",
                        "target_sample_rate_hz": 8000,
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "silence_duration_ms": 1000,
                            "prefix_padding_ms": 300,
                            "create_response": True
                        }
                    })
                # Always update greeting and instructions
                providers["openai_realtime"]["greeting"] = config.greeting
                providers["openai_realtime"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise. Always speak your responses out loud."
                
            elif config.provider == "deepgram":
                providers.setdefault("deepgram", {})["enabled"] = True
                if not provider_exists("deepgram"):
                    providers["deepgram"].update({
                        "model": "nova-2-general",
                        "tts_model": "aura-asteria-en",
                        "input_encoding": "mulaw",
                        "input_sample_rate_hz": 8000,
                        "output_encoding": "mulaw",
                        "output_sample_rate_hz": 8000
                    })
                providers["deepgram"]["greeting"] = config.greeting
                providers["deepgram"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."
                
            elif config.provider == "google_live":
                providers.setdefault("google_live", {})["enabled"] = True
                if not provider_exists("google_live"):
                    providers["google_live"].update({
                        "api_key": "${GOOGLE_API_KEY}",
                        "llm_model": "gemini-2.0-flash-exp",
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "provider_input_encoding": "linear16",
                        "provider_input_sample_rate_hz": 16000,
                        "target_encoding": "ulaw",
                        "target_sample_rate_hz": 8000,
                        "response_modalities": "audio",
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"]
                    })
                providers["google_live"]["greeting"] = config.greeting
                providers["google_live"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."

            elif config.provider == "elevenlabs_agent":
                providers.setdefault("elevenlabs_agent", {})["enabled"] = True
                if not provider_exists("elevenlabs_agent"):
                    providers["elevenlabs_agent"].update({
                        "api_key": "${ELEVENLABS_API_KEY}",
                        "agent_id": "${ELEVENLABS_AGENT_ID}",
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "target_encoding": "ulaw",
                        "target_sample_rate_hz": 8000
                    })

            elif config.provider == "local":
                providers.setdefault("local", {})["enabled"] = True
                if not provider_exists("local"):
                    providers["local"].update({
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "base_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "connect_timeout_sec": 2.0,
                        "response_timeout_sec": 10.0,
                        "chunk_ms": 320
                    })
                providers["local"]["greeting"] = config.greeting
                providers["local"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."
                # Start local-ai-server container
                try:
                    client = docker.from_env()
                    try:
                        container = client.containers.get("local_ai_server")
                        if container.status != "running":
                            container.start()
                    except docker.errors.NotFound:
                        print("Warning: local_ai_server container not found")
                except Exception as e:
                    print(f"Error starting local_ai_server: {e}")

            elif config.provider == "local_hybrid":
                # local_hybrid is a PIPELINE (Local STT + Cloud/Local LLM + Local TTS)
                yaml_config["active_pipeline"] = "local_hybrid"
                yaml_config["default_provider"] = "local"  # Fallback provider
                
                # Configure local provider
                providers.setdefault("local", {})["enabled"] = True
                if not provider_exists("local"):
                    providers["local"].update({
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "base_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "connect_timeout_sec": 2.0,
                        "response_timeout_sec": 10.0,
                        "chunk_ms": 320
                    })
                
                # Configure pipeline components
                providers.setdefault("local_stt", {})["enabled"] = True
                if not provider_exists("local_stt"):
                    providers["local_stt"].update({
                        "ws_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "stt_backend": "vosk"
                    })
                
                providers.setdefault("local_tts", {})["enabled"] = True
                if not provider_exists("local_tts"):
                    providers["local_tts"]["ws_url"] = "${LOCAL_WS_URL:-ws://127.0.0.1:8765}"
                
                llm_provider = (config.hybrid_llm_provider or "groq").lower()
                if llm_provider == "openai":
                    providers.setdefault("openai_llm", {})["enabled"] = True
                    if not provider_exists("openai_llm"):
                        providers["openai_llm"].update({
                            "api_key": "${OPENAI_API_KEY}",
                            "chat_base_url": "https://api.openai.com/v1",
                            "chat_model": "gpt-4o-mini",
                            "type": "openai",
                            "capabilities": ["llm"],
                        })
                elif llm_provider == "groq":
                    providers.setdefault("groq_llm", {})["enabled"] = True
                    if not provider_exists("groq_llm"):
                        providers["groq_llm"].update({
                            "api_key": "${GROQ_API_KEY}",
                            "chat_base_url": "https://api.groq.com/openai/v1",
                            "chat_model": "llama-3.3-70b-versatile",
                            "tools_enabled": False,
                            "type": "openai",
                            "capabilities": ["llm"],
                        })
                elif llm_provider == "ollama":
                    providers.setdefault("ollama_llm", {})["enabled"] = True
                    if not provider_exists("ollama_llm"):
                        providers["ollama_llm"].update({
                            "base_url": "http://localhost:11434",
                            "model": "llama3.2",
                            "temperature": 0.7,
                            "max_tokens": 200,
                            "timeout_sec": 60,
                            "tools_enabled": True,
                            "type": "ollama",
                            "capabilities": ["llm"],
                        })
                
                # Define the pipeline
                llm_component = "openai_llm"
                if llm_provider == "groq":
                    llm_component = "groq_llm"
                elif llm_provider == "ollama":
                    llm_component = "ollama_llm"

                yaml_config.setdefault("pipelines", {})["local_hybrid"] = {
                    "stt": "local_stt",
                    "llm": llm_component,
                    "tts": "local_tts"
                }
                
                # Start local-ai-server container
                try:
                    client = docker.from_env()
                    try:
                        container = client.containers.get("local_ai_server")
                        if container.status != "running":
                            container.start()
                    except docker.errors.NotFound:
                        print("Warning: local_ai_server container not found")
                except Exception as e:
                    print(f"Error starting local_ai_server: {e}")

            # C6 Fix: Create default context
            yaml_config.setdefault("contexts", {})["default"] = {
                "greeting": config.greeting,
                "prompt": f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise.",
                "provider": config.provider if config.provider != "local_hybrid" else "local",
                "profile": "telephony_ulaw_8k"
            }

            atomic_write_text(
                CONFIG_PATH,
                yaml.dump(yaml_config, default_flow_style=False, sort_keys=False),
                mode_from_existing=True,
            )
        
        # Config saved - engine start will be handled by completion step UI
        return {"status": "success", "provider": config.provider}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/skip")
async def skip_setup():
    """
    Skip the setup wizard by creating a minimal .env file
    This allows advanced users to configure manually
    """
    try:
        # Create minimal .env with a marker that setup was acknowledged
        if not os.path.exists(ENV_PATH):
            atomic_write_text(
                ENV_PATH,
                (
                    "# Setup wizard skipped - configure manually\n"
                    "ASTERISK_HOST=127.0.0.1\n"
                    "ASTERISK_ARI_USERNAME=asterisk\n"
                    "ASTERISK_ARI_PASSWORD=\n"
                ),
                mode_from_existing=True,
            )
        
        return {"status": "success", "message": "Setup skipped successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
