"""
Call Transfer Tool - Transfer caller to internal extension or external number.

Supports warm and blind transfer modes.
"""

from typing import Dict, Any, Optional
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext
import structlog
import asyncio

logger = structlog.get_logger(__name__)


class TransferCallTool(Tool):
    """
    Transfer caller to another extension or department.
    
    Features:
    - Warm transfer (AI stays on bridge, confirms connection)
    - Blind transfer (immediate redirect)
    - Hold music during transfer
    - Department name → extension resolution
    """
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="transfer_call",
            description="Transfer the caller to another extension or department. Use this when the caller asks to speak with a live person, agent, or specific department.",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=45,
            parameters=[
                ToolParameter(
                    name="target",
                    type="string",
                    description="Extension number or department name (e.g., '2765', 'sales', 'support')",
                    required=True
                ),
                ToolParameter(
                    name="mode",
                    type="string",
                    description="Transfer mode: 'warm' (announce and confirm) or 'blind' (immediate)",
                    enum=["warm", "blind"],
                    default="warm"
                )
            ]
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute call transfer.
        
        Workflow (warm transfer):
        1. Resolve target to extension
        2. Validate target is allowed
        3. Start hold music for caller
        4. Originate call to target
        5. Wait for target to answer
        6. Add target to bridge
        7. Stop hold music
        8. Return success (AI will announce)
        
        Args:
            parameters: {target: str, mode: str}
            context: Tool execution context
        
        Returns:
            {
                status: "success" | "failed" | "error",
                message: "Human-readable message",
                extension: "2765",
                transfer_mode: "warm"
            }
        """
        await self.validate_parameters(parameters)
        
        target = parameters['target']
        mode = parameters.get('mode', 'warm')
        
        logger.info(f"🔀 Transfer requested: {target} ({mode} mode)", 
                   call_id=context.call_id)
        
        try:
            # 1. Resolve target to extension
            extension_info = await self._resolve_target(target, context)
            if not extension_info:
                return {
                    "status": "error",
                    "message": f"I'm sorry, I couldn't find '{target}'. Please try again or ask for help."
                }
            
            extension = extension_info['extension']
            dial_string = extension_info['dial_string']
            
            logger.info(f"Resolved {target} → {extension} ({dial_string})")
            
            # 2. Execute transfer based on mode
            if mode == "warm":
                result = await self._warm_transfer(extension, dial_string, extension_info, context)
            else:
                result = await self._blind_transfer(extension, dial_string, context)
            
            return result
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}", exc_info=True, call_id=context.call_id)
            return {
                "status": "error",
                "message": f"I encountered an error while transferring. Please hold while I try again.",
                "error": str(e)
            }
    
    async def _resolve_target(
        self,
        target: str,
        context: ToolExecutionContext
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve target (extension or department name) to dial information.
        
        Args:
            target: Extension number or department name
            context: Execution context
        
        Returns:
            {
                extension: "2765",
                name: "Live Agent",
                dial_string: "PJSIP/2765",
                context: "from-internal"
            }
            or None if not found
        """
        # Load extensions config
        # DEBUG: Check what config looks like
        logger.info(f"DEBUG: context.config type: {type(context.config)}", call_id=context.call_id)
        logger.info(f"DEBUG: context.config is None: {context.config is None}", call_id=context.call_id)
        if context.config:
            has_tools = 'tools' in context.config if isinstance(context.config, dict) else hasattr(context.config, 'tools')
            logger.info(f"DEBUG: config has 'tools': {has_tools}", call_id=context.call_id)
        
        extensions_config = context.get_config_value('tools.extensions.internal', {})
        logger.info(f"DEBUG: extensions_config result: {extensions_config}", call_id=context.call_id)
        
        if not extensions_config:
            logger.warning("No extensions configured in tools.extensions.internal")
            return None
        
        # Try direct extension lookup
        if target in extensions_config:
            ext_config = extensions_config[target]
            return {
                'extension': target,
                'name': ext_config.get('name', target),
                'dial_string': ext_config.get('dial_string', f"PJSIP/{target}"),
                'context': ext_config.get('context', 'from-internal')
            }
        
        # Try department name/alias lookup
        target_lower = target.lower()
        for ext_num, ext_config in extensions_config.items():
            # Check name match
            if ext_config.get('name', '').lower() == target_lower:
                return {
                    'extension': ext_num,
                    'name': ext_config.get('name', ext_num),
                    'dial_string': ext_config.get('dial_string', f"PJSIP/{ext_num}"),
                    'context': ext_config.get('context', 'from-internal')
                }
            
            # Check aliases
            aliases = ext_config.get('aliases', [])
            if target_lower in [a.lower() for a in aliases]:
                return {
                    'extension': ext_num,
                    'name': ext_config.get('name', ext_num),
                    'dial_string': ext_config.get('dial_string', f"PJSIP/{ext_num}"),
                    'context': ext_config.get('context', 'from-internal')
                }
        
        logger.warning(f"Target '{target}' not found in extensions config")
        return None
    
    async def _warm_transfer(
        self,
        extension: str,
        dial_string: str,
        extension_info: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute warm transfer (AI stays on bridge until confirmed).
        
        Args:
            extension: Target extension number
            dial_string: Full dial string (e.g., "PJSIP/2765")
            extension_info: Extension configuration
            context: Execution context
        
        Returns:
            Result dict
        """
        logger.info(f"Starting warm transfer to {extension}", call_id=context.call_id)
        
        session = context.get_session()
        caller_channel_id = context.caller_channel_id
        bridge_id = context.bridge_id
        
        # 1. Start hold music on caller
        logger.debug(f"Starting hold music on {caller_channel_id}")
        await self._start_moh(caller_channel_id, context)
        
        # 2. Originate call to target
        logger.info(f"Originating call to {dial_string}")
        target_channel = await self._originate_call(
            dial_string=dial_string,
            context_name=extension_info['context'],
            timeout=30,
            ari_client=context.ari_client
        )
        
        if not target_channel:
            # Target didn't answer
            await self._stop_moh(caller_channel_id, context)
            return {
                "status": "failed",
                "message": f"{extension_info['name']} is not available right now. Would you like to leave a message?",
                "extension": extension
            }
        
        target_channel_id = target_channel['id']
        logger.info(f"Target answered: {target_channel_id}")
        
        # 3. Add target to bridge
        logger.debug(f"Adding {target_channel_id} to bridge {bridge_id}")
        await context.ari_client.add_channel_to_bridge(bridge_id, target_channel_id)
        
        # 4. Stop hold music
        await self._stop_moh(caller_channel_id, context)
        
        # 5. Update session to track transfer
        context.update_session(
            transfer_active=True,
            transfer_target=extension,
            transfer_channel_id=target_channel_id,
            transfer_mode="warm"
        )
        
        logger.info(f"✅ Warm transfer completed to {extension}", call_id=context.call_id)
        
        # AI will now announce transfer and can exit gracefully
        return {
            "status": "success",
            "message": f"I've connected you to {extension_info['name']}. I'll step aside now.",
            "extension": extension,
            "transfer_mode": "warm",
            "target_name": extension_info['name']
        }
    
    async def _blind_transfer(
        self,
        extension: str,
        dial_string: str,
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute blind transfer (immediate redirect).
        
        Args:
            extension: Target extension number
            dial_string: Full dial string
            context: Execution context
        
        Returns:
            Result dict
        """
        logger.info(f"Starting blind transfer to {extension}", call_id=context.call_id)
        
        caller_channel_id = context.caller_channel_id
        
        # Redirect channel to extension
        # Using ARI redirect endpoint
        result = await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{caller_channel_id}/redirect",
            data={
                "endpoint": dial_string
            }
        )
        
        if result and result.get('status') == 204:
            logger.info(f"✅ Blind transfer completed to {extension}", call_id=context.call_id)
            return {
                "status": "success",
                "message": f"Transferring you now.",
                "extension": extension,
                "transfer_mode": "blind"
            }
        else:
            logger.error(f"Blind transfer failed: {result}")
            return {
                "status": "failed",
                "message": f"Unable to transfer. Please hold.",
                "extension": extension
            }
    
    async def _start_moh(self, channel_id: str, context: ToolExecutionContext):
        """Start music on hold for channel."""
        moh_class = context.get_config_value('tools.transfer_call.hold_music_class', 'default')
        
        try:
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{channel_id}/moh",
                params={"mohClass": moh_class}
            )
            logger.debug(f"Started MOH on {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to start MOH: {e}")
    
    async def _stop_moh(self, channel_id: str, context: ToolExecutionContext):
        """Stop music on hold for channel."""
        try:
            await context.ari_client.send_command(
                method="DELETE",
                resource=f"channels/{channel_id}/moh"
            )
            logger.debug(f"Stopped MOH on {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to stop MOH: {e}")
    
    async def _originate_call(
        self,
        dial_string: str,
        context_name: str,
        timeout: int,
        ari_client: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Originate a call to target extension.
        
        Args:
            dial_string: Full dial string (e.g., "PJSIP/2765")
            context_name: Asterisk context for origination
            timeout: Timeout in seconds
            ari_client: ARI client instance
        
        Returns:
            Channel dict if successful, None if failed/timeout
        """
        # Create a unique channel ID for tracking
        channel_id = f"transfer-{dial_string.replace('/', '-')}-{asyncio.get_event_loop().time()}"
        
        try:
            result = await ari_client.send_command(
                method="POST",
                resource="channels",
                data={
                    "endpoint": dial_string,
                    "app": ari_client.app_name,
                    "appArgs": "transfer",
                    "channelId": channel_id,
                    "timeout": timeout
                }
            )
            
            if result and result.get('status') == 200:
                channel_data = result.get('body', {})
                
                # Wait for channel to be answered (with timeout)
                answered = await self._wait_for_answer(channel_id, timeout, ari_client)
                
                if answered:
                    return channel_data
                else:
                    logger.warning(f"Target channel {channel_id} did not answer within {timeout}s")
                    # Hangup the channel
                    await ari_client.hangup_channel(channel_id)
                    return None
            else:
                logger.error(f"Failed to originate call: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Error originating call: {e}", exc_info=True)
            return None
    
    async def _wait_for_answer(
        self,
        channel_id: str,
        timeout: int,
        ari_client: Any
    ) -> bool:
        """
        Wait for channel to be answered.
        
        Args:
            channel_id: Channel ID to monitor
            timeout: Max wait time in seconds
            ari_client: ARI client instance
        
        Returns:
            True if answered, False if timeout/failed
        """
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                # Check channel state
                result = await ari_client.send_command(
                    method="GET",
                    resource=f"channels/{channel_id}"
                )
                
                if result and result.get('status') == 200:
                    channel_data = result.get('body', {})
                    state = channel_data.get('state')
                    
                    if state == 'Up':
                        logger.info(f"Channel {channel_id} answered")
                        return True
                    elif state in ['Down', 'Busy', 'Invalid']:
                        logger.info(f"Channel {channel_id} failed: {state}")
                        return False
                
                # Wait a bit before checking again
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error checking channel state: {e}")
                return False
        
        logger.warning(f"Timeout waiting for {channel_id} to answer")
        return False
