"""
Generic Webhook Tool - Post-call webhook notifications.

Sends call data to external systems after call ends (fire-and-forget).
"""

import os
import re
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from string import Template

import aiohttp

from src.tools.base import PostCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PostCallContext

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    """Configuration for a generic webhook tool instance."""
    name: str
    enabled: bool = True
    phase: str = "post_call"
    is_global: bool = False
    timeout_ms: int = 5000
    
    # HTTP request configuration
    url: str = ""
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    
    # Payload template (JSON string with variable substitution)
    payload_template: Optional[str] = None
    
    # Default content type
    content_type: str = "application/json"


class GenericWebhookTool(PostCallTool):
    """
    Generic webhook tool for post-call notifications.
    
    Configured via YAML, sends call data to external endpoints
    after the call ends. Fire-and-forget (no retry).
    
    Example config:
    ```yaml
    tools:
      n8n_webhook:
        kind: generic_webhook
        phase: post_call
        enabled: true
        is_global: true
        timeout_ms: 5000
        url: "https://n8n.example.com/webhook/call-completed"
        method: POST
        headers:
          Content-Type: "application/json"
          Authorization: "Bearer ${N8N_API_KEY}"
        payload_template: |
          {
            "schema_version": 1,
            "event_type": "call_completed",
            "call_id": "{call_id}",
            "caller_number": "{caller_number}",
            "caller_name": "{caller_name}",
            "call_duration": {call_duration},
            "call_outcome": "{call_outcome}",
            "transcript": {transcript_json},
            "summary": "{summary}",
            "context": "{context_name}",
            "provider": "{provider}",
            "timestamp": "{call_end_time}"
          }
    ```
    """
    
    def __init__(self, config: WebhookConfig):
        self.config = config
        self._definition = ToolDefinition(
            name=config.name,
            description=f"Webhook: {config.name}",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.POST_CALL,
            is_global=config.is_global,
            timeout_ms=config.timeout_ms,
        )
    
    @property
    def definition(self) -> ToolDefinition:
        return self._definition
    
    async def execute(self, context: PostCallContext) -> None:
        """
        Execute the webhook (fire-and-forget).
        
        Args:
            context: PostCallContext with comprehensive call data
        """
        if not self.config.enabled:
            logger.debug(f"Webhook tool disabled: {self.config.name}")
            return
        
        if not self.config.url:
            logger.warning(f"Webhook tool has no URL configured: {self.config.name}")
            return
        
        try:
            # Build request
            url = self._substitute_variables(self.config.url, context)
            headers = {
                k: self._substitute_variables(v, context)
                for k, v in self.config.headers.items()
            }
            
            # Ensure content-type is set
            if 'Content-Type' not in headers and 'content-type' not in headers:
                headers['Content-Type'] = self.config.content_type
            
            # Build payload
            payload = None
            if self.config.payload_template:
                payload = self._build_payload(context)
            else:
                # Default payload using context's to_payload_dict
                payload = json.dumps(context.to_payload_dict())
            
            logger.info(f"Sending webhook: {self.config.name} {self.config.method} {self._redact_url(url)}")
            
            # Make request (fire-and-forget)
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_ms / 1000.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method=self.config.method,
                    url=url,
                    headers=headers,
                    data=payload,
                ) as response:
                    status = response.status
                    
                    if 200 <= status < 300:
                        logger.info(f"Webhook sent successfully: {self.config.name} status={status}")
                    else:
                        # Log but don't fail (fire-and-forget)
                        body_preview = ""
                        try:
                            body = await response.text()
                            body_preview = body[:200] if body else ""
                        except Exception:
                            pass
                        logger.warning(f"Webhook returned non-2xx: {self.config.name} status={status} body={body_preview}")
        
        except aiohttp.ClientError as e:
            logger.warning(f"Webhook request failed: {self.config.name} error={e}")
        except Exception as e:
            logger.error(f"Webhook unexpected error: {self.config.name} error={e}", exc_info=True)
    
    def _build_payload(self, context: PostCallContext) -> str:
        """
        Build payload from template with variable substitution.
        """
        template = self.config.payload_template or "{}"
        
        # Get payload dict from context
        payload_vars = context.to_payload_dict()
        
        # Add schema_version
        payload_vars["schema_version"] = "1"
        
        # Substitute variables
        result = template
        
        # Simple variable substitution: {var_name}
        for key, value in payload_vars.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                # For JSON fields (ending with _json), don't quote
                if key.endswith("_json"):
                    result = result.replace(placeholder, str(value))
                else:
                    # Escape for JSON string
                    escaped = json.dumps(str(value))[1:-1]  # Remove outer quotes
                    result = result.replace(placeholder, escaped)
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            value = os.environ.get(var_name, "")
            return json.dumps(value)[1:-1]  # Escape for JSON
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _substitute_variables(self, template: str, context: PostCallContext) -> str:
        """
        Substitute variables in URL/headers.
        """
        result = template
        
        # Context variables
        replacements = {
            "{call_id}": context.call_id or "",
            "{caller_number}": context.caller_number or "",
            "{called_number}": context.called_number or "",
            "{caller_name}": context.caller_name or "",
            "{context_name}": context.context_name or "",
            "{provider}": context.provider or "",
            "{call_direction}": context.call_direction or "",
            "{campaign_id}": context.campaign_id or "",
            "{lead_id}": context.lead_id or "",
        }
        
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, "")
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _redact_url(self, url: str) -> str:
        """Redact sensitive parts of URL for logging."""
        redacted = re.sub(r'(api_key|apikey|key|token|auth)=([^&]+)', r'\1=***', url, flags=re.IGNORECASE)
        return redacted


def create_webhook_tool(name: str, config_dict: Dict[str, Any]) -> GenericWebhookTool:
    """
    Factory function to create a webhook tool from YAML config.
    
    Args:
        name: Tool name from YAML key
        config_dict: Tool configuration dictionary
    
    Returns:
        Configured GenericWebhookTool instance
    """
    config = WebhookConfig(
        name=name,
        enabled=config_dict.get('enabled', True),
        phase=config_dict.get('phase', 'post_call'),
        is_global=config_dict.get('is_global', False),
        timeout_ms=config_dict.get('timeout_ms', 5000),
        url=config_dict.get('url', ''),
        method=config_dict.get('method', 'POST'),
        headers=config_dict.get('headers', {}),
        payload_template=config_dict.get('payload_template'),
        content_type=config_dict.get('content_type', 'application/json'),
    )
    
    return GenericWebhookTool(config)
