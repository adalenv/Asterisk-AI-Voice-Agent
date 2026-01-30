"""
Check Extension Status Tool - Query Asterisk device state for an extension.

Purpose (AAVA-53):
- Allow the AI agent to check whether an internal extension is available (e.g., NOT_INUSE)
  during a call, and then decide whether to transfer or continue the conversation.

Notes:
- Uses ARI deviceStates API (GET /ari/deviceStates/{deviceStateName}).
- Device state name is usually "<TECH>/<EXT>" (e.g., "PJSIP/2765" or "SIP/6000").
- Tech selection should be configurable per extension via Admin UI (stored under tools.extensions.internal).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import structlog

from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory, ToolPhase
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


def _parse_dial_string_tech(dial_string: str) -> Optional[str]:
    dial_string = (dial_string or "").strip()
    if not dial_string:
        return None
    # Common patterns: "PJSIP/2765", "SIP/6000", "PJSIP/2765@from-internal"
    if "/" not in dial_string:
        return None
    tech = dial_string.split("/", 1)[0].strip()
    if not tech:
        return None
    return tech


def _resolve_device_state_id(
    *,
    extension: str,
    extensions_config: Dict[str, Any],
    tech: str = "",
    device_state_id: str = "",
) -> Tuple[str, str]:
    """
    Resolve the ARI deviceStateName to query.

    Returns:
        (device_state_id, resolution_source)
    """
    extension = (extension or "").strip()
    if not extension:
        return "", ""

    if device_state_id:
        return device_state_id.strip(), "parameter.device_state_id"

    entry = None
    if isinstance(extensions_config, dict):
        entry = extensions_config.get(extension)

    # Best-effort: if the extension isn't keyed by its number, try to match by dial_string suffix.
    if entry is None and isinstance(extensions_config, dict):
        for _, cfg in extensions_config.items():
            if not isinstance(cfg, dict):
                continue
            dial_string = str(cfg.get("dial_string", "") or "")
            if dial_string.endswith(f"/{extension}") or f"/{extension}@" in dial_string:
                entry = cfg
                break

    if isinstance(entry, dict):
        cfg_state_id = str(entry.get("device_state_id", "") or "").strip()
        if cfg_state_id:
            return cfg_state_id, "config.device_state_id"

        cfg_tech = str(entry.get("device_state_tech", "") or "").strip()
        if cfg_tech and cfg_tech.lower() != "auto":
            tech = cfg_tech
            return f"{tech.upper()}/{extension}", "config.device_state_tech"

        dial_string = str(entry.get("dial_string", "") or "")
        dial_tech = _parse_dial_string_tech(dial_string)
        if dial_tech:
            tech = dial_tech
            return f"{tech.upper()}/{extension}", "config.dial_string"

    if tech:
        return f"{tech.upper()}/{extension}", "parameter.tech"

    return "", ""


class CheckExtensionStatusTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="check_extension_status",
            description=(
                "Check if an internal extension is available by querying Asterisk device state. "
                "Use this before attempting a transfer to a live agent."
            ),
            category=ToolCategory.TELEPHONY,
            phase=ToolPhase.IN_CALL,
            is_global=True,
            requires_channel=False,
            max_execution_time=10,
            parameters=[
                ToolParameter(
                    name="extension",
                    type="string",
                    description="Extension number to check (e.g., '2765').",
                    required=True,
                ),
                ToolParameter(
                    name="tech",
                    type="string",
                    description="Channel technology for device state (e.g., 'PJSIP', 'SIP'). Defaults to auto via config/dial_string.",
                    required=False,
                ),
                ToolParameter(
                    name="device_state_id",
                    type="string",
                    description="Optional explicit device state id to query (e.g., 'PJSIP/2765'). Overrides tech/extension resolution.",
                    required=False,
                ),
            ],
        )

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        await self.validate_parameters(parameters)

        if not context.ari_client:
            return {"status": "error", "message": "ARI client not available in tool context"}

        extension = str(parameters.get("extension", "") or "").strip()
        tech = str(parameters.get("tech", "") or "").strip()
        device_state_id = str(parameters.get("device_state_id", "") or "").strip()

        extensions_cfg = context.get_config_value("tools.extensions.internal", {}) or {}
        resolved_id, source = _resolve_device_state_id(
            extension=extension,
            extensions_config=extensions_cfg,
            tech=tech,
            device_state_id=device_state_id,
        )
        if not resolved_id:
            return {
                "status": "error",
                "message": (
                    "Unable to resolve device state id. Provide tech/device_state_id, or configure "
                    "tools.extensions.internal.<ext>.device_state_tech/device_state_id."
                ),
                "extension": extension,
            }

        # ARI expects deviceStateName in the URL path, so URL-encode slashes.
        encoded = quote(resolved_id, safe="")

        try:
            resp = await context.ari_client.send_command(
                method="GET",
                resource=f"deviceStates/{encoded}",
            )
        except Exception as exc:
            logger.error("Device state query failed", call_id=context.call_id, extension=extension, device_state_id=resolved_id, exc_info=True)
            return {"status": "error", "message": "ARI device state query failed", "error": str(exc)}

        # Common ARI response: {"name":"PJSIP/2765","state":"NOT_INUSE"}
        state = ""
        name = ""
        if isinstance(resp, dict):
            name = str(resp.get("name", "") or "")
            state = str(resp.get("state", "") or "")
        state_norm = state.strip().upper()

        # Conservative availability mapping:
        # - NOT_INUSE is clearly available.
        # - INUSE/BUSY/RINGING/UNAVAILABLE are not.
        available = state_norm == "NOT_INUSE"

        result = {
            "status": "success",
            "extension": extension,
            "device_state_id": resolved_id,
            "resolution_source": source,
            "device_state_name": name or resolved_id,
            "device_state": state_norm or state,
            "available": available,
        }

        logger.info(
            "Extension device state",
            call_id=context.call_id,
            extension=extension,
            device_state_id=resolved_id,
            state=state_norm or state,
            available=available,
            source=source,
        )
        return result

