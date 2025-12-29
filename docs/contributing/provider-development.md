# Provider Development

This guide summarizes where and how to add new providers and integrations.

## Integration Surfaces

- **Full-agent providers** (monolithic STT+LLM+TTS “agents”): [`src/providers/`](../../src/providers/)
- **Pipeline adapters** (mix-and-match STT/LLM/TTS components): [`src/pipelines/`](../../src/pipelines/)

## References

- Architecture overview: [`architecture-quickstart.md`](architecture-quickstart.md)
- Deep dive: [`architecture-deep-dive.md`](architecture-deep-dive.md)
- Provider internals (existing implementations):
  - [`references/Provider-Google-Implementation.md`](references/Provider-Google-Implementation.md)
  - [`references/Provider-Deepgram-Implementation.md`](references/Provider-Deepgram-Implementation.md)
  - [`references/Provider-OpenAI-Implementation.md`](references/Provider-OpenAI-Implementation.md)

## Expectations For Contributions

- Add/update docs for setup where applicable (user-facing):
  - [`docs/Provider-Google-Setup.md`](../Provider-Google-Setup.md)
  - [`docs/Provider-Deepgram-Setup.md`](../Provider-Deepgram-Setup.md)
  - [`docs/Provider-OpenAI-Setup.md`](../Provider-OpenAI-Setup.md)
  - [`docs/Provider-ElevenLabs-Setup.md`](../Provider-ElevenLabs-Setup.md)
- Add tests under [`tests/`](../../tests/) when behavior changes.

