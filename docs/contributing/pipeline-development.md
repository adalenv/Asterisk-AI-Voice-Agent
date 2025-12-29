# Pipeline Development

Pipelines let you mix and match STT/LLM/TTS components and options in `config/ai-agent.yaml`.

## Start Here

- Pipeline architecture milestone: [`milestone-7-configurable-pipelines.md`](milestones/milestone-7-configurable-pipelines.md)
- Configuration reference: [`docs/Configuration-Reference.md`](../Configuration-Reference.md)

## Where Code Lives

- Pipeline orchestrator: [`src/pipelines/orchestrator.py`](../../src/pipelines/orchestrator.py)
- Pipeline adapters: [`src/pipelines/`](../../src/pipelines/)
- Example pipeline configs: [`examples/pipelines/`](../../examples/pipelines/)

## Testing

- Pipeline-related tests live under [`tests/`](../../tests/) (search for `test_pipeline_`).

