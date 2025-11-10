# CLI Tools Usage Guide

Complete reference for the Asterisk AI Voice Agent command-line tools.

## Overview

The `agent` CLI provides a suite of tools for setup, diagnostics, and troubleshooting. These tools are implemented in Go and designed to simplify operations and reduce time-to-first-call.

**Version**: v4.1  
**Status**: Production Ready - Binary builds available

---

## Installation

### Option 1: One-Line Installer (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

**Features**:
- Auto-detects your platform (Linux/macOS/Windows, AMD64/ARM64)
- Downloads latest release from GitHub
- Verifies SHA256 checksum
- Installs to `/usr/local/bin`
- Works on: Linux (AMD64, ARM64), macOS (Intel, Apple Silicon), Windows (AMD64)

**Custom installation directory**:
```bash
INSTALL_DIR=~/bin curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

### Option 2: Manual Download

Download pre-built binaries from [GitHub Releases](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases):

**Linux AMD64**:
```bash
wget https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-linux-amd64
chmod +x agent-linux-amd64
sudo mv agent-linux-amd64 /usr/local/bin/agent
```

**macOS Apple Silicon (M1/M2/M3)**:
```bash
wget https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-darwin-arm64
chmod +x agent-darwin-arm64
sudo mv agent-darwin-arm64 /usr/local/bin/agent
```

**All platforms**:
- `agent-linux-amd64` - Linux x86_64
- `agent-linux-arm64` - Linux ARM64 (Raspberry Pi, AWS Graviton)
- `agent-darwin-amd64` - macOS Intel
- `agent-darwin-arm64` - macOS Apple Silicon
- `agent-windows-amd64.exe` - Windows x86_64

### Option 3: Build from Source

```bash
# Clone repository
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent

# Build using Makefile
make cli-build

# Binary will be at: bin/agent
./bin/agent version

# Optional: Install system-wide
sudo cp bin/agent /usr/local/bin/
```

### Verify Installation

```bash
agent version

# Expected output:
# agent version v4.1.0
# Built: 2025-11-10T12:34:56Z
# Go version: go1.21.0
```

---

## Command Reference

### `agent init` - Interactive Setup Wizard

Guides you through initial configuration with an interactive wizard.

**Usage:**
```bash
agent init [flags]
```

**Flags:**
- `--non-interactive` - Non-interactive mode (future)
- `--template <name>` - Use config template (future)
- `-v, --verbose` - Verbose output

**What It Configures:**

1. **Asterisk ARI Connection**
   - Host (default: 127.0.0.1)
   - Port (default: 8088)
   - Username
   - Password

2. **Audio Transport**
   - AudioSocket (TCP) - for full agents
   - ExternalMedia (RTP/UDP) - for hybrid pipelines

3. **AI Provider**
   - OpenAI Realtime
   - Deepgram Voice Agent
   - Local Hybrid (Vosk + OpenAI + Piper)
   - Custom pipeline

4. **API Keys**
   - Prompts only for selected provider
   - Validates key format
   - Stores in `.env` file

**Example Session:**

```
$ ./bin/agent init

╔══════════════════════════════════════════════════════════╗
║   Asterisk AI Voice Agent - Setup Wizard v4.0           ║
╚══════════════════════════════════════════════════════════╝

Step 1/4: Asterisk ARI Configuration
────────────────────────────────────────────────────────────
Asterisk Host [127.0.0.1]: 
ARI Port [8088]: 
ARI Username: AIAgent
ARI Password: ********

✓ Testing ARI connection... Success!

Step 2/4: Audio Transport Selection
────────────────────────────────────────────────────────────
Select audio transport:
  1) AudioSocket (Modern, for full agents) [RECOMMENDED]
  2) ExternalMedia RTP (Legacy, for hybrid pipelines)

Your choice [1]: 1

✓ AudioSocket selected

Step 3/4: AI Provider Selection
────────────────────────────────────────────────────────────
Select AI provider:
  1) OpenAI Realtime (0.5-1.5s response time)
  2) Deepgram Voice Agent (1-2s response time)
  3) Local Hybrid (3-7s, privacy-focused)

Your choice [1]: 1

Enter OpenAI API Key: sk-...
✓ API key validated

Step 4/4: Configuration Review
────────────────────────────────────────────────────────────
Configuration Summary:
  Asterisk:  127.0.0.1:8088 (user: AIAgent)
  Transport: AudioSocket
  Provider:  OpenAI Realtime
  Config:    /path/to/.env

Save configuration? [Y/n]: y

✓ Configuration saved to .env
✓ Docker services restarted

Next steps:
  1. Configure Asterisk dialplan (see docs/INSTALLATION.md)
  2. Run 'agent doctor' to validate setup
  3. Make a test call!

Setup complete! 🎉
```

**Files Created/Modified:**
- `.env` - Environment variables with credentials
- `config/ai-agent.yaml` - Provider configuration (if using custom template)

**Tips:**
- Run multiple times to reconfigure
- Previous settings are shown as defaults
- Validates connectivity before saving

---

### `agent doctor` - System Health Check

Comprehensive health check and diagnostics tool.

**Usage:**
```bash
agent doctor [flags]
```

**Flags:**
- `--fix` - Attempt to auto-fix issues (future)
- `--json` - Output as JSON
- `--format <type>` - Output format: text, json, markdown
- `-v, --verbose` - Show detailed check output

**Exit Codes:**
- `0` - All checks passed ✅
- `1` - Warnings detected (non-critical) ⚠️
- `2` - Failures detected (critical) ❌

**Checks Performed:**

| Check | Description | Critical? |
|-------|-------------|-----------|
| Docker Daemon | Docker service running | ✅ Yes |
| Containers | ai-engine and local-ai-server status | ✅ Yes |
| Asterisk ARI | HTTP connectivity to ARI | ✅ Yes |
| AudioSocket Port | Port 8090 available/listening | ⚠️ Warning |
| RTP Ports | Ports 18080-18099 available | ⚠️ Warning |
| Configuration | YAML syntax and required fields | ✅ Yes |
| API Keys | Environment variables present | ✅ Yes |
| Provider Connectivity | Can reach OpenAI/Deepgram APIs | ⚠️ Warning |
| Audio Pipeline | Pipeline validation status | ⚠️ Warning |
| Recent Calls | Call history last 24 hours | Info only |
| Disk Space | /mnt/asterisk_media available | ⚠️ Warning |

**Example Output:**

```
$ ./bin/agent doctor

╔══════════════════════════════════════════════════════════╗
║   Asterisk AI Voice Agent - Health Check v4.0           ║
╚══════════════════════════════════════════════════════════╝

[1/11] Docker Daemon...     ✅ Docker running (v26.1.4)
[2/11] Containers...        ✅ ai-engine running (healthy)
                            ✅ local-ai-server running
[3/11] Asterisk ARI...      ✅ Connected to 127.0.0.1:8088
                               Version: Asterisk 18.20.0
[4/11] AudioSocket Port...  ✅ Port 8090 listening
[5/11] RTP Ports...         ✅ Ports 18080-18099 available
[6/11] Configuration...     ✅ YAML valid
                               Provider: openai_realtime
                               Transport: audiosocket
[7/11] API Keys...          ✅ OPENAI_API_KEY present
[8/11] Provider Connectivity... ✅ OpenAI API reachable (134ms)
[9/11] Audio Pipeline...    ✅ Pipeline healthy
                               Last validated: 2 minutes ago
[10/11] Recent Calls...     ℹ️  12 calls in last 24 hours
                               Avg duration: 45s
[11/11] Disk Space...       ✅ 45.2 GB available

════════════════════════════════════════════════════════════
Summary: 10 passed, 0 warnings, 0 failures

✅ System is healthy - ready for calls!
════════════════════════════════════════════════════════════
```

**Example with Issues:**

```
$ ./bin/agent doctor

[1/11] Docker Daemon...     ✅ Docker running (v26.1.4)
[2/11] Containers...        ❌ ai-engine not running

   FIX: Start the container
        docker compose up -d ai-engine

[3/11] Asterisk ARI...      ❌ Connection refused (127.0.0.1:8088)

   FIX: Check Asterisk is running
        asterisk -rx "core show version"
        
   FIX: Verify ARI is enabled
        Check /etc/asterisk/ari.conf
        
[4/11] AudioSocket Port...  ⚠️  Port 8090 not listening
                            (Container not running)
...

════════════════════════════════════════════════════════════
Summary: 6 passed, 2 warnings, 2 failures

❌ Critical issues detected - fix before making calls
════════════════════════════════════════════════════════════

Exit code: 2
```

**JSON Output:**

```bash
agent doctor --json | jq .
```

```json
{
  "timestamp": "2025-10-29T17:00:00Z",
  "checks": [
    {
      "name": "docker_daemon",
      "status": "pass",
      "message": "Docker running (v26.1.4)",
      "critical": true
    },
    {
      "name": "asterisk_ari",
      "status": "fail",
      "message": "Connection refused",
      "critical": true,
      "remediation": "Check Asterisk is running: asterisk -rx 'core show version'"
    }
  ],
  "summary": {
    "total": 11,
    "passed": 6,
    "warnings": 2,
    "failures": 2
  },
  "exit_code": 2
}
```

**Use in Scripts:**

```bash
#!/bin/bash
# Pre-deployment health check

if ! agent doctor; then
    echo "Health check failed"
    agent doctor --json > health-report.json
    exit 1
fi

echo "Proceeding with deployment"
```

---

### `agent demo` - Audio Pipeline Validation

Tests audio pipeline without making real calls.

**Usage:**
```bash
agent demo [flags]
```

**Flags:**
- `--provider <name>` - Test specific provider
- `--duration <seconds>` - Test duration (default: 10)
- `-v, --verbose` - Show detailed test output

**What It Tests:**

1. **Audio Capture** - Verify VAD and audio processing
2. **Provider Connection** - Test STT/LLM/TTS integration
3. **Audio Quality** - Measure latency and quality metrics
4. **Playback** - Test downstream audio path

**Example Output:**

```
$ ./bin/agent demo

╔══════════════════════════════════════════════════════════╗
║   Asterisk AI Voice Agent - Audio Demo v4.0             ║
╚══════════════════════════════════════════════════════════╝

Provider: OpenAI Realtime
Duration: 10 seconds

[1/4] Audio Capture Test
────────────────────────────────────────────────────────────
✓ Generating test audio (1kHz tone, 10s)
✓ VAD detection working
✓ Audio frames captured: 500 (20ms each)
  Latency: 24ms average
  Quality: SNR 68.4 dB

[2/4] Provider Connection Test
────────────────────────────────────────────────────────────
✓ Connected to OpenAI Realtime API
✓ Session established
✓ Audio buffer accepted
  Response time: 847ms

[3/4] Audio Quality Test
────────────────────────────────────────────────────────────
✓ TTS synthesis successful
✓ Audio quality metrics:
  Sample rate: 24000 Hz (resampled to 8000 Hz)
  Format: PCM16
  Duration: 3.2s
  No clipping detected
  RMS level: -18.2 dB

[4/4] Playback Test
────────────────────────────────────────────────────────────
✓ Streaming playback successful
✓ Pacing: 20ms frames
  Underflows: 0
  Jitter: < 5ms
  
════════════════════════════════════════════════════════════
Demo Results: 4/4 tests passed

✅ Audio pipeline is working correctly!
════════════════════════════════════════════════════════════
```

**Use Cases:**
- Validate configuration changes
- Test after provider API key updates
- Benchmark performance
- Troubleshoot audio quality issues

---

### `agent troubleshoot` - Post-Call Analysis

Interactive post-call troubleshooting with AI-powered root cause analysis.

**Usage:**
```bash
agent troubleshoot [call_id] [flags]
```

**Flags:**
- `--interactive` - Interactive analysis mode (default)
- `--auto` - Automatic analysis mode
- `--output <file>` - Save report to file
- `-v, --verbose` - Show detailed logs

**Example Usage:**

```bash
# Analyze most recent call
agent troubleshoot

# Analyze specific call by ID
agent troubleshoot 1761449250.2163

# Generate report file
agent troubleshoot --output call-analysis.md
```

**Example Session:**

```
$ ./bin/agent troubleshoot

╔══════════════════════════════════════════════════════════╗
║   Post-Call Troubleshooting Assistant v4.0              ║
╚══════════════════════════════════════════════════════════╝

Found 3 recent calls:
  1) 1761449250.2163 - 45s (2 min ago) - OpenAI Realtime
  2) 1761448120.2162 - 67s (15 min ago) - Deepgram
  3) 1761447890.2161 - 12s (1 hour ago) - Local Hybrid

Select call to analyze [1]: 1

Loading call data...
✓ Call logs retrieved (1,234 lines)
✓ Metrics collected
✓ Audio files located

Call Summary:
────────────────────────────────────────────────────────────
Call ID:     1761449250.2163
Provider:    OpenAI Realtime
Duration:    45.9 seconds
Turns:       6 (3 user, 3 agent)
Transport:   AudioSocket
Status:      Completed normally

Metrics:
────────────────────────────────────────────────────────────
Audio Quality:  SNR 64.7 dB (Excellent)
Response Time:  847ms average
Underflows:     0
Gating Events:  1 (normal)
API Latency:    134ms average

Issue Detection:
────────────────────────────────────────────────────────────
✓ No issues detected - call was successful!

Would you like to:
  1) View detailed logs
  2) Analyze audio quality
  3) Check timing metrics
  4) Export full report
  5) Exit

Your choice [5]: 4

Generating comprehensive report...
✓ Report saved to: troubleshoot-1761449250.2163.md

Report includes:
  - Call timeline
  - Audio quality analysis
  - Provider interaction log
  - Configuration snapshot
  - Recommendations

Done! 🎉
```

**Example with Issues:**

```
$ ./bin/agent troubleshoot 1761448120.2162

Issue Detection:
────────────────────────────────────────────────────────────
⚠️  High latency detected (avg 2.4s, expected <1.5s)
❌ Audio underflows: 12 events
⚠️  Jitter buffer warnings: 8

Root Cause Analysis:
────────────────────────────────────────────────────────────
Primary Issue: Network latency to provider

Evidence:
  - API response times: 1.8s-3.2s (expected: 200-800ms)
  - Jitter: 45ms (threshold: 20ms)
  - Provider timeout warnings in logs

Recommended Fixes:
  1. Check network connectivity to api.deepgram.com
     
     Test: ping -c 10 api.deepgram.com
     
  2. Increase jitter buffer:
     
     streaming:
       jitter_buffer_ms: 150  # Increase from 100
       
  3. Consider switching to AudioSocket if using RTP
     (AudioSocket has lower latency overhead)

Would you like me to apply fix #2 automatically? [y/N]:
```

**Report Output:**

Generated reports include:
- Call timeline with events
- Audio quality metrics (SNR, RMS, clipping)
- Provider interaction log
- Configuration at call time
- Recommended fixes
- Related documentation links

---

### `agent version` - Version Information

Display CLI and system version information.

**Usage:**
```bash
agent version
```

**Example Output:**

```
$ ./bin/agent version

agent version 1.0.0-p2-dev (P2 milestone)
Built for Asterisk AI Voice Agent
https://github.com/hkjarral/asterisk-ai-voice-agent

System Information:
  OS: Linux
  Arch: amd64
  Go: 1.21.5
```

---

## Common Workflows

### Initial Setup

```bash
# 1. Build CLI tools
cd cli && go build -o ../bin/agent ./cmd/agent

# 2. Run setup wizard
../bin/agent init

# 3. Validate environment
../bin/agent doctor

# 4. Test audio pipeline
../bin/agent demo

# 5. Configure Asterisk dialplan (see docs/INSTALLATION.md)

# 6. Make test call!
```

### Daily Operations

```bash
# Morning health check
agent doctor

# After config changes
agent doctor && agent demo

# Troubleshoot issue
agent troubleshoot  # Select recent call interactively
```

### CI/CD Integration

```bash
#!/bin/bash
# deployment-check.sh

set -e

echo "Running pre-deployment checks..."

# Health check (exit code 2 = failure)
if ! agent doctor; then
    echo "❌ Health check failed"
    agent doctor --json > health-check-failure.json
    exit 1
fi

# Audio pipeline test
if ! agent demo; then
    echo "❌ Audio demo failed"
    exit 1
fi

echo "✅ All checks passed - ready to deploy"
```

---

## Troubleshooting

### CLI Won't Build

**Issue**: Go build fails

**Fix**:
```bash
# Update Go dependencies
cd cli
go mod download
go mod tidy

# Rebuild
go build -o ../bin/agent ./cmd/agent
```

### "Command not found"

**Issue**: Binary not in PATH

**Fix**:
```bash
# Use relative path
./bin/agent version

# Or install system-wide
sudo cp bin/agent /usr/local/bin/
```

### Permission Denied

**Issue**: Can't execute binary

**Fix**:
```bash
chmod +x bin/agent
./bin/agent version
```

---

## Tips & Best Practices

**1. Run `agent doctor` after changes**
- After updating `.env` or YAML
- After Docker restart
- Before making test calls

**2. Use `agent demo` for quick validation**
- Faster than making real calls
- Tests end-to-end pipeline
- No Asterisk required

**3. Keep troubleshoot reports**
- Use `--output` flag for records
- Include in support tickets
- Track issues over time

**4. Automate with exit codes**
- `agent doctor` returns 0/1/2
- Use in scripts and CI/CD
- Catch issues early

---

## Future Enhancements (v4.1+)

- [ ] `agent config validate` - Pre-flight config check
- [ ] `agent test` - Automated test call
- [ ] `agent logs` - Streaming log viewer
- [ ] `agent metrics` - Real-time metrics dashboard
- [ ] Shell completion (bash, zsh, fish)
- [ ] Windows builds
- [ ] Auto-update mechanism

---

## See Also

- **[CLI Source Code](../cli/)** - Go implementation
- **[INSTALLATION.md](INSTALLATION.md)** - System installation guide
- **[Architecture.md](Architecture.md)** - System architecture
- **[CHANGELOG.md](../CHANGELOG.md)** - Release notes

---

## Support

**Issues**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues  
**Discussions**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions
