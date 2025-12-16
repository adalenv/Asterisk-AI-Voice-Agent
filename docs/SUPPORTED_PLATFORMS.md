# Supported Platforms Matrix

This project is designed to run on Linux hosts that either:

- Run Asterisk/FreePBX on the same machine, or
- Can reliably reach an Asterisk/FreePBX host over the network.

We do **not** target macOS/Windows as production hosts for Asterisk. Those are dev-only environments.

## Support Tiers

- **Tier 1 (CI-tested):** We expect this to work out-of-the-box and keep it green in CI.
- **Tier 2 (Community-verified):** Known to work based on community reports + troubleshooting artifacts.
- **Tier 3 (Best-effort):** Likely works if Docker/Compose requirements are met, but not verified.

## Current Verification Status

| Platform | Tier | Status | Notes |
|----------|------|--------|------|
| **PBX Distro** `12.7.8-2306-1.sng7` | Tier 2 | ✅ Verified (project dev server) | Only fully verified end-to-end environment to date |
| Ubuntu 22.04 | Tier 1 | ⏳ Pending | Add CI + community verification |
| Ubuntu 24.04 | Tier 1 | ⏳ Pending | Add CI + community verification |
| Debian 11/12 | Tier 2 | ⏳ Pending | Community verification requested |
| Rocky/Alma 9 | Tier 2 | ⏳ Pending | Community verification requested |
| Fedora (latest) | Tier 3 | ⚠ Best-effort | Rootless Docker common; we warn rather than “guarantee” |

## Baseline Requirements (All Tiers)

- Docker + Docker Compose v2
- x86_64 host
- Asterisk ARI reachable and credentials configured in `.env`

## Evidence Required for Tier 2 (Community-verified)

When reporting “works on X”, include:

- `./preflight.sh` output (and `./preflight.sh --apply-fixes` if used)
- `agent doctor --json` output
- One confirmed baseline call flow:
  - Provider: Deepgram/OpenAI Realtime/Google Live/ElevenLabs/local
  - Transport: AudioSocket or ExternalMedia RTP

If a report includes these artifacts, we can promote the platform in this matrix.
