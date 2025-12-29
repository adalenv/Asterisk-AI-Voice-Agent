# Debugging Guide

For operator and developer debugging workflows, start with:

- Troubleshooting (operators + contributors): [`docs/TROUBLESHOOTING_GUIDE.md`](../TROUBLESHOOTING_GUIDE.md)
- Milestone context (Call History–first): [`milestone-21-call-history.md`](milestones/milestone-21-call-history.md)

## Useful Tools

- `agent doctor`
- `agent troubleshoot --last`

## Logs & Data

- Container logs: `docker compose logs ai-engine`
- Call history DB: `./data/call_history.db` (host)

