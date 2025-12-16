from fastapi import APIRouter, HTTPException
import docker
from typing import List, Optional
from pydantic import BaseModel
import psutil
import os
import shutil
import logging
import yaml
from services.fs import upsert_env_vars

logger = logging.getLogger(__name__)


def get_docker_compose_cmd() -> List[str]:
    """
    Find docker-compose binary dynamically.
    Returns the command as a list (either ['docker-compose'] or ['docker', 'compose']).
    """
    # Try docker-compose standalone first
    compose_path = shutil.which('docker-compose')
    if compose_path:
        return [compose_path]
    
    # Try docker compose (v2 plugin)
    docker_path = shutil.which('docker')
    if docker_path:
        # Verify 'docker compose' works
        import subprocess
        try:
            result = subprocess.run(
                [docker_path, 'compose', 'version'],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return [docker_path, 'compose']
        except:
            pass
    
    # Fallback to hardcoded paths for backwards compatibility
    if os.path.exists('/usr/local/bin/docker-compose'):
        return ['/usr/local/bin/docker-compose']
    if os.path.exists('/usr/bin/docker-compose'):
        return ['/usr/bin/docker-compose']
    
    raise FileNotFoundError('docker-compose not found in PATH or standard locations')

router = APIRouter()

class ContainerInfo(BaseModel):
    id: str
    name: str
    status: str
    state: str

@router.get("/containers")
async def get_containers():
    try:
        from datetime import datetime, timezone
        
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            # Get image name
            image_name = c.image.tags[0] if c.image.tags else c.image.short_id
            
            # Calculate uptime from StartedAt
            uptime = None
            started_at = None
            if c.status == "running":
                try:
                    started_str = c.attrs['State'].get('StartedAt', '')
                    if started_str and started_str != '0001-01-01T00:00:00Z':
                        # Docker uses nanoseconds (9 digits), Python only handles microseconds (6)
                        # Truncate nanoseconds to microseconds and normalize timezone
                        import re
                        # Match: 2025-12-03T06:23:45.362413338+00:00 or 2025-12-03T06:23:45.362413338Z
                        match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(Z|[+-]\d{2}:\d{2})?', started_str)
                        if match:
                            base = match.group(1)
                            frac = match.group(2)[:6].ljust(6, '0')  # Truncate to 6 digits
                            tz = match.group(3) or '+00:00'
                            if tz == 'Z':
                                tz = '+00:00'
                            normalized = f"{base}.{frac}{tz}"
                            started_dt = datetime.fromisoformat(normalized)
                        else:
                            # Fallback for simple format
                            started_dt = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
                        
                        started_at = started_str
                        now = datetime.now(timezone.utc)
                        delta = now - started_dt
                        
                        # Format uptime nicely
                        days = delta.days
                        hours, remainder = divmod(delta.seconds, 3600)
                        minutes, _ = divmod(remainder, 60)
                        
                        if days > 0:
                            uptime = f"{days}d {hours}h {minutes}m"
                        elif hours > 0:
                            uptime = f"{hours}h {minutes}m"
                        else:
                            uptime = f"{minutes}m"
                except Exception as e:
                    print(f"Error calculating uptime for {c.name}: {e}")
            
            # Get exposed ports
            ports = []
            try:
                port_bindings = c.attrs.get('NetworkSettings', {}).get('Ports', {})
                for container_port, host_bindings in (port_bindings or {}).items():
                    if host_bindings:
                        for binding in host_bindings:
                            host_port = binding.get('HostPort', '')
                            if host_port:
                                ports.append(f"{host_port}:{container_port}")
            except Exception:
                pass
            
            result.append({
                "id": c.id,
                "name": c.name,
                "image": image_name,
                "status": c.status,
                "state": c.attrs['State']['Status'],
                "uptime": uptime,
                "started_at": started_at,
                "ports": ports
            })
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error listing containers: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/containers/{container_id}/start")
async def start_container(container_id: str):
    """Start a stopped container using docker-compose or Docker API."""
    import subprocess
    
    # Map container names to docker-compose service names
    service_map = {
        "ai_engine": "ai-engine",
        "admin_ui": "admin-ui",
        "local_ai_server": "local-ai-server"
    }
    
    service_name = service_map.get(container_id)
    
    # If not in map, it might be an ID or a raw name.
    if not service_name:
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            name = container.name.lstrip('/')
            service_name = service_map.get(name, name)
        except:
            service_name = container_id
    
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    logger.info(f"Starting {service_name} from {project_root}")
    
    try:
        # Use docker compose with --build to ensure image exists
        compose_cmd = get_docker_compose_cmd()
        cmd = compose_cmd + ["up", "-d", "--build", service_name]
        
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout for potential build
        )
        
        logger.debug(f"start returncode={result.returncode}, stdout={result.stdout}, stderr={result.stderr}")
        
        if result.returncode == 0:
            return {"status": "success", "output": result.stdout or "Container started"}
        else:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to start: {result.stderr or result.stdout}"
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout waiting for container start")
    except FileNotFoundError:
        # Fallback to Docker API if docker-compose not available
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            container.start()
            return {"status": "success", "method": "docker-api"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str):
    """
    Restart a container using Docker SDK (preferred) or docker-compose.
    A5: Uses container.restart() for cleaner, faster restarts.
    """
    # Map container names to docker-compose service names
    service_map = {
        "ai_engine": "ai-engine",
        "admin_ui": "admin-ui",
        "local_ai_server": "local-ai-server"
    }
    
    # Map service names to container names
    container_name_map = {
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui", 
        "local-ai-server": "local_ai_server"
    }
    
    # Resolve container name
    container_name = container_id
    if container_id in service_map:
        # Input is already a container name like "ai_engine"
        container_name = container_id
    elif container_id in container_name_map:
        # Input is a service name like "ai-engine"
        container_name = container_name_map[container_id]
    
    logger.info(f"Restarting container: {container_name}")

    # NOTE: docker restart does NOT reload env_file changes.
    # For ai_engine/local_ai_server, prefer force-recreate so updated .env keys apply.
    if container_name in ("ai_engine", "local_ai_server"):
        service_name = service_map.get(container_name, container_name)
        return await _recreate_via_compose(service_name)
    
    try:
        # A5: Use Docker SDK for cleaner restart (no stop/rm/up)
        client = docker.from_env()
        container = client.containers.get(container_name)
        
        # Restart with 10 second timeout for graceful stop
        container.restart(timeout=10)
        
        logger.info(f"Container {container_name} restarted successfully via Docker SDK")
        return {
            "status": "success", 
            "method": "docker-sdk",
            "output": f"Container {container_name} restarted"
        }
        
    except docker.errors.NotFound:
        # Container doesn't exist, try to start it via docker-compose
        logger.warning(f"Container {container_name} not found, attempting docker-compose up")
        return await _start_via_compose(container_id, service_map)
        
    except docker.errors.APIError as e:
        logger.error(f"Docker API error restarting {container_name}: {e}")
        # Fallback to docker-compose for recreation
        return await _start_via_compose(container_id, service_map)
        
    except Exception as e:
        logger.error(f"Error restarting container {container_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _start_via_compose(container_id: str, service_map: dict):
    """Helper to start a container via docker-compose."""
    import subprocess
    
    service_name = service_map.get(container_id, container_id)
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    try:
        compose_cmd = get_docker_compose_cmd()
        cmd = compose_cmd + ["-p", "asterisk-ai-voice-agent", "up", "-d", "--no-build", service_name]
        
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            return {"status": "success", "method": "docker-compose", "output": result.stdout or "Container started"}
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start via compose: {result.stderr or result.stdout}"
            )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="docker-compose not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout waiting for container start")


async def _recreate_via_compose(service_name: str):
    """Force-recreate a compose service so env_file changes (.env) are applied."""
    import subprocess

    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    # Map service names to container names for stopping
    container_map = {
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui",
        "local-ai-server": "local_ai_server"
    }
    container_name = container_map.get(service_name, service_name)

    try:
        # First stop and remove the existing container to avoid name conflicts
        try:
            client = docker.from_env()
            container = client.containers.get(container_name)
            logger.info(f"Stopping container {container_name} before recreate")
            container.stop(timeout=10)
            container.remove()
            logger.info(f"Container {container_name} stopped and removed")
        except docker.errors.NotFound:
            logger.info(f"Container {container_name} not found, will create fresh")
        except Exception as e:
            logger.warning(f"Error stopping container: {e}")
        
        compose_cmd = get_docker_compose_cmd()
        # Use --force-recreate instead of --build for faster restarts
        # Rebuild only happens on explicit build request, not restart
        cmd = compose_cmd + [
            "up",
            "-d",
            "--force-recreate",
            "--no-build",
            service_name,
        ]

        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            return {"status": "success", "method": "docker-compose", "output": result.stdout or "Service recreated"}
        raise HTTPException(
            status_code=500,
            detail=f"Failed to recreate via compose: {result.stderr or result.stdout}",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="docker-compose not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout waiting for container recreate")


@router.post("/containers/ai_engine/reload")
async def reload_ai_engine():
    """
    Hot-reload AI Engine configuration without restarting the container.
    This reloads ai-agent.yaml and .env changes.
    
    Returns restart_required=True if new providers need to be added (hot-reload can't add new providers).
    """
    try:
        import httpx
        env = os.getenv("HEALTH_CHECK_AI_ENGINE_URL")
        candidates = []
        if env:
            candidates.append(env.replace("/health", "/reload"))
        candidates.extend(
            [
                "http://127.0.0.1:15000/reload",
                "http://ai-engine:15000/reload",
                "http://ai_engine:15000/reload",
            ]
        )
        # Dedupe
        seen = set()
        urls = []
        for u in candidates:
            u = (u or "").strip()
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        
        resp = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in urls:
                try:
                    logger.info(f"Sending reload request to AI Engine at {url}")
                    resp = await client.post(url)
                    break
                except httpx.ConnectError:
                    continue
        if resp is None:
            raise HTTPException(status_code=503, detail="AI Engine is not reachable")
        
        if resp.status_code == 200:
            data = resp.json()
            changes = data.get("changes", [])
                
            # Check if any change requires a restart (new providers, removed providers, deferred reload)
            restart_required = any(
                any(marker in str(c).lower() for marker in ("restart needed", "reload deferred"))
                for c in changes
            )
                
            if restart_required:
                return {
                    "status": "partial",
                    "message": "Config updated but some changes require a restart to fully apply",
                    "changes": changes,
                    "restart_required": True
                }
                
            return {
                "status": "success",
                "message": data.get("message", "Configuration reloaded"),
                "changes": changes,
                "restart_required": False
            }
        
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"AI Engine reload failed: {resp.text}"
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="AI Engine is not running. Start it first."
        )
    except Exception as e:
        logger.error(f"Error reloading AI Engine: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics")
async def get_system_metrics():
    try:
        # interval=None is non-blocking, returns usage since last call
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu": {
                "percent": cpu_percent,
                "count": psutil.cpu_count()
            },
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent,
                "used": memory.used
            },
            "disk": {
                "total": disk.total,
                "free": disk.free,
                "percent": disk.percent
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def get_system_health():
    """
    Aggregate health status from Local AI Server and AI Engine.
    """
    async def check_local_ai():
        try:
            import websockets
            import json
            import asyncio
            
            # With host networking, use localhost instead of container name
            uri = os.getenv("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
            print(f"DEBUG: Checking Local AI at {uri}")
            async with websockets.connect(uri, open_timeout=5) as websocket:
                print("DEBUG: Local AI connected, sending status...")
                await websocket.send(json.dumps({"type": "status"}))
                print("DEBUG: Local AI sent, waiting for response...")
                response = await asyncio.wait_for(websocket.recv(), timeout=5)
                print(f"DEBUG: Local AI response: {response[:100]}...")
                data = json.loads(response)
                if data.get("type") == "status_response":
                    # Prefer explicit fields from local-ai-server (v2 protocol), fallback to heuristics.
                    kroko = data.get("kroko") or {}
                    kokoro = data.get("kokoro") or {}

                    kroko_embedded = bool(kroko.get("embedded", False))
                    kroko_port = kroko.get("port")

                    kokoro_mode = (kokoro.get("mode") or "local").lower()
                    kokoro_voice = kokoro.get("voice")

                    # Back-compat for older payloads that didn't include structured metadata
                    if not kokoro_voice:
                        tts_display = data.get("models", {}).get("tts", {}).get("display") or ""
                        if "(" in tts_display and ")" in tts_display:
                            kokoro_voice = tts_display.split("(")[1].rstrip(")")

                    data["kroko_embedded"] = kroko_embedded
                    data["kroko_port"] = kroko_port
                    data["kokoro_mode"] = kokoro_mode
                    data["kokoro_voice"] = kokoro_voice
                    
                    return {
                        "status": "connected",
                        "details": data
                    }
                else:
                    return {
                        "status": "error",
                        "details": {"error": "Invalid response type"}
                    }
        except Exception as e:
            print(f"Local AI Check Error: {type(e).__name__}: {str(e)}")
            return {
                "status": "error",
                "details": {"error": f"{type(e).__name__}: {str(e)}"}
            }

    async def check_ai_engine():
        try:
            import httpx
            # With host networking, use localhost instead of container name
            url = os.getenv("HEALTH_CHECK_AI_ENGINE_URL", "http://127.0.0.1:15000/health")
            print(f"DEBUG: Checking AI Engine at {url}")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                print(f"DEBUG: AI Engine response: {resp.status_code}")
                if resp.status_code == 200:
                    return {
                        "status": "connected",
                        "details": resp.json()
                    }
                else:
                    return {
                        "status": "error",
                        "details": {"status_code": resp.status_code}
                    }
        except Exception as e:
            print(f"AI Engine Check Error: {type(e).__name__}: {str(e)}")
            return {
                "status": "error",
                "details": {"error": f"{type(e).__name__}: {str(e)}"}
            }

    import asyncio
    local_ai, ai_engine = await asyncio.gather(check_local_ai(), check_ai_engine())

    return {
        "local_ai_server": local_ai,
        "ai_engine": ai_engine
    }


@router.get("/directories")
async def get_directory_health():
    """
    Check health of directories required for audio playback.
    Returns status of media directory, symlink, and permissions.
    """
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    ast_media_dir = os.getenv("AST_MEDIA_DIR", "")
    
    # Expected paths
    host_media_dir = os.path.join(project_root, "asterisk_media", "ai-generated")
    asterisk_sounds_link = "/var/lib/asterisk/sounds/ai-generated"
    container_media_dir = "/mnt/asterisk_media/ai-generated"
    
    checks = {
        "media_dir_configured": {
            "status": "unknown",
            "configured_path": ast_media_dir,
            "expected_path": container_media_dir,
            "message": ""
        },
        "host_directory": {
            "status": "unknown",
            "path": host_media_dir,
            "exists": False,
            "writable": False,
            "message": ""
        },
        "asterisk_symlink": {
            "status": "unknown",
            "path": asterisk_sounds_link,
            "exists": False,
            "target": None,
            "valid": False,
            "message": ""
        }
    }
    
    # Check 1: AST_MEDIA_DIR configured
    if ast_media_dir:
        if "ai-generated" in ast_media_dir:
            checks["media_dir_configured"]["status"] = "ok"
            checks["media_dir_configured"]["message"] = "Correctly configured"
        else:
            checks["media_dir_configured"]["status"] = "warning"
            checks["media_dir_configured"]["message"] = "Missing 'ai-generated' subdirectory in path"
    else:
        checks["media_dir_configured"]["status"] = "error"
        checks["media_dir_configured"]["message"] = "AST_MEDIA_DIR not set in environment"
    
    # Check 2: Host directory exists and is writable
    try:
        if os.path.exists(host_media_dir):
            checks["host_directory"]["exists"] = True
            # Test write permission
            test_file = os.path.join(host_media_dir, ".write_test")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
                checks["host_directory"]["writable"] = True
                checks["host_directory"]["status"] = "ok"
                checks["host_directory"]["message"] = "Directory exists and is writable"
            except PermissionError:
                checks["host_directory"]["status"] = "error"
                checks["host_directory"]["message"] = "Directory exists but not writable"
        else:
            checks["host_directory"]["status"] = "error"
            checks["host_directory"]["message"] = "Directory does not exist"
    except Exception as e:
        checks["host_directory"]["status"] = "error"
        checks["host_directory"]["message"] = f"Error checking directory: {str(e)}"
    
    # Check 3: Asterisk symlink
    # Note: When running in Docker, we can't verify the symlink because
    # /var/lib/asterisk/sounds is on the host and not mounted into the container.
    # If the other checks pass, assume symlink is OK (user can verify with test call).
    try:
        in_docker = os.path.exists("/.dockerenv") or os.getenv("DOCKER_CONTAINER", "")
        
        if os.path.islink(asterisk_sounds_link):
            checks["asterisk_symlink"]["exists"] = True
            target = os.readlink(asterisk_sounds_link)
            checks["asterisk_symlink"]["target"] = target
            
            # Check if target contains the project path or is correct
            if host_media_dir in target or target == host_media_dir:
                checks["asterisk_symlink"]["valid"] = True
                checks["asterisk_symlink"]["status"] = "ok"
                checks["asterisk_symlink"]["message"] = f"Symlink valid → {target}"
            else:
                checks["asterisk_symlink"]["status"] = "warning"
                checks["asterisk_symlink"]["message"] = f"Symlink points to {target}, expected {host_media_dir}"
        elif os.path.exists(asterisk_sounds_link):
            checks["asterisk_symlink"]["exists"] = True
            checks["asterisk_symlink"]["status"] = "warning"
            checks["asterisk_symlink"]["message"] = "Path exists but is not a symlink"
        elif in_docker:
            # Running in Docker - can't verify symlink but if other checks pass, assume OK
            checks["asterisk_symlink"]["status"] = "ok"
            checks["asterisk_symlink"]["message"] = "Cannot verify from Docker (symlink is on host)"
            checks["asterisk_symlink"]["docker_note"] = True
        else:
            checks["asterisk_symlink"]["status"] = "error"
            checks["asterisk_symlink"]["message"] = "Symlink does not exist"
    except Exception as e:
        checks["asterisk_symlink"]["status"] = "error"
        checks["asterisk_symlink"]["message"] = f"Error checking symlink: {str(e)}"
    
    # Calculate overall health
    statuses = [c["status"] for c in checks.values()]
    if all(s == "ok" for s in statuses):
        overall = "healthy"
    elif any(s == "error" for s in statuses):
        overall = "error"
    else:
        overall = "warning"
    
    return {
        "overall": overall,
        "checks": checks
    }


@router.post("/directories/fix")
async def fix_directory_issues():
    """
    Attempt to fix directory permission and symlink issues.
    Note: Symlink creation requires host access - use preflight.sh for that.
    """
    import subprocess
    
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    host_media_dir = os.path.join(project_root, "asterisk_media", "ai-generated")
    asterisk_sounds_link = "/var/lib/asterisk/sounds/ai-generated"
    in_docker = os.path.exists("/.dockerenv") or os.getenv("DOCKER_CONTAINER", "")
    
    fixes_applied = []
    errors = []
    manual_steps = []
    
    # Fix 1: Create directory if missing
    try:
        os.makedirs(host_media_dir, mode=0o777, exist_ok=True)
        fixes_applied.append(f"Created directory: {host_media_dir}")
    except Exception as e:
        errors.append(f"Failed to create directory: {str(e)}")
    
    # Fix 2: Set permissions
    try:
        os.chmod(host_media_dir, 0o777)
        parent_dir = os.path.dirname(host_media_dir)
        os.chmod(parent_dir, 0o777)
        fixes_applied.append(f"Set permissions 777 on {host_media_dir}")
    except Exception as e:
        errors.append(f"Failed to set permissions: {str(e)}")
    
    # Fix 3: Symlink - can only be done from host, not container
    if in_docker:
        # Check if symlink already exists on host via mounted path
        # The symlink is on the host at /var/lib/asterisk/sounds which isn't mounted
        manual_steps.append(
            f"Run on host: sudo ln -sf {host_media_dir} {asterisk_sounds_link}"
        )
        manual_steps.append(
            f"Or run: ./preflight.sh --apply-fixes"
        )
    else:
        # Running on host - can create symlink directly
        try:
            if os.path.islink(asterisk_sounds_link):
                os.unlink(asterisk_sounds_link)
                fixes_applied.append(f"Removed old symlink: {asterisk_sounds_link}")
            elif os.path.exists(asterisk_sounds_link):
                errors.append(f"Cannot fix: {asterisk_sounds_link} exists and is not a symlink")
            
            if not os.path.exists(asterisk_sounds_link):
                os.symlink(host_media_dir, asterisk_sounds_link)
                fixes_applied.append(f"Created symlink: {asterisk_sounds_link} → {host_media_dir}")
        except PermissionError:
            try:
                result = subprocess.run(
                    ["sudo", "ln", "-sf", host_media_dir, asterisk_sounds_link],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    fixes_applied.append(f"Created symlink with sudo: {asterisk_sounds_link} → {host_media_dir}")
                else:
                    errors.append(f"Failed to create symlink with sudo: {result.stderr}")
            except Exception as e:
                errors.append(f"Failed to create symlink: {str(e)}")
        except Exception as e:
            errors.append(f"Failed to manage symlink: {str(e)}")
    
    # Fix 4: Update .env if needed
    env_file = os.path.join(project_root, ".env")
    try:
        result = upsert_env_vars(
            env_file,
            {"AST_MEDIA_DIR": "/mnt/asterisk_media/ai-generated"},
            header="Auto-fix: media directory",
        )
        if result.added_keys:
            fixes_applied.append("Added AST_MEDIA_DIR to .env (requires container restart)")
        elif result.updated_keys:
            fixes_applied.append("Updated AST_MEDIA_DIR in .env (requires container restart)")
    except Exception as e:
        errors.append(f"Failed to update .env: {str(e)}")
    
    return {
        "success": len(errors) == 0,
        "fixes_applied": fixes_applied,
        "errors": errors,
        "manual_steps": manual_steps if manual_steps else None,
        "restart_required": any("restart" in f.lower() for f in fixes_applied)
    }


# =============================================================================
# Platform Detection API (AAVA-126)
# =============================================================================

class PlatformCheck(BaseModel):
    id: str
    status: str  # ok, warning, error
    message: str
    blocking: bool
    action: dict = None

class PlatformInfo(BaseModel):
    os: dict
    docker: dict
    compose: dict
    selinux: dict = None
    directories: dict
    asterisk: dict = None

class PlatformResponse(BaseModel):
    platform: PlatformInfo
    checks: List[PlatformCheck]
    summary: dict


_PLATFORMS_CACHE = None
_PLATFORMS_CACHE_MTIME = None


def _github_docs_url(path_or_url: Optional[str]) -> Optional[str]:
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    base = os.getenv(
        "AAVA_DOCS_BASE_URL",
        "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/",
    )
    return base.rstrip("/") + "/" + path_or_url.lstrip("/")


def _load_platforms_yaml() -> Optional[dict]:
    global _PLATFORMS_CACHE, _PLATFORMS_CACHE_MTIME

    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    path = os.path.join(project_root, "config", "platforms.yaml")
    if not os.path.exists(path):
        return None

    try:
        mtime = os.path.getmtime(path)
        if _PLATFORMS_CACHE is not None and _PLATFORMS_CACHE_MTIME == mtime:
            return _PLATFORMS_CACHE
    except Exception:
        mtime = None

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        _PLATFORMS_CACHE = data
        _PLATFORMS_CACHE_MTIME = mtime
        return data
    except Exception:
        return None


def _deep_merge_dict(base: dict, override: dict) -> dict:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if k == "inherit":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k), v)
        else:
            out[k] = v
    return out


def _resolve_platform(platforms: dict, platform_key: str) -> Optional[dict]:
    if not platforms or not platform_key:
        return None
    node = platforms.get(platform_key)
    if not isinstance(node, dict):
        return None

    parent_key = node.get("inherit")
    if parent_key:
        parent = _resolve_platform(platforms, parent_key) or {}
        return _deep_merge_dict(parent, node)
    return dict(node)


def _select_platform_key(platforms: Optional[dict], os_id: str, os_family: str) -> Optional[str]:
    if not platforms:
        return None

    if os_id and os_id in platforms and isinstance(platforms.get(os_id), dict):
        if platforms[os_id].get("name") or platforms[os_id].get("docker"):
            return os_id

    # Find by os_ids membership.
    for key, node in platforms.items():
        if not isinstance(node, dict):
            continue
        ids = node.get("os_ids") or []
        if isinstance(ids, list) and os_id in ids:
            return key

    # Fallback to family base.
    if os_family and os_family in platforms and isinstance(platforms.get(os_family), dict):
        return os_family

    return None


def _detect_os():
    """Detect OS from /etc/os-release or container environment."""
    os_info = {
        "id": "unknown",
        "version": "unknown", 
        "family": "unknown",
        "arch": os.uname().machine,
        "is_eol": False,
        "in_container": os.path.exists("/.dockerenv")
    }
    
    # Try to read host OS info (mounted from host in docker-compose)
    os_release_paths = [
        "/host/etc/os-release",  # Mounted from host
        "/etc/os-release"         # Container's own
    ]
    
    for path in os_release_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        if line.startswith("ID="):
                            os_info["id"] = line.split("=")[1].strip().strip('"')
                        elif line.startswith("VERSION_ID="):
                            os_info["version"] = line.split("=")[1].strip().strip('"')
                
                # Determine family
                os_id = os_info["id"]
                if os_id in ["ubuntu", "debian", "linuxmint"]:
                    os_info["family"] = "debian"
                elif os_id in ["centos", "rhel", "rocky", "almalinux", "fedora"]:
                    os_info["family"] = "rhel"
                
                # Check EOL status
                eol_versions = {
                    "ubuntu": ["18.04", "20.04"],
                    "debian": ["9", "10"],
                    "centos": ["7", "8"]
                }
                if os_info["version"] in eol_versions.get(os_id, []):
                    os_info["is_eol"] = True
                
                break
            except Exception:
                pass
    
    return os_info


def _detect_docker():
    """Detect Docker version and mode."""
    docker_info = {
        "installed": False,
        "version": None,
        "mode": "unknown",
        "status": "error",
        "message": "Docker not detected",
        "socket_present": os.path.exists("/var/run/docker.sock"),
        "cli_present": shutil.which("docker") is not None,
    }
    
    try:
        client = docker.from_env()
        version_info = client.version()
        docker_info["installed"] = True
        docker_info["version"] = version_info.get("Version", "unknown")
        docker_info["api_version"] = version_info.get("ApiVersion", "unknown")
        docker_info["status"] = "ok"
        docker_info["message"] = None
        
        # Check version
        try:
            major = int(docker_info["version"].split(".")[0])
            if major < 20:
                docker_info["status"] = "error"
                docker_info["message"] = "Docker version too old (minimum: 20.10)"
            elif major < 25:
                docker_info["status"] = "warning"
                docker_info["message"] = "Upgrade to Docker 25.x+ recommended"
        except:
            pass
        
        # Detect rootless (check socket path)
        docker_host = os.environ.get("DOCKER_HOST", "")
        if "rootless" in docker_host or "/run/user/" in docker_host:
            docker_info["mode"] = "rootless"
        else:
            docker_info["mode"] = "rootful"
            
    except Exception as e:
        docker_info["message"] = str(e)
    
    return docker_info


def _detect_compose():
    """Detect Docker Compose version."""
    import subprocess
    
    compose_info = {
        "installed": False,
        "version": None,
        "type": "unknown",
        "status": "error",
        "message": "Docker Compose not detected"
    }
    
    # Method 1: Try docker compose (v2 plugin) via subprocess
    try:
        result = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().lstrip("v")
            compose_info["installed"] = True
            compose_info["version"] = version
            compose_info["type"] = "plugin"
            compose_info["status"] = "ok"
            compose_info["message"] = None
            
            # Check version - v2.x.x format
            try:
                parts = version.split(".")
                major = int(parts[0])
                minor = int(parts[1]) if len(parts) > 1 else 0
                if major >= 2:
                    if minor < 20:
                        compose_info["status"] = "warning"
                        compose_info["message"] = "Upgrade to Compose 2.20+ recommended"
                    # v2.20+ is good
                elif major == 1:
                    compose_info["status"] = "error"
                    compose_info["message"] = "Compose v1 is EOL and unsupported"
            except:
                pass
            
            return compose_info
    except Exception as e:
        # Docker CLI not available in container
        pass
    
    # Method 2: Infer from Docker SDK - if we're running in compose, it's v2
    try:
        client = docker.from_env()
        # Check if any containers are managed by compose
        for container in client.containers.list():
            labels = container.labels
            if "com.docker.compose.version" in labels:
                version = labels.get("com.docker.compose.version", "")
                compose_info["installed"] = True
                compose_info["version"] = version
                compose_info["type"] = "plugin"
                compose_info["status"] = "ok"
                compose_info["message"] = None
                
                # Check version
                try:
                    parts = version.split(".")
                    major = int(parts[0])
                    minor = int(parts[1]) if len(parts) > 1 else 0
                    if major >= 2:
                        if minor < 20:
                            compose_info["status"] = "warning"
                            compose_info["message"] = "Upgrade to Compose 2.20+ recommended"
                    elif major == 1:
                        compose_info["status"] = "error"
                        compose_info["message"] = "Compose v1 is EOL and unsupported"
                except:
                    pass
                
                return compose_info
    except:
        pass
    
    # Method 3: Try docker-compose (v1 standalone) - last resort
    try:
        result = subprocess.run(
            ["docker-compose", "version", "--short"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            compose_info["installed"] = True
            compose_info["version"] = result.stdout.strip().lstrip("v")
            compose_info["type"] = "standalone_v1"
            compose_info["status"] = "error"
            compose_info["message"] = "Compose v1 is EOL and unsupported"
    except:
        pass
    
    return compose_info


def _detect_selinux():
    """Detect SELinux status."""
    selinux_info = {
        "present": False,
        "mode": None,
        "tools_installed": False
    }
    
    # Check if SELinux is present
    if os.path.exists("/sys/fs/selinux"):
        selinux_info["present"] = True

        # Prefer kernel-provided status (works even if getenforce isn't installed).
        try:
            enforce_path = "/sys/fs/selinux/enforce"
            if os.path.exists(enforce_path):
                with open(enforce_path, "r") as f:
                    val = f.read().strip()
                selinux_info["mode"] = "enforcing" if val == "1" else "permissive"
        except Exception:
            pass
        
        # Get mode
        try:
            import subprocess
            result = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                selinux_info["mode"] = result.stdout.strip().lower()
        except:
            pass
        
        # Check if semanage is available
        try:
            import subprocess
            result = subprocess.run(["which", "semanage"], capture_output=True, timeout=5)
            selinux_info["tools_installed"] = result.returncode == 0
        except:
            pass
    
    return selinux_info


def _detect_directories():
    """Check required directories."""
    media_dir = os.environ.get("AST_MEDIA_DIR", "/mnt/asterisk_media/ai-generated")
    in_container = os.path.exists("/.dockerenv")
    
    # When running in container, check if media dir is mounted
    # The path inside container may differ from host path
    container_media_path = "/app/media" if in_container else media_dir
    
    # Also check the actual configured path
    paths_to_check = [media_dir, container_media_path, "/mnt/asterisk_media/ai-generated"]
    
    exists = False
    writable = False
    actual_path = media_dir
    
    for path in paths_to_check:
        if os.path.exists(path):
            exists = True
            writable = os.access(path, os.W_OK)
            actual_path = path
            break
    
    # If in container and no local path found, check via Docker client
    if in_container and not exists:
        try:
            client = docker.from_env()
            # Check if there's a volume mount for media
            for container in client.containers.list():
                if container.name in ["ai_engine", "admin_ui"]:
                    mounts = container.attrs.get("Mounts", [])
                    for mount in mounts:
                        if "asterisk_media" in mount.get("Source", "") or "ai-generated" in mount.get("Source", ""):
                            # Volume is mounted on host
                            exists = True
                            writable = True  # Assume writable if mounted
                            actual_path = mount.get("Source", media_dir)
                            break
        except:
            pass
    
    dir_info = {
        "media": {
            "path": actual_path,
            "exists": exists,
            "writable": writable,
            "status": "ok" if (exists and writable) else "warning",
            "in_container": in_container
        }
    }
    
    return dir_info


def _detect_asterisk():
    """Detect Asterisk installation."""
    asterisk_info = {
        "detected": False,
        "version": None,
        "config_dir": None,
        "freepbx": {
            "detected": False,
            "version": None
        }
    }
    
    # Check common paths
    asterisk_paths = ["/etc/asterisk", "/usr/local/etc/asterisk"]
    for path in asterisk_paths:
        if os.path.exists(path) and os.path.exists(os.path.join(path, "asterisk.conf")):
            asterisk_info["detected"] = True
            asterisk_info["config_dir"] = path
            break
    
    # Check for Asterisk binary
    try:
        import subprocess
        result = subprocess.run(["asterisk", "-V"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            asterisk_info["version"] = result.stdout.strip()
    except:
        pass
    
    # Check for FreePBX
    if os.path.exists("/etc/freepbx.conf") or os.path.exists("/etc/sangoma/pbx"):
        asterisk_info["freepbx"]["detected"] = True
        try:
            import subprocess
            result = subprocess.run(["fwconsole", "-V"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                asterisk_info["freepbx"]["version"] = result.stdout.strip()
        except:
            pass
    
    return asterisk_info


def _check_port(port: int, is_own_port: bool = False) -> dict:
    """Check if a port is in use and by what."""
    import socket
    
    result = {
        "port": port,
        "in_use": False,
        "is_own_port": is_own_port,
        "status": "ok"
    }
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            connect_result = s.connect_ex(('localhost', port))
            result["in_use"] = (connect_result == 0)
    except:
        pass
    
    # If it's our own port (admin-ui on 3003), it's expected to be in use
    if is_own_port and result["in_use"]:
        result["status"] = "ok"  # Expected
    elif result["in_use"]:
        result["status"] = "warning"
    
    return result


def _build_checks(os_info, docker_info, compose_info, selinux_info, dir_info, asterisk_info, platform_cfg: Optional[dict]) -> List[dict]:
    """Build list of checks with status and actions."""
    checks = []
    docker_cfg = (platform_cfg or {}).get("docker", {}) if isinstance(platform_cfg, dict) else {}
    compose_cfg = (platform_cfg or {}).get("compose", {}) if isinstance(platform_cfg, dict) else {}
    selinux_cfg = (platform_cfg or {}).get("selinux", {}) if isinstance(platform_cfg, dict) else {}
    firewall_cfg = (platform_cfg or {}).get("firewall", {}) if isinstance(platform_cfg, dict) else {}
    
    # Architecture check
    if os_info["arch"] != "x86_64":
        checks.append({
            "id": "architecture",
            "status": "error",
            "message": f"Unsupported architecture: {os_info['arch']} (x86_64 required)",
            "blocking": True,
            "action": None
        })
    else:
        checks.append({
            "id": "architecture",
            "status": "ok",
            "message": f"Architecture: {os_info['arch']}",
            "blocking": False,
            "action": None
        })
    
    # OS EOL check
    if os_info["is_eol"]:
        checks.append({
            "id": "os_eol",
            "status": "warning",
            "message": f"{os_info['id']} {os_info['version']} is EOL or nearing EOL",
            "blocking": False,
            "action": {
                "type": "link",
                "label": "Upgrade Guide",
                "value": "https://docs.docker.com/engine/install/"
            }
        })
    
    # Docker check
    if not docker_info["installed"]:
        docs_url = _github_docs_url(docker_cfg.get("aava_docs")) or "https://docs.docker.com/engine/install/"
        # Distinguish "not installed" vs "not reachable from Admin UI" (rootless socket mount is a common cause).
        if not docker_info.get("socket_present", True):
            checks.append({
                "id": "docker_socket",
                "status": "error",
                "message": "Docker socket not accessible to Admin UI (rootless Docker likely)",
                "blocking": True,
                "action": {
                    "type": "command",
                    "label": "Set DOCKER_SOCK and restart admin-ui",
                    "value": "export DOCKER_SOCK=/run/user/$(id -u)/docker.sock && docker compose up -d --force-recreate admin-ui",
                    "docs_url": _github_docs_url(docker_cfg.get("rootless_docs")) or _github_docs_url("docs/CROSS_PLATFORM_PLAN.md"),
                    "docs_label": "Rootless Docker docs",
                }
            })
        else:
            install_cmd = (docker_cfg.get("install_cmd") or "curl -fsSL https://get.docker.com | sh").strip()
            checks.append({
                "id": "docker_installed",
                "status": "error",
                "message": "Docker not detected by Admin UI",
                "blocking": True,
                "action": {
                    "type": "command",
                    "label": "Install Docker",
                    "value": install_cmd,
                    "docs_url": docs_url,
                    "docs_label": "AAVA installation docs",
                }
            })
    elif docker_info["status"] == "error":
        docs_url = _github_docs_url(docker_cfg.get("aava_docs")) or "https://docs.docker.com/engine/install/"
        start_cmd = docker_cfg.get("start_cmd") or "sudo systemctl start docker"
        rootless_start_cmd = docker_cfg.get("rootless_start_cmd")
        checks.append({
            "id": "docker_version",
            "status": "error",
            "message": docker_info["message"],
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Start Docker (or upgrade if needed)",
                "value": start_cmd,
                "rootless_value": rootless_start_cmd,
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif docker_info["status"] == "warning":
        checks.append({
            "id": "docker_version",
            "status": "warning",
            "message": docker_info["message"],
            "blocking": False,
            "action": None
        })
    else:
        checks.append({
            "id": "docker_version",
            "status": "ok",
            "message": f"Docker {docker_info['version']}",
            "blocking": False,
            "action": None
        })
    
    # Compose check
    if not compose_info["installed"]:
        docs_url = _github_docs_url(compose_cfg.get("aava_docs")) or "https://docs.docker.com/compose/install/"
        install_cmd = (compose_cfg.get("install_cmd") or "sudo apt-get install -y docker-compose-plugin").strip()
        checks.append({
            "id": "compose_installed",
            "status": "error",
            "message": "Docker Compose not installed",
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Install Docker Compose",
                "value": install_cmd,
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif compose_info["status"] == "error":
        docs_url = _github_docs_url(compose_cfg.get("aava_docs")) or "https://docs.docker.com/compose/install/"
        upgrade_cmd = (compose_cfg.get("upgrade_cmd") or compose_cfg.get("install_cmd") or "").strip()
        checks.append({
            "id": "compose_version",
            "status": "error",
            "message": compose_info["message"],
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Upgrade Docker Compose",
                "value": upgrade_cmd or "sudo apt-get update && sudo apt-get install -y docker-compose-plugin",
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif compose_info["status"] == "warning":
        checks.append({
            "id": "compose_version",
            "status": "warning",
            "message": compose_info["message"],
            "blocking": False,
            "action": None
        })
    else:
        checks.append({
            "id": "compose_version",
            "status": "ok",
            "message": f"Docker Compose {compose_info['version']}",
            "blocking": False,
            "action": None
        })
    
    # Media directory check
    media = dir_info["media"]
    if not media["exists"]:
        checks.append({
            "id": "media_directory",
            "status": "warning",
            "message": f"Media directory missing: {media['path']}",
            "blocking": False,
            "action": {
                "type": "command",
                "label": "Create Directory",
                "value": f"sudo mkdir -p {media['path']} && sudo chown -R $(id -u):$(id -g) {media['path']}",
                "rootless_value": f"mkdir -p {media['path']}",
                "docs_url": _github_docs_url((platform_cfg or {}).get("docker", {}).get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md"),
                "docs_label": "Media directory docs",
            }
        })
    elif not media["writable"]:
        checks.append({
            "id": "media_directory",
            "status": "warning",
            "message": f"Media directory not writable: {media['path']}",
            "blocking": False,
            "action": {
                "type": "command",
                "label": "Fix Permissions",
                "value": f"sudo chown -R $(id -u):$(id -g) {media['path']}",
                "rootless_value": None,
                "docs_url": _github_docs_url((platform_cfg or {}).get("docker", {}).get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md"),
                "docs_label": "Media directory docs",
            }
        })
    else:
        checks.append({
            "id": "media_directory",
            "status": "ok",
            "message": f"Media directory: {media['path']}",
            "blocking": False,
            "action": None
        })
    
    # SELinux check
    if selinux_info["present"] and selinux_info["mode"] == "enforcing":
        if not selinux_info["tools_installed"]:
            docs_url = _github_docs_url(selinux_cfg.get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md")
            install_cmd = (selinux_cfg.get("tools_install_cmd") or "sudo dnf install -y policycoreutils-python-utils").strip()
            checks.append({
                "id": "selinux",
                "status": "warning",
                "message": "SELinux enforcing but semanage not installed",
                "blocking": False,
                "action": {
                    "type": "command",
                    "label": "Install SELinux Tools",
                    "value": install_cmd,
                    "docs_url": docs_url,
                    "docs_label": "SELinux docs",
                }
            })
        else:
            docs_url = _github_docs_url(selinux_cfg.get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md")
            context_cmd_tmpl = selinux_cfg.get("context_cmd") or "sudo semanage fcontext -a -t container_file_t '{path}(/.*)?'"
            restore_cmd_tmpl = selinux_cfg.get("restore_cmd") or "sudo restorecon -Rv {path}"
            ctx_cmd = context_cmd_tmpl.format(path=media["path"])
            restore_cmd = restore_cmd_tmpl.format(path=media["path"])
            checks.append({
                "id": "selinux",
                "status": "warning",
                "message": "SELinux enforcing - context fix may be needed",
                "blocking": False,
                "action": {
                    "type": "command",
                    "label": "Fix SELinux Context",
                    "value": f"{ctx_cmd} && {restore_cmd}",
                    "docs_url": docs_url,
                    "docs_label": "SELinux docs",
                }
            })
    elif selinux_info["present"]:
        checks.append({
            "id": "selinux",
            "status": "ok",
            "message": f"SELinux: {selinux_info['mode'] or 'disabled'}",
            "blocking": False,
            "action": None
        })
    
    # Port check - port 3003 is admin-ui's own port, so it's expected to be in use
    port_check = _check_port(3003, is_own_port=True)
    # Only show port check if it's NOT in use (which would be unexpected for admin-ui)
    # or skip entirely since this is admin-ui's port
    # We'll show a success message instead
    checks.append({
        "id": "port_3003",
        "status": "ok",
        "message": "Admin UI port 3003 active",
        "blocking": False,
        "action": None
    })
    
    # Asterisk check
    if asterisk_info["detected"]:
        checks.append({
            "id": "asterisk",
            "status": "ok",
            "message": f"Asterisk config: {asterisk_info['config_dir']}",
            "blocking": False,
            "action": None
        })
        if asterisk_info["freepbx"]["detected"]:
            checks.append({
                "id": "freepbx",
                "status": "ok",
                "message": f"FreePBX: {asterisk_info['freepbx']['version'] or 'detected'}",
                "blocking": False,
                "action": None
            })
    
    return checks


@router.get("/platform")
async def get_platform():
    """
    Get platform detection and check results.
    AAVA-126: Cross-Platform Support
    """
    os_info = _detect_os()
    docker_info = _detect_docker()
    compose_info = _detect_compose()
    selinux_info = _detect_selinux()
    dir_info = _detect_directories()
    asterisk_info = _detect_asterisk()

    platforms = _load_platforms_yaml()
    platform_key = _select_platform_key(platforms, os_info.get("id"), os_info.get("family"))
    platform_cfg = _resolve_platform(platforms or {}, platform_key) if platform_key else None
    if isinstance(platform_cfg, dict):
        platform_cfg["_key"] = platform_key

    checks = _build_checks(os_info, docker_info, compose_info, selinux_info, dir_info, asterisk_info, platform_cfg)
    
    # Build summary
    passed = sum(1 for c in checks if c["status"] == "ok")
    warnings = sum(1 for c in checks if c["status"] == "warning")
    errors = sum(1 for c in checks if c["status"] == "error")
    blocking = sum(1 for c in checks if c.get("blocking", False))
    
    return {
        "platform": {
            "os": os_info,
            "docker": docker_info,
            "compose": compose_info,
            "selinux": selinux_info,
            "directories": dir_info,
            "asterisk": asterisk_info,
            "platform_key": platform_key,
        },
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "passed": passed,
            "warnings": warnings,
            "errors": errors,
            "blocking_errors": blocking,
            "ready": blocking == 0
        }
    }


@router.post("/preflight")
async def run_preflight():
    """
    Re-run preflight checks and return fresh results.
    AAVA-126: Cross-Platform Support
    """
    # Same as GET /platform but explicitly named for clarity
    return await get_platform()


class ContainerAction(BaseModel):
    containers: List[str] = None  # None = all


@router.post("/containers/start")
async def start_containers(action: ContainerAction = None):
    """Start containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    cmd = ["docker", "compose", "up", "-d"]
    if action and action.containers:
        cmd.extend(action.containers)
    
    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/containers/stop")
async def stop_containers(action: ContainerAction = None):
    """Stop containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    cmd = ["docker", "compose", "stop"]
    if action and action.containers:
        cmd.extend(action.containers)
    
    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/containers/restart-all")
async def restart_all_containers():
    """Restart all containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    try:
        # Stop
        subprocess.run(["docker", "compose", "stop"], cwd=project_root, timeout=60)
        # Start
        result = subprocess.run(["docker", "compose", "up", "-d"], cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AriTestRequest(BaseModel):
    host: str
    port: int = 8088
    username: str
    password: str
    scheme: str = "http"


@router.post("/test-ari")
async def test_ari_connection(request: AriTestRequest):
    """Test connection to Asterisk ARI endpoint"""
    import httpx
    
    try:
        # Build ARI URL
        ari_url = f"{request.scheme}://{request.host}:{request.port}/ari/asterisk/info"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                ari_url,
                auth=(request.username, request.password)
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "message": "Successfully connected to Asterisk ARI",
                    "asterisk_version": data.get("system", {}).get("version", "Unknown"),
                    "build": data.get("build", {})
                }
            elif response.status_code == 401:
                return {
                    "success": False,
                    "error": "Authentication failed - check username and password"
                }
            elif response.status_code == 403:
                return {
                    "success": False,
                    "error": "Access forbidden - check ARI user permissions"
                }
            else:
                return {
                    "success": False,
                    "error": f"Unexpected response: HTTP {response.status_code}"
                }
                
    except httpx.ConnectError:
        return {
            "success": False,
            "error": f"Connection refused - is Asterisk running at {request.host}:{request.port}?"
        }
    except httpx.ConnectTimeout:
        return {
            "success": False,
            "error": f"Connection timeout - check if {request.host}:{request.port} is reachable"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Connection failed: {str(e)}"
        }
