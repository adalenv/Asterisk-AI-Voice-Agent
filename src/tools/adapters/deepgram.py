"""
Deepgram Voice Agent adapter for tool calling.

Handles translation between unified tool format and Deepgram's function calling format.
"""

from typing import Dict, Any, List
from src.tools.registry import ToolRegistry
from src.tools.context import ToolExecutionContext
import json
import logging

logger = logging.getLogger(__name__)


class DeepgramToolAdapter:
    """
    Adapter for Deepgram Voice Agent API tool calling.
    
    Translates between unified tool format and Deepgram's specific event format.
    """
    
    def __init__(self, registry: ToolRegistry):
        """
        Initialize adapter with tool registry.
        
        Args:
            registry: ToolRegistry instance with registered tools
        """
        self.registry = registry
    
    def get_tools_config(self) -> List[Dict[str, Any]]:
        """
        Get tools configuration in Deepgram format.
        
        Returns:
            List of tool schemas for Deepgram session initialization
        
        Example:
            [
                {
                    "name": "transfer_call",
                    "description": "Transfer caller to extension",
                    "parameters": {
                        "type": "object",
                        "properties": {...},
                        "required": [...]
                    }
                }
            ]
        """
        schemas = self.registry.to_deepgram_schema()
        logger.debug(f"Generated Deepgram schemas for {len(schemas)} tools")
        return schemas
    
    async def handle_tool_call_event(
        self,
        event: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle function call event from Deepgram.
        
        Per Deepgram docs, event format is:
        {
            "type": "function_call",
            "id": "call_123456",
            "function_call": {
                "name": "transfer_call",
                "arguments": {
                    "target": "2765"
                }
            }
        }
        
        Args:
            event: Function call event from Deepgram
            context: Execution context dict with:
                - call_id
                - caller_channel_id
                - bridge_id
                - session_store
                - ari_client
                - config
        
        Returns:
            Dict with function_call_id and result for sending back to Deepgram
        """
        # Extract function call details per Deepgram spec
        function_call_id = event.get('id')
        function_call = event.get('function_call', {})
        function_name = function_call.get('name')
        parameters = function_call.get('arguments', {})
        
        logger.info(f"🔧 Deepgram tool call: {function_name}({parameters})", call_id=function_call_id)
        
        # Get tool from registry
        tool = self.registry.get(function_name)
        if not tool:
            error_msg = f"Unknown tool: {function_name}"
            logger.error(error_msg)
            return {
                "function_call_id": function_call_id,
                "status": "error",
                "message": error_msg
            }
        
        # Build execution context
        exec_context = ToolExecutionContext(
            call_id=context['call_id'],
            caller_channel_id=context.get('caller_channel_id'),
            bridge_id=context.get('bridge_id'),
            session_store=context['session_store'],
            ari_client=context['ari_client'],
            config=context.get('config'),
            provider_name="deepgram",
            user_input=context.get('user_input')
        )
        
        # Execute tool
        try:
            result = await tool.execute(parameters, exec_context)
            logger.info(f"✅ Tool {function_name} executed: {result.get('status')}")
            result['function_call_id'] = function_call_id
            return result
        except Exception as e:
            error_msg = f"Tool execution failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "function_call_id": function_call_id,
                "status": "error",
                "message": error_msg,
                "error": str(e)
            }
    
    async def send_tool_result(
        self,
        result: Dict[str, Any],
        context: Dict[str, Any]
    ) -> None:
        """
        Send tool execution result back to Deepgram.
        
        Per Deepgram docs, format must be:
        {
            "type": "function_call_result",
            "id": "call_123456",  // The function_call_id from the request
            "function_call_result": {
                // Tool's result data
            }
        }
        
        Args:
            result: Tool execution result (must include function_call_id)
            context: Context dict with websocket connection
        """
        websocket = context.get('websocket')
        if not websocket:
            logger.error("No websocket in context, cannot send tool result")
            return
        
        # Extract function_call_id from result
        function_call_id = result.pop('function_call_id', None)
        if not function_call_id:
            logger.error("No function_call_id in result, cannot send response")
            return
        
        # Build response per Deepgram spec
        response = {
            "type": "function_call_result",
            "id": function_call_id,
            "function_call_result": result  # Send the entire result as function output
        }
        
        try:
            await websocket.send(json.dumps(response))
            logger.info(f"✅ Sent tool result to Deepgram: {result.get('status')}", function_call_id=function_call_id)
        except Exception as e:
            logger.error(f"Failed to send tool result to Deepgram: {e}", exc_info=True)
