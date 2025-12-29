# Tool Development

This guide covers how to implement and wire new tools (telephony/business) that the agent can call at runtime.

## Start Here

- User-facing overview: [`docs/TOOL_CALLING_GUIDE.md`](../TOOL_CALLING_GUIDE.md)
- Implementation milestone (historical context): [`milestone-16-tool-calling-system.md`](milestones/milestone-16-tool-calling-system.md)

## Where Code Lives

- Tool registry + contracts: [`src/tools/`](../../src/tools/)
- Provider-specific tool adapters: [`src/tools/adapters/`](../../src/tools/adapters/)
- Telephony tools (transfer, hangup, voicemail, ...): [`src/tools/telephony/`](../../src/tools/telephony/)
- Business tools (email summary, transcripts, ...): [`src/tools/business/`](../../src/tools/business/)

## Testing

- Unit/integration tests live in [`tests/`](../../tests/)
- Test overview: [`tests/README.md`](../../tests/README.md)

