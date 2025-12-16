import pytest


class _NoopTool:
    def __init__(self, definition):
        self._definition = definition

    @property
    def definition(self):
        return self._definition

    async def execute(self, parameters, context):
        return {"status": "success", "message": "ok"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_rejects_disallowed_tool():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.openai import OpenAIToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _NoopTool(ToolDefinition(name="allowed_tool", description="x", category=ToolCategory.BUSINESS))
    )

    adapter = OpenAIToolAdapter(tool_registry)

    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call_1",
            "name": "allowed_tool",
            "arguments": "{}",
        },
    }

    context = {
        "call_id": "c1",
        "session_store": object(),
        "ari_client": object(),
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["some_other_tool"],
    }

    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "error"

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deepgram_adapter_rejects_when_tools_disabled():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.deepgram import DeepgramToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _NoopTool(ToolDefinition(name="t1", description="x", category=ToolCategory.BUSINESS))
    )

    adapter = DeepgramToolAdapter(tool_registry)
    event = {
        "type": "FunctionCallRequest",
        "functions": [{"id": "call_1", "name": "t1", "arguments": "{}"}],
    }
    context = {
        "call_id": "c1",
        "session_store": object(),
        "ari_client": object(),
        "config": {"tools": {"enabled": False}},
    }
    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "error"

    tool_registry.clear()

