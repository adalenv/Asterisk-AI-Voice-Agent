# Agent CLI v5.0 — Simplified Operator Workflow

**Status**: Design / implementation plan

## Goals

- Reduce CLI surface area to **4 high-value commands**:
  - `agent setup` — setup/config + dialplan guidance
  - `agent check` — single shareable diagnostics report ("attach this to issues")
  - `agent rca` — call-focused RCA (post-call)
  - `agent update` — pull latest code + apply updates safely
- Make `agent check` a **single source of truth** for:
  - host + docker + compose
  - container status, mounts, network mode
  - ARI reachability **from inside `ai_engine` only**
  - transport/compatibility alignment (config vs runtime expectations)
  - persistence readiness (media + call history DB)
  - best-effort internet/DNS reachability
- Keep flags minimal and consistent.

## Non-goals

- No auto-fix in v5.0 (`--fix` removed until it truly fixes).
- No requirement to install extra tools inside `ai_engine` (e.g., `curl`).
- No attempt to read remote Asterisk dialplan files directly.

## Command Surface (v5.0)

### `agent version`

**Purpose**: show version/build info (useful in support requests and issue templates).

### 0) `agent update` (v5.1+)

**Purpose**: safe “pull latest + rebuild/restart + verify” for repo-based deployments.

### 1) `agent setup`

**Purpose**: interactive setup and validation entrypoint.

**Behavior**:
- Guides user through:
  - ARI host/port/scheme/auth
  - transport selection
  - provider selection + key presence checks
  - writes `.env` + `config/ai-agent.yaml`
  - prints the expected Stasis app name and minimal dialplan snippet
- Ends by running `agent check`.

**Flags**:
- `-v/--verbose`
- `--no-color`

### 2) `agent check`

**Purpose**: the standard support report.

**Behavior**:
- Prints a structured report to stdout (copy/paste friendly; redirect to a file if needed).
- **Runs all probes using `docker exec ai_engine ...`** (no `docker run`, no external containers).
- Internet reachability is **best-effort** (warn/skip only).

**Flags**:
- `-v/--verbose` (include raw probe details)
- `--json` (JSON-only output to stdout)
- `--no-color` (disable color; also auto-disabled when stdout is not a TTY)

**Report sections** (order is stable):
1. Header: CLI version/build, timestamp, host identifiers
2. Host OS: `/etc/os-release`, kernel, arch
3. Docker/Compose: versions and availability
4. Containers: `ai_engine` running/healthy, image info
5. Network mode: `NetworkMode` and port exposure expectations
6. Mounts: `/app/data`, `/mnt/asterisk_media` present + writable
7. Call history DB: SQLite temp write test under `/app/data` (canonical DB: `/app/data/call_history.db`)
8. Config effective summary: `audio_transport`, `active_pipeline`, `downstream_mode`, format/ports
9. Transport compatibility: validate against `docs/Transport-Mode-Compatibility.md`
10. Advertise host alignment:
    - `EXTERNAL_MEDIA_ADVERTISE_HOST`
    - `AUDIOSOCKET_ADVERTISE_HOST`
    - compare to network mode + Asterisk topology
11. ARI probe (container-side only):
    - GET `/ari/asterisk/info` (Asterisk version)
    - GET `/ari/applications` (verify expected `app_name`)
12. Dialplan guidance:
    - If Asterisk local: optionally read dialplan file and grep for `Stasis(<app_name>)`
    - If Asterisk remote: print the commands the user should run and paste output
13. Best-effort internet/DNS: in-container DNS resolve + TCP connect checks
14. Summary: PASS/WARN/FAIL counts + top remediations

### 3) `agent rca`

**Purpose**: post-call RCA.

**Behavior**:
- Defaults to the most recent call.
- Prints a shareable RCA summary to stdout (redirect to a file if needed).
- Emits top likely causes + exact remediations.

**Flags**:
- `-v/--verbose`
- `--no-color`
- `--json` (JSON-only output to stdout)
- `--call <id>` (optional)

---

## Proposed Addition (v5.1+): `agent update`

**Purpose**: make “pull latest + rebuild/restart + verify” a single safe command for operators.

**Guiding principles**:
- Default to **safe, non-destructive** Git behavior (no hard resets; no forced merges).
- Preserve local operator changes (especially `.env` and `config/ai-agent.yaml`) using **stash + restore** and/or **backups**.
- Keep Docker actions predictable: rebuild/recreate only what’s needed; verify with `agent check`.
- Keep the CLI itself current: optionally self-update the `agent` binary from the latest GitHub Release before pulling code.

### Summary of the update flow

1. Validate we are in an AVA git clone (repo sanity).
2. Detect dirty working tree:
   - If clean: proceed.
   - If dirty: stash changes (or abort if `--no-stash`).
3. Fast-forward the local branch to `origin/main`:
   - If fast-forward possible: update.
   - If not: stop and print remediation.
   - If already up to date: skip rebuild/restart; still run `agent check` (unless `--skip-check`).
4. Restore stashed changes (best-effort):
   - If conflicts: keep stash, stop, print instructions.
5. Rebuild/restart containers (scope depends on diff / flags).
6. Run `agent check` and print a concise summary (old SHA → new SHA, services restarted, any warnings).

### Git commands (preliminary review)

These are the core Git operations the CLI would orchestrate. Exact invocation is intentionally conservative.

**Repo sanity**
- `git rev-parse --is-inside-work-tree`
- `git remote get-url origin`

**Capture current state**
- `git rev-parse --abbrev-ref HEAD` (detect branch vs detached HEAD)
- `git rev-parse HEAD` (old SHA for diff + reporting)
- `git status --porcelain` (detect uncommitted changes)

**Preserve local changes (default)**
- `git stash save "agent update <timestamp>"` (tracked changes only; compatible with older Git)

**Update to main (safe fast-forward only)**
- `git fetch origin main`
- `git merge --ff-only origin/main`

Notes:
- Prefer `fetch + merge --ff-only` over `git pull` so we can clearly handle each failure mode.
- If the user is not on `main`, either:
  - abort with guidance, or
  - offer an explicit `--target-branch main` that checks out `main` (only if no conflicts / safe).

**Restore local changes**
- `git stash pop`

Conflict handling (must be explicit and safe):
- If `stash pop` introduces conflicts, stop and print:
  - `git status`
  - how to resolve, and
  - how to recover stash: `git stash list` / `git stash apply stash@{N}`.

**Backup strategy (recommended in addition to stash)**
- Before updating, copy operator-owned files to a timestamped backup directory, e.g.:
  - `.env`
  - `config/ai-agent.yaml`
  - `config/contexts/` (if used)
  - any local overrides files you standardize on

The CLI should perform these backups using Go file I/O (not shell `cp`) so it’s cross-platform.

### Docker/Compose commands (preliminary review)

Goal: apply the updated code by rebuilding and recreating the minimal set of services.

**Baseline (safe default)**
- `docker compose up -d --build ai_engine admin_ui`

**If `local_ai_server/` changed or rebuild is requested**
- `docker compose up -d --build local_ai_server`

**If compose files changed**
- `docker compose up -d --build --remove-orphans`

**Optional stronger recreate (if needed for tricky dependency changes)**
- `docker compose up -d --build --force-recreate ai_engine admin_ui`

**Post-update verification**
- `agent check`

### Preservation contract (based on the current dev server)

On typical deployments (including the current dev server), the following must be preserved across updates:

- **Secrets/config**:
  - `.env` (ignored by git) — must be backed up; never overwritten automatically.
  - `config/ai-agent.yaml` — commonly locally edited; stash/backup and restore safely.
- **Persistent state** (never delete/clean):
  - `data/` (e.g., `data/call_history.db`, plus any learned/adaptive files)
  - `asterisk_media/` (generated audio)
  - `models/` (downloaded models; large)
  - `logs/` (troubleshooting artifacts)

### Compatibility note (Git versions)

Some servers run older Git (e.g., 1.8.x). `agent update` should avoid relying solely on newer commands like `git stash push -m` and should prefer broadly compatible sequences (or implement fallbacks).

### “Auto rebuild” decision rules (recommended)

The CLI can pick rebuild scope by diffing `old_sha..new_sha` and looking for changes in:
- `src/`, `requirements.txt`, `Dockerfile*`, `docker-compose*.yml` → rebuild `ai_engine`
- `admin_ui/` → rebuild `admin_ui`
- `local_ai_server/` → rebuild `local_ai_server`
- `config/` changes → no rebuild required (but restart may be recommended depending on change type)

Implementation note: use `git diff --name-only <old>..<new>` to build the impacted service set.

### Suggested flags (keep minimal)

- `--ref main` (default: `main`)
- `--remote origin` (default: `origin`)
- `--no-stash` (abort if dirty instead of stashing)
- `--rebuild auto|none|all` (default: `auto`)
- `--services ai_engine,admin_ui,local_ai_server` (override auto selection)
- `--force-recreate` (opt-in)
- `--skip-check` (opt-in)

### Regression and safety risks

Primary risk is Git state management:
- Users with local modifications in tracked files (common: `.env`, config YAML) can hit merge/stash conflicts.
- Mitigation: fast-forward only + backups + explicit conflict messaging + never hard reset by default.

Docker risks are lower:
- Rebuild/recreate is already the documented remediation path; the CLI just makes it consistent and faster.

## Probes: `docker exec ai_engine` only

All in-container probes must work without installing additional packages.

### Required Python availability

The `ai_engine` image is Python-based (`python:3.11-slim-bookworm`) and includes:
- stdlib modules: `os`, `json`, `sqlite3`, `socket`, `ssl`, `urllib.request`
- repo requirements include: `PyYAML` and `websockets`
- `curl` is intentionally not present.

### Planned exec probes

- **Config parse**: `python -c` loads `/app/config/ai-agent.yaml` via `yaml.safe_load`.
- **Mount + perms**: create/delete temp file in `/mnt/asterisk_media/ai-generated` and `/app/data`.
- **SQLite write test**: create `/app/data/.call_history_sqlite_test.db` (matches `preflight.sh` pattern).
- **ARI probe** (no curl): use `urllib.request` + Basic Auth:
  - `/ari/asterisk/info`
  - `/ari/applications` (verify app name)
- **Best-effort DNS/TCP** (no external container): `socket.getaddrinfo` + `socket.create_connection`.

## Known recurring failures (from community reports)

- ARI unreachable from inside container (wrong `ASTERISK_HOST`, wrong topology assumptions).
- Media mount RO/permission mismatch (PlaybackManager fallback to `/tmp`).
- Local AI server absent when config expects it.
- NAT advertise host mis-set for ExternalMedia RTP.

`agent check` must directly detect and print remediation for these.

## Backwards Compatibility

- Keep old commands as aliases (hidden in help) until v5.1:
  - `agent doctor` → alias of `agent check`
  - `agent troubleshoot` → alias of `agent rca`
  - `agent init` / `agent quickstart` → alias of `agent setup`

## Documentation changes (v5.0)

- Update docs to reference `agent setup`, `agent check`, `agent rca` and call this CLI version **5.0**.

## Publishing (CLI release)

- Build artifacts via Makefile targets (CI or maintainer machine):
  - `make cli-build-all`
  - `make cli-checksums`
- Publish GitHub Release tagged `v5.0.0` (or `agent-cli-v5.0.0` if decoupled) including:
  - `agent-<os>-<arch>` binaries
  - `SHA256SUMS`
- Update installer guidance (`scripts/install-cli.sh`) to show `agent check/rca/setup`.
