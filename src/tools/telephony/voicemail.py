"""
Voicemail Tool - Route calls to voicemail.

This tool allows the AI to send callers to voicemail when requested.
"""

from typing import Dict, Any
import structlog

from ..base import Tool, ToolDefinition, ToolParameter, ToolCategory
from ..context import ToolExecutionContext

logger = structlog.get_logger(__name__)


class VoicemailTool(Tool):
    """
    Tool for sending callers to voicemail.
    
    Uses ARI continue() to transfer to FreePBX ext-local context
    with vmu{extension} pattern for voicemail.
    """
    
    @property
    def definition(self) -> ToolDefinition:
        """Return tool definition."""
        return ToolDefinition(
            name="leave_voicemail",
            description="Send the caller to voicemail so they can leave a message",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=15,
            parameters=[]  # No parameters - uses config
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute voicemail transfer.
        
        Args:
            parameters: Empty dict (no parameters)
            context: Tool execution context
        
        Returns:
            Dict with status and message
        """
        # Get voicemail config
        config = context.get_config_value("tools.leave_voicemail")
        if not config or not config.get("enabled"):
            logger.warning("Voicemail tool not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not available"
            }
        
        extension = config.get('extension')
        if not extension:
            logger.error("Voicemail extension not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not configured properly"
            }
        
        logger.info(
            "Voicemail transfer requested",
            call_id=context.call_id,
            extension=extension
        )
        
        # Set transfer_active flag BEFORE calling continue
        # This prevents cleanup from hanging up the caller channel
        await context.update_session(
            transfer_active=True,
            transfer_target=f"Voicemail {extension}"
        )
        
        # CRITICAL: Wait briefly to allow AI audio to clear the RTP channel
        # Without this delay, the channel leaves Stasis while AI is still streaming,
        # causing the voicemail greeting to be blocked until caller speaks
        import asyncio
        await asyncio.sleep(0.8)  # Wait 800ms for Deepgram to finish speaking
        
        try:
            # Transfer to FreePBX voicemail context using continue
            # Pattern: ext-local,vmu{extension},1
            asterisk_context = "ext-local"
            asterisk_extension = f"vmu{extension}"
            
            logger.info(
                "Voicemail transfer initiated",
                call_id=context.call_id,
                context=asterisk_context,
                extension=asterisk_extension
            )
            
            # Use continue to leave Stasis and enter dialplan
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{context.caller_channel_id}/continue",
                params={
                    "context": asterisk_context,
                    "extension": asterisk_extension,
                    "priority": 1
                }
            )
            
            logger.info(
                "Voicemail transfer executed",
                call_id=context.call_id,
                extension=extension
            )
            
            return {
                "status": "success",
                "message": f"Transferring you to voicemail for extension {extension}"
            }
            
        except Exception as e:
            logger.error(
                "Voicemail transfer failed",
                call_id=context.call_id,
                error=str(e),
                exc_info=True
            )
            
            # Clear transfer flag on failure
            await context.update_session(
                transfer_active=False,
                transfer_target=None
            )
            
            return {
                "status": "failed",
                "message": "Unable to transfer to voicemail at this time"
            }
