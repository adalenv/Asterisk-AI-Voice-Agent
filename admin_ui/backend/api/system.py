from fastapi import APIRouter, HTTPException
import docker
from typing import List
from pydantic import BaseModel
import psutil
import os

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
                        # Parse ISO format with timezone
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

@router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str):
    """Restart a container using docker-compose with proper stop/remove/recreate."""
    import subprocess
    
    # Map container names to docker-compose service names
    service_map = {
        "ai_engine": "ai-engine",
        "admin_ui": "admin-ui",
        "local_ai_server": "local-ai-server"
    }
    
    service_name = service_map.get(container_id)
    
    # If not in map, it might be an ID or a raw name.
    # Try to resolve ID to name if possible.
    if not service_name:
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            # Strip leading slash
            name = container.name.lstrip('/')
            # Try map again with name, or use name directly
            service_name = service_map.get(name, name)
        except:
            # Fallback to using the input as is
            service_name = container_id
    
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    print(f"DEBUG: Restarting {service_name} from {project_root}")
    
    # Map service names to container names
    container_name_map = {
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui", 
        "local-ai-server": "local_ai_server"
    }
    container_name = container_name_map.get(service_name, service_name.replace("-", "_"))
    
    try:
        # Step 1: Stop the container using docker directly (more reliable)
        stop_result = subprocess.run(
            ["docker", "stop", container_name],
            capture_output=True,
            text=True,
            timeout=60
        )
        print(f"DEBUG: docker stop returncode={stop_result.returncode}")
        
        # Step 2: Force remove the container using docker directly
        rm_result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        print(f"DEBUG: docker rm returncode={rm_result.returncode}")
        
        # Step 3: Bring the service back up
        up_result = subprocess.run(
            ["docker", "compose", "up", "-d", service_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        print(f"DEBUG: up returncode={up_result.returncode}")
        print(f"DEBUG: up stdout={up_result.stdout}")
        print(f"DEBUG: up stderr={up_result.stderr}")
        
        if up_result.returncode == 0:
            return {"status": "success", "output": up_result.stdout or "Container restarted"}
        else:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to restart: {up_result.stderr or up_result.stdout}"
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout waiting for container restart")
    except FileNotFoundError:
        # Fallback to Docker API if docker-compose not available
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            container.restart()
            return {"status": "success", "method": "docker-api"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
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
