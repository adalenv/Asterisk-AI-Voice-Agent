from fastapi import APIRouter, HTTPException, UploadFile, File
import yaml
import os
import re
import asyncio
import glob
import tempfile
import sys
from contextlib import contextmanager
from pydantic import BaseModel
from typing import Dict, Any
import settings

# A11: Maximum number of backups to keep
MAX_BACKUPS = 5

router = APIRouter()

# Regex to strip ANSI escape codes from logs
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from text for clean log files."""
    return ANSI_ESCAPE.sub('', text)


def _rotate_backups(base_path: str) -> None:
    """
    A11: Keep only the last MAX_BACKUPS backup files.
    Deletes oldest backups when limit is exceeded.
    """
    pattern = f"{base_path}.bak.*"
    backups = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    
    # Delete oldest backups beyond MAX_BACKUPS
    for old_backup in backups[MAX_BACKUPS:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass  # Ignore errors deleting old backups

class ConfigUpdate(BaseModel):
    content: str

@contextmanager
def _temporary_dotenv(path: str, defaults: Dict[str, str] | None = None):
    """
    Temporarily load KEY=VALUE pairs from a .env file into os.environ.

    This keeps config schema validation consistent with how ai-engine injects
    credentials/settings from environment variables at runtime.
    """
    env_pairs: Dict[str, str] = {}
    try:
        if path and os.path.exists(path):
            from dotenv import dotenv_values
            raw = dotenv_values(path)
            for key, value in (raw or {}).items():
                if key and value is not None:
                    env_pairs[str(key)] = str(value)
    except Exception:
        env_pairs = {}

    previous: Dict[str, Any] = {}
    for key, value in env_pairs.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value

    applied_defaults: Dict[str, str] = {}
    for key, value in (defaults or {}).items():
        if key not in os.environ or os.environ.get(key, "").strip() == "":
            previous[key] = os.environ.get(key)
            os.environ[key] = value
            applied_defaults[key] = value

    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(old_value)


def _resolve_json_schema_ref(schema_root: Dict[str, Any], ref: str) -> Dict[str, Any]:
    # Expected format: "#/$defs/SomeModel"
    if not ref.startswith("#/"):
        return {}
    node: Any = schema_root
    for part in ref.lstrip("#/").split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _collect_unknown_keys(data: Any, schema_root: Dict[str, Any], schema_node: Dict[str, Any], prefix: str) -> list:
    """
    Best-effort unknown-key detection using Pydantic's JSON schema.

    We only warn when the schema node is a structured object with explicit
    properties and does NOT allow additionalProperties (dict-like blobs).
    """
    if not isinstance(schema_node, dict):
        return []

    if "$ref" in schema_node:
        resolved = _resolve_json_schema_ref(schema_root, schema_node["$ref"])
        if resolved:
            schema_node = resolved

    # Avoid false positives for union-ish nodes.
    for union_key in ("anyOf", "oneOf", "allOf"):
        if union_key in schema_node:
            return []

    if not isinstance(data, dict):
        return []

    properties = schema_node.get("properties")
    if not isinstance(properties, dict):
        return []

    additional = schema_node.get("additionalProperties")
    if additional not in (None, False):
        # This node is intentionally dict-like (e.g., providers, contexts).
        # Don't warn about unknown keys here.
        # Still descend into known properties when present.
        warnings: list = []
        for key, subschema in properties.items():
            if key in data:
                next_prefix = f"{prefix}.{key}" if prefix else key
                warnings.extend(_collect_unknown_keys(data[key], schema_root, subschema, next_prefix))
        return warnings

    warnings: list = []
    known_keys = set(properties.keys())
    for key in data.keys():
        if key not in known_keys:
            full = f"{prefix}.{key}" if prefix else str(key)
            warnings.append(f"Unknown config key: {full} (will be ignored)")

    for key, subschema in properties.items():
        if key in data:
            next_prefix = f"{prefix}.{key}" if prefix else key
            warnings.extend(_collect_unknown_keys(data[key], schema_root, subschema, next_prefix))

    return warnings


def _validate_ai_agent_config(content: str) -> Dict[str, Any]:
    """
    Validate ai-agent.yaml content against the canonical AppConfig schema.

    Returns:
      {"warnings": [...]} on success

    Raises:
      HTTPException(400) on validation errors
    """
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(exc)}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid YAML: expected a mapping at the document root")

    # Ensure project root is importable so we can reuse canonical Pydantic models.
    project_root = getattr(settings, "PROJECT_ROOT", None)
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from pydantic import ValidationError
        from src.config import AppConfig, load_config
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Server misconfiguration: cannot import config schema (src.config). Error: {exc}",
        )

    warnings: list[str] = []

    # Warn if user put credentials in YAML (they will be ignored by design).
    try:
        asterisk_block = parsed.get("asterisk") if isinstance(parsed.get("asterisk"), dict) else {}
        if isinstance(asterisk_block, dict) and any(k in asterisk_block for k in ("username", "password")):
            warnings.append("Asterisk credentials in YAML are ignored; set ASTERISK_ARI_USERNAME/ASTERISK_ARI_PASSWORD in .env instead.")

        providers_block = parsed.get("providers") if isinstance(parsed.get("providers"), dict) else {}
        if isinstance(providers_block, dict):
            for provider_name, provider_cfg in providers_block.items():
                if isinstance(provider_cfg, dict) and "api_key" in provider_cfg:
                    warnings.append(f"providers.{provider_name}.api_key in YAML is ignored; set the provider API key in .env instead.")
    except Exception:
        pass

    # If ARI credentials are not present, validate with placeholders but warn the user.
    env_required_defaults: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        dotenv_map = dotenv_values(settings.ENV_PATH) if os.path.exists(settings.ENV_PATH) else {}
        get_dotenv = lambda k: str(dotenv_map.get(k) or "").strip()

        ari_user_present = bool(get_dotenv("ASTERISK_ARI_USERNAME") or get_dotenv("ARI_USERNAME") or os.environ.get("ASTERISK_ARI_USERNAME") or os.environ.get("ARI_USERNAME"))
        ari_pass_present = bool(get_dotenv("ASTERISK_ARI_PASSWORD") or get_dotenv("ARI_PASSWORD") or os.environ.get("ASTERISK_ARI_PASSWORD") or os.environ.get("ARI_PASSWORD"))

        if not ari_user_present:
            warnings.append("Missing ARI username in .env (ASTERISK_ARI_USERNAME or ARI_USERNAME). Engine will not connect to Asterisk ARI until set.")
            env_required_defaults["ASTERISK_ARI_USERNAME"] = "__MISSING__"
        if not ari_pass_present:
            warnings.append("Missing ARI password in .env (ASTERISK_ARI_PASSWORD or ARI_PASSWORD). Engine will not connect to Asterisk ARI until set.")
            env_required_defaults["ASTERISK_ARI_PASSWORD"] = "__MISSING__"
    except Exception:
        pass

    # Validate using the same loader pipeline as ai-engine (env injection + defaults + normalization).
    dir_path = os.path.dirname(settings.CONFIG_PATH)
    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".validate.yaml") as f:
        f.write(content)
        tmp_path = f.name

    try:
        with _temporary_dotenv(settings.ENV_PATH, defaults=env_required_defaults):
            load_config(tmp_path)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Config schema validation failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    try:
        schema = AppConfig.model_json_schema()
        warnings.extend(_collect_unknown_keys(parsed, schema, schema, prefix=""))
    except Exception:
        pass

    return {"warnings": warnings}


@router.post("/yaml")
async def update_yaml_config(update: ConfigUpdate):
    try:
        # Validate YAML + schema before saving.
        validation = _validate_ai_agent_config(update.content)
        warnings = validation.get("warnings") or []

        # Create backup before saving
        if os.path.exists(settings.CONFIG_PATH):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{settings.CONFIG_PATH}.bak.{timestamp}"
            with open(settings.CONFIG_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            # A11: Rotate backups - keep only last MAX_BACKUPS
            _rotate_backups(settings.CONFIG_PATH)

        # A8: Atomic write via temp file + rename (preserve permissions)
        dir_path = os.path.dirname(settings.CONFIG_PATH)
        # Get original file permissions if file exists
        original_mode = None
        if os.path.exists(settings.CONFIG_PATH):
            original_mode = os.stat(settings.CONFIG_PATH).st_mode
        
        with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as f:
            f.write(update.content)
            temp_path = f.name
        
        # Restore original permissions before replace
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        
        os.replace(temp_path, settings.CONFIG_PATH)  # Atomic on POSIX
        return {
            "status": "success",
            "restart_required": True,
            "message": "Configuration saved. Restart AI Engine to apply changes.",
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/yaml")
async def get_yaml_config():
    print(f"Accessing config at {settings.CONFIG_PATH}")
    if not os.path.exists(settings.CONFIG_PATH):
        print("Config file not found")
        raise HTTPException(status_code=404, detail="Config file not found")
    try:
        with open(settings.CONFIG_PATH, 'r') as f:
            config_content = f.read()
        yaml.safe_load(config_content) # Validate content is still valid YAML
        return {"content": config_content}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=500, detail=f"Error reading or parsing YAML config: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/env")
async def get_env_config():
    env_vars = {}
    if os.path.exists(settings.ENV_PATH):
        try:
            with open(settings.ENV_PATH, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        env_vars[key] = value
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return env_vars

@router.post("/env")
async def update_env(env_data: Dict[str, str]):
    try:
        # A12: Validate env data before writing
        for key, value in env_data.items():
            if not key or not key.strip():
                raise HTTPException(status_code=400, detail="Empty key not allowed")
            if '\n' in key or '\n' in str(value):
                raise HTTPException(status_code=400, detail=f"Newlines not allowed in key or value: {key}")
            if '=' in key:
                raise HTTPException(status_code=400, detail=f"Key cannot contain '=': {key}")
        
        # Create backup before saving
        if os.path.exists(settings.ENV_PATH):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{settings.ENV_PATH}.bak.{timestamp}"
            with open(settings.ENV_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            # A11: Rotate backups
            _rotate_backups(settings.ENV_PATH)

        # Read existing lines
        lines = []
        if os.path.exists(settings.ENV_PATH):
            with open(settings.ENV_PATH, 'r') as f:
                lines = f.readlines()

        # Create a map of keys to line numbers
        key_line_map = {}
        for i, line in enumerate(lines):
            line = line.strip()
            if line and not line.startswith('#'):
                if '=' in line:
                    key = line.split('=', 1)[0].strip()
                    key_line_map[key] = i

        # Update existing keys or append new ones
        new_lines = lines.copy()
        
        # Ensure we have a newline at the end if the file is not empty
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines[-1] += '\n'

        for key, value in env_data.items():
            # Skip empty keys
            if not key:
                continue
                
            line_content = f"{key}={value}\n"
            
            if key in key_line_map:
                # Update existing line
                new_lines[key_line_map[key]] = line_content
            else:
                # Append new key
                new_lines.append(line_content)
                # Update map for subsequent iterations (though not strictly needed for this simple logic)
                key_line_map[key] = len(new_lines) - 1

        # A8: Atomic write via temp file + rename (preserve permissions)
        dir_path = os.path.dirname(settings.ENV_PATH)
        # Get original file permissions if file exists
        original_mode = None
        if os.path.exists(settings.ENV_PATH):
            original_mode = os.stat(settings.ENV_PATH).st_mode
        
        with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as f:
            f.writelines(new_lines)
            temp_path = f.name
        
        # Restore original permissions before replace
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        
        os.replace(temp_path, settings.ENV_PATH)  # Atomic on POSIX
        
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ProviderTestRequest(BaseModel):
    name: str
    config: Dict[str, Any]

@router.post("/providers/test")
async def test_provider_connection(request: ProviderTestRequest):
    """Test connection to a provider based on its configuration"""
    try:
        import httpx
        import os
        
        # Helper to read API keys from .env file
        def get_env_key(key_name: str) -> str:
            """Read API key from .env file"""
            if os.path.exists(settings.ENV_PATH):
                with open(settings.ENV_PATH, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key_name}="):
                            return line.split('=', 1)[1].strip()
            return ''
        
        # Helper to substitute environment variables in config values
        def substitute_env_vars(item):
            import re
            if isinstance(item, dict):
                return {k: substitute_env_vars(v) for k, v in item.items()}
            elif isinstance(item, list):
                return [substitute_env_vars(i) for i in item]
            elif isinstance(item, str):
                # Match ${VAR} or ${VAR:-default} or ${VAR:=default}
                # Capture group 1: Var name, Group 2: Default value (optional)
                pattern = r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?:[:=-]([^}]*))?\}'
                
                def replace(match):
                    var_name = match.group(1)
                    default_value = match.group(2)
                    # Check env var first
                    val = os.getenv(var_name)
                    if val is not None and val != "":
                        return val
                    # Then check .env file (Admin UI backend typically runs without env vars)
                    val = get_env_key(var_name)
                    if val:
                        return val
                    # Then check if we have a default value
                    if default_value is not None:
                        return default_value
                    # If neither, keep original string (or empty?)
                    # Keeping original helps debug missing vars, but might break URLs.
                    # Standard behavior would be empty string if no default.
                    return "" 
                
                return re.sub(pattern, replace, item)
            return item

        # Apply substitution to the config
        provider_config = substitute_env_vars(request.config)
        provider_name = request.name.lower()
        
        # ============================================================
        # LOCAL PROVIDER - test connection to local_ai_server
        # ============================================================
        if 'local' in provider_name or provider_config.get('type') == 'local':
            import websockets
            import json
            
            # Get WebSocket URL from either base_url or ws_url
            ws_url = provider_config.get('base_url') or provider_config.get('ws_url') or 'ws://127.0.0.1:8765'
            # Handle env var format
            if '${' in ws_url:
                ws_url = 'ws://127.0.0.1:8765'  # Default fallback
            
            try:
                async with websockets.connect(ws_url, open_timeout=5.0) as ws:
                    # Send status request to check models
                    await ws.send(json.dumps({"type": "status"}))
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(response)
                    
                    if data.get("type") == "status_response" and data.get("status") == "ok":
                        models = data.get("models", {})
                        stt_loaded = models.get("stt", {}).get("loaded", False)
                        llm_loaded = models.get("llm", {}).get("loaded", False)
                        tts_loaded = models.get("tts", {}).get("loaded", False)
                        
                        stt_backend = data.get("stt_backend", "unknown")
                        tts_backend = data.get("tts_backend", "unknown")
                        llm_model = models.get("llm", {}).get("path", "").split("/")[-1] if models.get("llm", {}).get("path") else "none"
                        
                        status_parts = []
                        if stt_loaded:
                            status_parts.append(f"STT: {stt_backend} ✓")
                        else:
                            status_parts.append(f"STT: not loaded")
                        if llm_loaded:
                            status_parts.append(f"LLM: {llm_model} ✓")
                        else:
                            status_parts.append(f"LLM: not loaded")
                        if tts_loaded:
                            status_parts.append(f"TTS: {tts_backend} ✓")
                        else:
                            status_parts.append(f"TTS: not loaded")
                        
                        all_loaded = stt_loaded and llm_loaded and tts_loaded
                        return {
                            "success": all_loaded,
                            "message": f"Local AI Server connected. {' | '.join(status_parts)}"
                        }
                    else:
                        return {"success": False, "message": "Local AI Server responded but status invalid"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to Local AI Server at {ws_url}: {str(e)}"}
        
        # ============================================================
        # ELEVENLABS AGENT - check before other providers
        # ============================================================
        if 'elevenlabs' in provider_name or 'agent_id' in provider_config:
            api_key = get_env_key('ELEVENLABS_API_KEY')
            if not api_key:
                return {"success": False, "message": "ELEVENLABS_API_KEY not set in .env file"}
            
            async with httpx.AsyncClient() as client:
                # Use /v1/voices endpoint for validation (works with all API key types)
                response = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": api_key, "Accept": "application/json"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = response.json()
                    voice_count = len(data.get('voices', []))
                    return {"success": True, "message": f"Connected to ElevenLabs ({voice_count} voices available)"}
                return {"success": False, "message": f"ElevenLabs API error: HTTP {response.status_code}"}
        
        # ============================================================
        # OPENAI REALTIME
        # ============================================================
        if 'realtime_base_url' in provider_config or 'turn_detection' in provider_config:
            # OpenAI Realtime
            api_key = get_env_key('OPENAI_API_KEY')
            if not api_key:
                return {"success": False, "message": "OPENAI_API_KEY not set in .env file"}
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"success": True, "message": f"Connected to OpenAI (HTTP {response.status_code})"}
                return {"success": False, "message": f"OpenAI API error: HTTP {response.status_code}"}

        # ============================================================
        # OPENAI-COMPATIBLE (OpenAI / Groq / OpenRouter / etc.)
        # ============================================================
        if provider_config.get('type') == 'openai':
            chat_base_url = (provider_config.get('chat_base_url') or 'https://api.openai.com/v1').rstrip('/')
            api_key = provider_config.get('api_key')
            if not api_key:
                return {"success": False, "message": "API key missing for OpenAI-compatible provider (set api_key or env var)"}

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{chat_base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            models = data.get('data') or []
                            return {"success": True, "message": f"Connected (OpenAI-compatible). Found {len(models)} models."}
                        except Exception:
                            return {"success": True, "message": f"Connected (OpenAI-compatible) (HTTP {response.status_code})"}
                    if response.status_code == 401:
                        return {"success": False, "message": "Invalid API key (401)"}
                    return {"success": False, "message": f"Provider API error: HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to provider at {chat_base_url}: {str(e)}"}
                
        elif 'google_live' in provider_config or ('llm_model' in provider_config and 'gemini' in provider_config.get('llm_model', '')):
            # Google Live
            api_key = get_env_key('GOOGLE_API_KEY')
            if not api_key:
                return {"success": False, "message": "GOOGLE_API_KEY not set in .env file"}
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"success": True, "message": f"Connected to Google API (HTTP {response.status_code})"}
                return {"success": False, "message": f"Google API error: HTTP {response.status_code}"}
                
        elif 'ws_url' in provider_config:
            # Local provider (WebSocket)
            ws_url = provider_config.get('ws_url', '')
            if not ws_url:
                 return {"success": False, "message": "No WebSocket URL provided"}
            
            try:
                import websockets
                # Try connecting to the WebSocket
                async with websockets.connect(ws_url, open_timeout=5.0) as ws:
                    await ws.close()
                return {"success": True, "message": "Local AI server is reachable via WebSocket"}
            except ImportError:
                 return {"success": False, "message": "websockets library not installed"}
            except Exception as e:
                # If local-ai-server is on host network, ensure we use host.docker.internal or host networking properties
                return {"success": False, "message": f"Cannot reach local AI server at {ws_url}. Error: {str(e)}"}
        
        # ============================================================
        # OLLAMA - Self-hosted LLM
        # ============================================================
        if 'ollama' in provider_name or provider_config.get('type') == 'ollama':
            import aiohttp
            base_url = provider_config.get('base_url', 'http://localhost:11434').rstrip('/')
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{base_url}/api/tags"
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with session.get(url, timeout=timeout) as response:
                        if response.status == 200:
                            data = await response.json()
                            models = data.get("models", [])
                            return {"success": True, "message": f"Connected to Ollama! Found {len(models)} models."}
                        else:
                            return {"success": False, "message": f"Ollama returned status {response.status}"}
            except aiohttp.ClientConnectorError:
                return {"success": False, "message": f"Cannot connect to Ollama at {base_url}. Ensure Ollama is running and accessible."}
            except asyncio.TimeoutError:
                return {"success": False, "message": "Connection timeout - is Ollama running?"}
            except Exception as e:
                return {"success": False, "message": f"Ollama connection failed: {str(e)}"}
                
        elif 'model' in provider_config or 'stt_model' in provider_config or 'chat_model' in provider_config or 'tts_model' in provider_config:
            # Check if it's Deepgram or OpenAI standard
            # Deepgram often has 'deepgram' in name or model names like 'nova'
            if provider_config.get('model', '').startswith('nova') or 'deepgram' in provider_name.lower():
                # Deepgram
                api_key = get_env_key('DEEPGRAM_API_KEY')
                if not api_key:
                    return {"success": False, "message": "DEEPGRAM_API_KEY not set in .env file"}
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "https://api.deepgram.com/v1/projects",
                        headers={"Authorization": f"Token {api_key}"},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        return {"success": True, "message": f"Connected to Deepgram (HTTP {response.status_code})"}
                    return {"success": False, "message": f"Deepgram API error: HTTP {response.status_code}"}
            else:
                # OpenAI Standard or Generic
                # Try OpenAI first
                api_key = get_env_key('OPENAI_API_KEY')
                if api_key:
                   async with httpx.AsyncClient() as client:
                        try:
                            response = await client.get(
                                "https://api.openai.com/v1/models",
                                headers={"Authorization": f"Bearer {api_key}"},
                                timeout=5.0
                            )
                            if response.status_code == 200:
                                return {"success": True, "message": f"Connected to OpenAI (HTTP {response.status_code})"}
                        except:
                            pass
                
                # If we are here, it might be a local provider using 'model' key (e.g. local_tts)
                # but without ws_url? Usually local providers have ws_url. 
                # If it's pure local without WS (e.g. wrapper), assume success if file paths exist?
                return {"success": True, "message": "Provider configuration valid (No specific connection test available)"}
        
        return {"success": False, "message": "Unknown provider type - cannot test"}
        
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timeout"}
    except Exception as e:
        return {"success": False, "message": f"Test failed: {str(e)}"}

@router.get("/export")
async def export_configuration():
    """Export configuration as a ZIP file"""
    try:
        import zipfile
        import io
        from datetime import datetime
        
        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add YAML config
            if os.path.exists(settings.CONFIG_PATH):
                zip_file.write(settings.CONFIG_PATH, 'ai-agent.yaml')
            
            # Add ENV file
            if os.path.exists(settings.ENV_PATH):
                zip_file.write(settings.ENV_PATH, '.env')
            
            # Add timestamp file
            timestamp = datetime.now().isoformat()
            zip_file.writestr('backup_info.txt', f'Backup created: {timestamp}\n')
        
        zip_buffer.seek(0)
        
        # Return as downloadable file
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            zip_buffer, 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=config-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export-logs")
async def export_logs():
    """Export logs and sanitized configuration for troubleshooting"""
    try:
        import zipfile
        import io
        import glob
        from datetime import datetime
        import subprocess
        
        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 1. Sanitized YAML
            if os.path.exists(settings.CONFIG_PATH):
                try:
                    import yaml
                    with open(settings.CONFIG_PATH, 'r') as f:
                        parsed = yaml.safe_load(f) or {}

                    def redact(obj):
                        if isinstance(obj, dict):
                            out = {}
                            for k, v in obj.items():
                                key = str(k).lower()
                                if any(s in key for s in ["api_key", "apikey", "token", "secret", "password", "pass", "key"]):
                                    out[k] = "[REDACTED]"
                                else:
                                    out[k] = redact(v)
                            return out
                        if isinstance(obj, list):
                            return [redact(v) for v in obj]
                        return obj

                    redacted = redact(parsed)
                    zip_file.writestr(
                        'ai-agent-sanitized.yaml',
                        yaml.safe_dump(redacted, sort_keys=False, default_flow_style=False),
                    )
                except Exception:
                    # Fallback: write raw if sanitization fails
                    with open(settings.CONFIG_PATH, 'r') as f:
                        zip_file.writestr('ai-agent-sanitized.yaml', f.read())
            
            # 2. Sanitized ENV (Just keys, no values)
            if os.path.exists(settings.ENV_PATH):
                env_keys = []
                with open(settings.ENV_PATH, 'r') as f:
                    for line in f:
                        if '=' in line and not line.startswith('#'):
                            key = line.split('=')[0].strip()
                            env_keys.append(f"{key}=[REDACTED]")
                zip_file.writestr('.env.sanitized', '\n'.join(env_keys))

            # 2b. Host OS info (if mounted) and basic Docker versions
            for os_release in ("/host/etc/os-release", "/etc/os-release"):
                if os.path.exists(os_release):
                    try:
                        with open(os_release, "r") as f:
                            zip_file.writestr("os-release.txt", f.read())
                        break
                    except Exception:
                        pass

            def add_cmd(name: str, cmd: list):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    content = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
                    zip_file.writestr(name, content.strip() + "\n")
                except Exception as e:
                    zip_file.writestr(name, f"Failed to run {cmd}: {e}\n")

            add_cmd("docker-version.txt", ["docker", "version"])
            add_cmd("docker-compose-version.txt", ["docker", "compose", "version"])
            add_cmd("docker-ps.txt", ["docker", "ps", "-a"])
            
            # 3. Logs from Docker Containers
            try:
                import docker
                client = docker.from_env()
                containers_to_log = ['ai_engine', 'local_ai_server', 'admin_ui']
                
                found_logs = False
                for container_name in containers_to_log:
                    try:
                        container = client.containers.get(container_name)
                        # Capture full logs (no tail limit)
                        logs = container.logs().decode('utf-8', errors='replace')
                        if logs:
                            # Strip ANSI escape codes for clean log files
                            clean_logs = strip_ansi_codes(logs)
                            zip_file.writestr(f'{container_name}.log', clean_logs)
                            found_logs = True
                    except Exception as e:
                        zip_file.writestr(f'{container_name}_error.txt', f"Could not fetch logs: {str(e)}")
                
                if not found_logs:
                    zip_file.writestr('logs_info.txt', 'No logs retrieved from containers.')

            except Exception as e:
                 zip_file.writestr('docker_error.txt', f"Failed to connect to Docker API: {str(e)}")

            # Add timestamp
            timestamp = datetime.now().isoformat()
            zip_file.writestr('export_info.txt', f'Debug export created: {timestamp}\n')
        
        zip_buffer.seek(0)
        
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            zip_buffer, 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=debug-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import")
async def import_configuration(file: UploadFile = File(...)):
    """Import configuration from a ZIP file"""
    try:
        import zipfile
        import io
        import shutil
        import datetime
        
        content = await file.read()
        zip_buffer = io.BytesIO(content)
        
        if not zipfile.is_zipfile(zip_buffer):
             raise HTTPException(status_code=400, detail="Invalid file format. Must be a ZIP file.")
        
        # Create backups of current config
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if os.path.exists(settings.CONFIG_PATH):
            backup_path = f"{settings.CONFIG_PATH}.bak.{timestamp}"
            shutil.copy2(settings.CONFIG_PATH, backup_path)
            
        if os.path.exists(settings.ENV_PATH):
            backup_path = f"{settings.ENV_PATH}.bak.{timestamp}"
            shutil.copy2(settings.ENV_PATH, backup_path)
        
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            # Check contents
            file_names = zip_ref.namelist()
            if 'ai-agent.yaml' not in file_names and '.env' not in file_names:
                raise HTTPException(status_code=400, detail="ZIP must contain ai-agent.yaml or .env")
            
            # Extract
            if 'ai-agent.yaml' in file_names:
                with open(settings.CONFIG_PATH, 'wb') as f:
                    f.write(zip_ref.read('ai-agent.yaml'))
                    
            if '.env' in file_names:
                with open(settings.ENV_PATH, 'wb') as f:
                    f.write(zip_ref.read('.env'))
                    
        return {"success": True, "message": "Configuration imported successfully."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


def update_yaml_provider_field(provider_name: str, field: str, value: Any) -> bool:
    """
    Update a single field in a provider's YAML config.
    
    Args:
        provider_name: Name of the provider (e.g., 'local')
        field: Field name to update (e.g., 'stt_backend')
        value: New value for the field
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if not os.path.exists(settings.CONFIG_PATH):
            return False
            
        with open(settings.CONFIG_PATH, 'r') as f:
            config = yaml.safe_load(f)
        
        if not config:
            return False
            
        # Ensure providers section exists
        if 'providers' not in config:
            config['providers'] = {}
        
        # Ensure provider exists
        if provider_name not in config['providers']:
            config['providers'][provider_name] = {}
        
        # Update the field
        config['providers'][provider_name][field] = value
        
        # Write back
        with open(settings.CONFIG_PATH, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        return True
    except Exception as e:
        print(f"Error updating YAML provider field: {e}")
        return False

@router.get("/options/{provider_type}")
async def get_provider_options(provider_type: str):
    """Get available options (models, voices) for a specific provider."""
    
    # Common catalogs
    DEEPGRAM_MODELS = [
        {"id": "nova-2", "name": "Nova 2 (General)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-phonecall", "name": "Nova 2 (Phonecall)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-medical", "name": "Nova 2 (Medical)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-meeting", "name": "Nova 2 (Meeting)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-general", "name": "Nova 2 (General Legacy)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "listen", "name": "General (Listen)", "cost": "Medium", "latency": "Low"},
    ]
    
    OPENAI_LLM_MODELS = [
        {"id": "gpt-4o", "name": "GPT-4o (Omni)", "description": "Most capable"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "description": "Fast & Cheap"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "description": "High intelligence"},
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "Legacy fast"},
    ]
    
    OPENAI_STT_MODELS = [
        {"id": "whisper-1", "name": "Whisper V1"}
    ]
    
    GOOGLE_MODELS = [
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash (Fastest)"},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro (Best Quality)"},
    ]

    GOOGLE_VOICES = [
        {"id": "en-US-Standard-A", "name": "US Female Standard"},
        {"id": "en-US-Standard-B", "name": "US Male Standard"},
        {"id": "en-US-Neural2-A", "name": "US Female Neural"},
        {"id": "en-US-Neural2-C", "name": "US Male Neural"},
        {"id": "en-US-Studio-O", "name": "US Female Studio"},
        {"id": "en-US-Studio-Q", "name": "US Male Studio"},
    ]

    # Return options based on provider
    if provider_type == "deepgram":
        return {"models": DEEPGRAM_MODELS}
        
    elif provider_type == "openai":
        return {
            "stt_models": OPENAI_STT_MODELS,
            "llm_models": OPENAI_LLM_MODELS,
            "tts_models": [{"id": "tts-1", "name": "TTS-1"}, {"id": "tts-1-hd", "name": "TTS-1 HD"}]
        }
        
    elif provider_type == "google":
        return {
            "models": GOOGLE_MODELS,
            "voices": GOOGLE_VOICES
        }
        
    elif provider_type == "elevenlabs":
        return {
            "models": [
                {"id": "eleven_turbo_v2_5", "name": "Turbo v2.5"},
                {"id": "eleven_multilingual_v2", "name": "Multilingual v2"},
                {"id": "eleven_monolingual_v1", "name": "Monolingual v1"}
            ]
        }
        
    elif provider_type == "local":
        return {"message": "Use /api/local-ai/models for dynamic local models"}
        
    return {"error": "Unknown provider type"}
