"""
Tool registry - central repository for all available tools.

Singleton pattern ensures only one registry exists across the application.
"""

from typing import Dict, List, Type, Optional, Iterable, Set, Union, Any
from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolPhase, PreCallTool, PostCallTool
import logging

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Singleton registry for all available tools.
    
    Manages tool registration, lookup, and schema generation for different providers.
    """
    
    _instance = None
    
    # Tool name aliases for provider compatibility
    # Different providers use different naming conventions for the same tools
    TOOL_ALIASES = {
        "transfer_call": "transfer",      # ElevenLabs, some OpenAI prompts
        "hangup": "hangup_call",          # Alternative naming
        "end_call": "hangup_call",        # Alternative naming
        "transfer_to_queue": "transfer",  # Legacy queue transfer
    }
    
    def __new__(cls):
        """Singleton pattern - only one instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: Dict[str, Tool] = {}
            cls._instance._initialized = False
        return cls._instance
    
    def register(self, tool_class: Type[Tool]) -> None:
        """
        Register a tool class.
        
        Args:
            tool_class: Tool class (not instance) to register
        
        Example:
            registry.register(TransferCallTool)
        """
        tool = tool_class()
        tool_name = tool.definition.name
        
        if tool_name in self._tools:
            logger.warning(f"Tool {tool_name} already registered, overwriting")
        
        self._tools[tool_name] = tool
        logger.info(f"✅ Registered tool: {tool_name} ({tool.definition.category.value})")

    def register_instance(self, tool: Tool) -> None:
        """
        Register a tool instance (used for dynamically constructed tools like MCP wrappers).
        """
        tool_name = tool.definition.name
        if tool_name in self._tools:
            logger.warning(f"Tool {tool_name} already registered, overwriting")
        self._tools[tool_name] = tool
        logger.info(f"✅ Registered tool: {tool_name} ({tool.definition.category.value})")

    def get(self, name: str) -> Optional[Tool]:
        """
        Get tool by name, with alias support.
        
        Args:
            name: Tool name (e.g., "transfer_call" or "transfer")
        
        Returns:
            Tool instance or None if not found
        """
        # Try direct lookup first
        tool = self._tools.get(name)
        if tool:
            return tool
        
        # Try alias lookup
        canonical_name = self.TOOL_ALIASES.get(name)
        if canonical_name:
            return self._tools.get(canonical_name)
        
        return None

    def has(self, name: str) -> bool:
        """Return True if a tool is registered under this exact name (no alias resolution)."""
        return name in self._tools

    def unregister(self, name: str) -> bool:
        """Unregister a tool by exact name (no alias resolution)."""
        if name in self._tools:
            self._tools.pop(name, None)
            logger.info(f"🗑️ Unregistered tool: {name}")
            return True
        return False

    def unregister_many(self, names: Iterable[str]) -> int:
        removed = 0
        for name in names:
            if self.unregister(str(name)):
                removed += 1
        return removed
    
    def get_all(self) -> List[Tool]:
        """
        Get all registered tools.
        
        Returns:
            List of all tool instances
        """
        return list(self._tools.values())
    
    def get_by_category(self, category: ToolCategory) -> List[Tool]:
        """
        Get tools by category.
        
        Args:
            category: ToolCategory enum value
        
        Returns:
            List of tools in that category
        """
        return [
            tool for tool in self._tools.values()
            if tool.definition.category == category
        ]
    
    def get_by_phase(self, phase: ToolPhase) -> List[Tool]:
        """
        Get tools by execution phase.
        
        Args:
            phase: ToolPhase enum value (PRE_CALL, IN_CALL, POST_CALL)
        
        Returns:
            List of tools in that phase
        """
        return [
            tool for tool in self._tools.values()
            if tool.definition.phase == phase
        ]
    
    def get_global_tools(self, phase: Optional[ToolPhase] = None) -> List[Tool]:
        """
        Get tools marked as global (is_global=True).
        
        Args:
            phase: Optional phase filter. If None, returns all global tools.
        
        Returns:
            List of global tools, optionally filtered by phase
        """
        tools = [
            tool for tool in self._tools.values()
            if tool.definition.is_global
        ]
        if phase is not None:
            tools = [t for t in tools if t.definition.phase == phase]
        return tools
    
    def get_pre_call_tools(self, include_global: bool = True) -> List[Tool]:
        """
        Get all pre-call tools.
        
        Args:
            include_global: If True, includes global pre-call tools
        
        Returns:
            List of pre-call tools
        """
        tools = self.get_by_phase(ToolPhase.PRE_CALL)
        if not include_global:
            tools = [t for t in tools if not t.definition.is_global]
        return tools
    
    def get_post_call_tools(self, include_global: bool = True) -> List[Tool]:
        """
        Get all post-call tools.
        
        Args:
            include_global: If True, includes global post-call tools
        
        Returns:
            List of post-call tools
        """
        tools = self.get_by_phase(ToolPhase.POST_CALL)
        if not include_global:
            tools = [t for t in tools if not t.definition.is_global]
        return tools
    
    def get_in_call_tools(self, include_global: bool = True) -> List[Tool]:
        """
        Get all in-call tools (existing behavior).
        
        Args:
            include_global: If True, includes global in-call tools
        
        Returns:
            List of in-call tools
        """
        tools = self.get_by_phase(ToolPhase.IN_CALL)
        if not include_global:
            tools = [t for t in tools if not t.definition.is_global]
        return tools
    
    def get_tools_for_context(
        self,
        phase: ToolPhase,
        context_tool_names: Optional[List[str]] = None,
        disabled_global_tools: Optional[List[str]] = None
    ) -> List[Tool]:
        """
        Get effective tools for a context and phase.
        
        Combines global tools with context-specific tools, respecting opt-outs.
        
        Args:
            phase: The execution phase (PRE_CALL, IN_CALL, POST_CALL)
            context_tool_names: Tool names explicitly enabled for this context
            disabled_global_tools: Global tool names to exclude for this context
        
        Returns:
            List of tools to execute for this context and phase
        """
        disabled = set(disabled_global_tools or [])
        
        # Start with global tools for this phase (minus opt-outs)
        result_tools: Dict[str, Tool] = {}
        for tool in self.get_global_tools(phase):
            if tool.definition.name not in disabled:
                result_tools[tool.definition.name] = tool
        
        # Add context-specific tools
        if context_tool_names:
            for name in context_tool_names:
                tool = self.get(name)
                if tool and tool.definition.phase == phase:
                    result_tools[tool.definition.name] = tool
        
        return list(result_tools.values())
    
    def get_definitions(self) -> List[ToolDefinition]:
        """
        Get all tool definitions.
        
        Returns:
            List of ToolDefinition objects
        """
        return [tool.definition for tool in self._tools.values()]

    def _iter_tools_filtered(self, tool_names: Optional[List[str]]) -> Iterable[Tool]:
        if tool_names is None:
            return self._tools.values()
        seen: Set[str] = set()
        tools: List[Tool] = []
        for name in tool_names:
            tool = self.get(name)
            if not tool:
                continue
            tname = tool.definition.name
            if tname in seen:
                continue
            seen.add(tname)
            tools.append(tool)
        return tools

    def to_deepgram_schema(self) -> List[Dict]:
        """
        Export all tools in Deepgram Voice Agent format.
        
        Returns:
            List of tool schemas for Deepgram
        """
        return [tool.definition.to_deepgram_schema() for tool in self._tools.values()]

    def to_deepgram_schema_filtered(self, tool_names: Optional[List[str]]) -> List[Dict]:
        return [tool.definition.to_deepgram_schema() for tool in self._iter_tools_filtered(tool_names)]
    
    def to_openai_schema(self) -> List[Dict]:
        """
        Export all tools in OpenAI Chat Completions API format.
        
        Returns:
            List of tool schemas for OpenAI Chat Completions (nested format)
        """
        return [tool.definition.to_openai_schema() for tool in self._tools.values()]

    def to_openai_schema_filtered(self, tool_names: Optional[List[str]]) -> List[Dict]:
        return [tool.definition.to_openai_schema() for tool in self._iter_tools_filtered(tool_names)]
    
    def to_openai_realtime_schema(self) -> List[Dict]:
        """
        Export all tools in OpenAI Realtime API format.
        
        Returns:
            List of tool schemas for OpenAI Realtime API (flat format)
        """
        return [tool.definition.to_openai_realtime_schema() for tool in self._tools.values()]

    def to_openai_realtime_schema_filtered(self, tool_names: Optional[List[str]]) -> List[Dict]:
        return [tool.definition.to_openai_realtime_schema() for tool in self._iter_tools_filtered(tool_names)]
    
    def to_elevenlabs_schema(self) -> List[Dict]:
        """
        Export all tools in ElevenLabs Conversational AI format.
        
        Returns:
            List of tool schemas for ElevenLabs (client-side execution)
        """
        return [tool.definition.to_elevenlabs_schema() for tool in self._tools.values()]

    def to_elevenlabs_schema_filtered(self, tool_names: Optional[List[str]]) -> List[Dict]:
        return [tool.definition.to_elevenlabs_schema() for tool in self._iter_tools_filtered(tool_names)]
    
    def to_prompt_text(self) -> str:
        """
        Export all tools as text for custom pipeline system prompts.
        
        Returns:
            Formatted text description of all tools
        """
        if not self._tools:
            return ""
        
        lines = ["Available tools:\n"]
        for tool in self._tools.values():
            lines.append(tool.definition.to_prompt_text())
            lines.append("")  # Blank line between tools
        
        return "\n".join(lines)
    
    def to_local_llm_schema(self) -> List[Dict]:
        """
        Export all tools in local LLM JSON schema format.
        
        Returns:
            List of tool schemas for local LLM prompt injection
        """
        return [
            tool.definition.to_local_llm_schema()
            for tool in self._tools.values()
        ]
    
    def to_local_llm_prompt(self) -> str:
        """
        Generate a complete tool prompt section for local LLMs.
        
        Returns a formatted string that can be injected into system prompts
        for local LLMs like Phi-3, Llama, etc.
        """
        import json
        if not self._tools:
            return ""
        
        tools_json = json.dumps(self.to_local_llm_schema(), indent=2)
        
        return f"""## Available Tools

You have access to the following tools. When you need to use a tool, output EXACTLY this format:

<tool_call>
{{"name": "tool_name", "arguments": {{"param": "value"}}}}
</tool_call>

After outputting a tool call, provide a brief spoken response.

### Tool Definitions:
{tools_json}

### Important Rules:
- When the user says goodbye, farewell, or wants to end the call, use hangup_call tool. Set farewell_message to the exact goodbye sentence you intend to say, then speak that exact sentence as your final response.
- When the user asks to email the transcript, use request_transcript tool
- When the user wants to transfer, use transfer tool
- Always provide a spoken response along with tool calls
- Only use tools when the user's intent clearly matches the tool's purpose
"""
    
    def initialize_default_tools(self) -> None:
        """
        Register all built-in tools.
        
        Called once during engine startup to register all available tools.
        """
        if self._initialized:
            logger.info("Tools already initialized, skipping")
            return
        
        logger.info("Initializing default tools...")
        
        # Import and register telephony tools
        try:
            from src.tools.telephony.unified_transfer import UnifiedTransferTool
            self.register(UnifiedTransferTool)
        except ImportError as e:
            logger.warning(f"Could not import UnifiedTransferTool: {e}")

        try:
            from src.tools.telephony.attended_transfer import AttendedTransferTool
            self.register(AttendedTransferTool)
        except ImportError as e:
            logger.warning(f"Could not import AttendedTransferTool: {e}")
        
        try:
            from src.tools.telephony.cancel_transfer import CancelTransferTool
            self.register(CancelTransferTool)
        except ImportError as e:
            logger.warning(f"Could not import CancelTransferTool: {e}")
        
        try:
            from src.tools.telephony.hangup import HangupCallTool
            self.register(HangupCallTool)
        except ImportError as e:
            logger.warning(f"Could not import HangupCallTool: {e}")
        
        try:
            from src.tools.telephony.voicemail import VoicemailTool
            self.register(VoicemailTool)
        except ImportError as e:
            logger.warning(f"Could not import VoicemailTool: {e}")

        try:
            from src.tools.telephony.check_extension_status import CheckExtensionStatusTool
            self.register(CheckExtensionStatusTool)
        except ImportError as e:
            logger.warning(f"Could not import CheckExtensionStatusTool: {e}")
        
        # Business tools
        try:
            from src.tools.business.email_summary import SendEmailSummaryTool
            self.register(SendEmailSummaryTool)
        except ImportError as e:
            logger.warning(f"Could not import SendEmailSummaryTool: {e}")
        
        try:
            from src.tools.business.request_transcript import RequestTranscriptTool
            self.register(RequestTranscriptTool)
        except ImportError as e:
            logger.warning(f"Could not import RequestTranscriptTool: {e}")
        
        # Future tools will be registered here:
        # from src.tools.telephony.voicemail import SendToVoicemailTool
        # self.register(SendToVoicemailTool)
        
        self._initialized = True
        logger.info(f"🛠️  Initialized {len(self._tools)} tools")
    
    def initialize_http_tools_from_config(self, tools_config: Dict[str, Any]) -> None:
        """
        Initialize HTTP lookup and webhook tools from YAML config.
        
        Scans the tools config for entries with 'kind: generic_http_lookup'
        or 'kind: generic_webhook' and registers them.
        
        Args:
            tools_config: The 'tools' section from ai-agent.yaml
        """
        if not tools_config:
            return
        
        http_tool_count = 0
        
        for tool_name, tool_config in tools_config.items():
            if not isinstance(tool_config, dict):
                continue
            
            kind = tool_config.get('kind', '')
            
            if kind == 'generic_http_lookup':
                try:
                    from src.tools.http.generic_lookup import create_http_lookup_tool
                    tool = create_http_lookup_tool(tool_name, tool_config)
                    self.register_instance(tool)
                    http_tool_count += 1
                    logger.info(f"✅ Registered HTTP lookup tool: {tool_name}")
                except Exception as e:
                    logger.warning(f"Failed to create HTTP lookup tool {tool_name}: {e}")
            
            elif kind == 'generic_webhook':
                try:
                    from src.tools.http.generic_webhook import create_webhook_tool
                    tool = create_webhook_tool(tool_name, tool_config)
                    self.register_instance(tool)
                    http_tool_count += 1
                    logger.info(f"✅ Registered webhook tool: {tool_name}")
                except Exception as e:
                    logger.warning(f"Failed to create webhook tool {tool_name}: {e}")
        
        if http_tool_count > 0:
            logger.info(f"🌐 Initialized {http_tool_count} HTTP tools from config")

    def initialize_in_call_http_tools_from_config(self, in_call_tools_config: Dict[str, Any]) -> None:
        """
        Initialize in-call HTTP tools from YAML config.
        
        Scans the in_call_tools config for entries with 'kind: in_call_http_lookup'
        and registers them as AI-invokable tools.
        
        Args:
            in_call_tools_config: The 'in_call_tools' section from ai-agent.yaml or context config
        """
        if not in_call_tools_config:
            return
        
        in_call_tool_count = 0
        
        for tool_name, tool_config in in_call_tools_config.items():
            if not isinstance(tool_config, dict):
                continue
            
            kind = tool_config.get('kind', 'in_call_http_lookup')
            
            if kind == 'in_call_http_lookup':
                try:
                    from src.tools.http.in_call_lookup import create_in_call_http_tool
                    tool = create_in_call_http_tool(tool_name, tool_config)
                    self.register_instance(tool)
                    in_call_tool_count += 1
                    logger.info(f"✅ Registered in-call HTTP tool: {tool_name}")
                except Exception as e:
                    logger.warning(f"Failed to create in-call HTTP tool {tool_name}: {e}")
        
        if in_call_tool_count > 0:
            logger.info(f"📞 Initialized {in_call_tool_count} in-call HTTP tools from config")
    
    def list_tools(self) -> List[str]:
        """
        Get list of all tool names.
        
        Returns:
            List of tool names
        """
        return list(self._tools.keys())
    
    def clear(self) -> None:
        """
        Clear all registered tools.
        
        Mainly for testing purposes.
        """
        self._tools.clear()
        self._initialized = False
        logger.info("Cleared all registered tools")


# Global singleton instance
tool_registry = ToolRegistry()
