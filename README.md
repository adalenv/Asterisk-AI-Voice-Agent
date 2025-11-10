# Asterisk AI Voice Agent v4.1

![Version](https://img.shields.io/badge/version-4.1.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)
![Asterisk](https://img.shields.io/badge/asterisk-18+-orange.svg)

The most powerful, flexible open-source AI voice agent for Asterisk/FreePBX. Featuring a **modular pipeline architecture** that lets you mix and match STT, LLM, and TTS providers, plus **3 production-ready golden baselines** validated for enterprise deployment.

## 🎉 What's New in v4.1

* **🔧 Tool Calling**: AI agents can now transfer calls and send emails
* **🩺 Agent CLI**: Professional CLI tools (`agent doctor`, `agent troubleshoot`, `agent demo`, `agent init`)
* **📞 Warm Transfers**: Direct SIP origination with bidirectional audio
* **📧 Email Integration**: Send transcripts and call summaries via Resend API
* **🏗️ Unified Architecture**: Write tools once, use with any provider (Deepgram, OpenAI)
* **✅ Production Validated**: All features tested in production with real call traffic

## 🌟 Why Asterisk AI Voice Agent?

* **Asterisk-Native:** Works directly with your existing Asterisk/FreePBX - no external telephony providers required
* **Truly Open Source:** MIT licensed with complete transparency and control
* **Modular Architecture:** Choose cloud, local, or hybrid - mix providers as needed
* **Production-Ready:** Battle-tested with validated configurations and enterprise monitoring
* **Cost-Effective:** Local Hybrid costs ~$0.001-0.003/minute (LLM only)
* **Privacy-First:** Keep audio local while using cloud intelligence

## ✨ Features

### 3 Golden Baseline Configurations

1. **OpenAI Realtime** (Recommended for Quick Start)
   * Modern cloud AI with natural conversations
   * Response time: <2 seconds
   * Best for: Enterprise deployments, quick setup

2. **Deepgram Voice Agent** (Enterprise Cloud)
   * Advanced Think stage for complex reasoning
   * Response time: <3 seconds
   * Best for: Deepgram ecosystem, advanced features

3. **Local Hybrid** (Privacy-Focused)
   * Local STT/TTS + Cloud LLM (OpenAI)
   * Audio stays on-premises, only text to cloud
   * Response time: 3-7 seconds
   * Best for: Audio privacy, cost control, compliance

### Technical Features

* **Tool Calling System**: AI-powered actions (transfers, emails) work with any provider
* **Agent CLI Tools**: `doctor`, `troubleshoot`, `demo`, `init` commands for operations
* **Modular Pipeline System**: Independent STT, LLM, and TTS provider selection
* **Dual Transport Support**: AudioSocket (full agents) and ExternalMedia RTP (pipelines)
* **High-Performance Architecture**: Separate `ai-engine` and `local-ai-server` containers
* **Enterprise Monitoring**: Prometheus + Grafana with 5 dashboards and 50+ metrics
* **State Management**: SessionStore for centralized, typed call state
* **Barge-In Support**: Interrupt handling with configurable gating
* **Docker Deployment**: Simple two-service orchestration
* **Customizable**: YAML configuration for greetings, personas, and behavior

## 🎥 Demo

[![Watch the demo](https://img.youtube.com/vi/ZQVny8wfCeY/hqdefault.jpg)](https://youtu.be/ZQVny8wfCeY "Asterisk AI Voice Agent demo")

### 📞 Try it Live! (US Only)

Experience all three golden baseline configurations with a single phone call:

**Dial: (925) 736-6718**

- **Press 6** → Deepgram Voice Agent (Enterprise cloud with Think stage)
- **Press 7** → OpenAI Realtime API (Modern cloud AI, most natural)
- **Press 8** → Local Hybrid Pipeline (Privacy-focused, audio stays local)

Each configuration uses the same Ava persona with full project knowledge. Compare response times, conversation quality, and naturalness!

**NEW in v4.1**: Try asking the agent to "transfer me to support" or "email me a transcript"!

## 🛠️ AI-Powered Actions (v4.1+)

Your AI agent can now perform real-world actions:

### Call Transfers

```
Caller: "I need to speak with technical support"
Agent: "I'll transfer you to technical support right away."
[Warm transfer to extension 6001 with bidirectional audio]
```

### Email Transcripts

```
Caller: "Can you email me a transcript of this call?"
Agent: "I'd be happy to send you a transcript. What email address should I use?"
Caller: "john dot smith at gmail dot com"
Agent: "That's john.smith@gmail.com - is that correct?"
Caller: "Yes"
Agent: "Perfect! I'll send the transcript there shortly."
[Email sent with full conversation transcript]
```

### Automatic Summaries

After every call, admins automatically receive:
- Full conversation transcript
- Call duration and metadata
- Caller information
- Professional HTML formatting

**Setup**: See [Tool Calling Guide](docs/TOOL_CALLING_GUIDE.md) for configuration.

## 🩺 Agent CLI Tools (v4.1+)

Production-ready CLI for operations:

```bash
# System health check
agent doctor

# Analyze specific call
agent troubleshoot

# Demo features
agent demo

# Interactive setup
agent init
```

**Binary Installation** (one-line):

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

Supports Linux, macOS (Intel + Apple Silicon), and Windows. See [CLI Tools Guide](docs/CLI_TOOLS_GUIDE.md).

## 🚀 Quick Start

Get up and running in **5 minutes**:

### 1. Clone and Install

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./install.sh
```

The installer will:
* Guide you through **3 simple configuration choices**
* Prompt for required API keys (only what you need)
* Set up Docker containers automatically
* Configure Asterisk integration

### 2. Choose Your Configuration

When prompted, select one of the 3 golden baselines:

* **[1] OpenAI Realtime** - Fastest setup, modern AI (requires `OPENAI_API_KEY`)
* **[2] Deepgram Voice Agent** - Enterprise features (requires `DEEPGRAM_API_KEY` + `OPENAI_API_KEY`)
* **[3] Local Hybrid** - Privacy-focused (requires `OPENAI_API_KEY`, 8GB+ RAM)

The installer automatically starts the correct services for your choice.

### 3. Configure Asterisk Dialplan

Add this to your FreePBX (Config Edit → extensions_custom.conf):

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent v4.1)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**That's it!** Without any variables, the system uses `local_hybrid` by default.

Then create a Custom Destination pointing to `from-ai-agent,s,1` and route calls to it.

### 4. Test Your Agent

Make a call to your configured destination and have a conversation!

**Verify health** (optional):
```bash
curl http://127.0.0.1:15000/health
```

**View logs**:
```bash
docker compose logs -f ai-engine
```

**That's it!** Your AI voice agent is ready. 🎉

For detailed setup, see [docs/FreePBX-Integration-Guide.md](docs/FreePBX-Integration-Guide.md)

## ⚙️ Configuration

### Two-File Configuration

* **`config/ai-agent.yaml`** - Golden baseline configs (safe to commit)
* **`.env`** - Secrets and API keys (git-ignored)

The installer handles everything automatically. To customize:

**Change greeting or persona**:
Edit [`config/ai-agent.yaml`](config/ai-agent.yaml):
```yaml
llm:
  initial_greeting: "Your custom greeting"
  prompt: "Your custom AI persona"
```

**Add/change API keys**:
Edit `.env`:
```bash
OPENAI_API_KEY=sk-your-key-here
DEEPGRAM_API_KEY=your-key-here
ASTERISK_ARI_USERNAME=asterisk
ASTERISK_ARI_PASSWORD=your-password
```

**Switch configurations**:
```bash
# Copy a different golden baseline
cp config/ai-agent.golden-deepgram.yaml config/ai-agent.yaml
docker compose up -d --force-recreate ai-engine
```

### Optional: Enterprise Monitoring

If you enabled monitoring during installation, you have Prometheus + Grafana running:

**Access Grafana:**
```
http://your-server-ip:3000
Username: admin
Password: admin (change after first login)
```

**If you didn't enable monitoring during install**, you can start it anytime:
```bash
docker compose -f docker-compose.monitoring.yml up -d
```

**Stop monitoring:**
```bash
docker compose -f docker-compose.monitoring.yml down
```

**Note:** Monitoring is completely optional. The AI agent works without it. See [monitoring/README.md](monitoring/README.md) for dashboards, alerts, and metrics.

For advanced tuning, see:
* [docs/Configuration-Reference.md](docs/Configuration-Reference.md) - Complete reference
* [docs/Transport-Mode-Compatibility.md](docs/Transport-Mode-Compatibility.md) - Transport modes

## 🏗️ Project Architecture

Two-container architecture for performance and scalability:

**`ai-engine`** (Lightweight orchestrator)
* Connects to Asterisk via ARI
* Manages call lifecycle
* Routes audio to/from AI providers
* Handles state management

**`local-ai-server`** (Optional, for Local Hybrid)
* Runs local STT/TTS models
* Vosk (speech-to-text)
* Piper (text-to-speech)
* WebSocket interface

```
┌─────────────────┐      ┌───────────┐      ┌───────────────────┐
│ Asterisk Server │◀────▶│ ai-engine │◀────▶│ AI Provider       │
│ (ARI, RTP)      │      │ (Docker)  │      │ (Cloud or Local)  │
└─────────────────┘      └───────────┘      └───────────────────┘
                           │     ▲
                           │ WS  │ (Local Hybrid only)
                           ▼     │
                         ┌─────────────────┐
                         │ local-ai-server │
                         │ (Docker)        │
                         └─────────────────┘
```

**Key Design Principles**:
* Separation of concerns - AI processing isolated from call handling
* Modular pipelines - Mix and match STT, LLM, TTS providers
* Transport flexibility - AudioSocket (legacy) or ExternalMedia RTP (modern)
* Enterprise-ready - Monitoring, observability, production-hardened

## 📊 Requirements

### Minimum System Requirements

**For Cloud Configurations** (OpenAI Realtime, Deepgram):
* CPU: 2+ cores
* RAM: 4GB
* Disk: 1GB
* Network: Stable internet connection

**For Local Hybrid** (Local STT/TTS + Cloud LLM):
* CPU: 4+ cores (modern 2020+)
* RAM: 8GB+ recommended
* Disk: 2GB (models + workspace)
* Network: Stable internet for LLM API

### Software Requirements

* Docker + Docker Compose
* Asterisk 18+ with ARI enabled
* FreePBX (recommended) or vanilla Asterisk

### API Keys Required

| Configuration | Required Keys |
|--------------|---------------|
| OpenAI Realtime | `OPENAI_API_KEY` |
| Deepgram Voice Agent | `DEEPGRAM_API_KEY` + `OPENAI_API_KEY` |
| Local Hybrid | `OPENAI_API_KEY` |

## 🗺️ Documentation

### Getting Started
* **[FreePBX Integration Guide](docs/FreePBX-Integration-Guide.md)** - Complete setup with dialplan examples
* **[Installation Guide](docs/INSTALLATION.md)** - Detailed installation and deployment

### Configuration
* **[Configuration Reference](docs/Configuration-Reference.md)** - All YAML settings explained
* **[Transport Compatibility](docs/Transport-Mode-Compatibility.md)** - AudioSocket vs ExternalMedia RTP
* **[Tuning Recipes](docs/Tuning-Recipes.md)** - Performance optimization guide

### Operations
* **[Monitoring Guide](docs/MONITORING_GUIDE.md)** - Prometheus + Grafana dashboards *(coming soon)*
* **[Production Deployment](docs/PRODUCTION_DEPLOYMENT.md)** - Production best practices *(coming soon)*
* **[Hardware Requirements](docs/HARDWARE_REQUIREMENTS.md)** - System specs and sizing *(coming soon)*

### Development
* **[Architecture](docs/Architecture.md)** - System design and components
* **[Contributing](CONTRIBUTING.md)** - How to contribute
* **[Changelog](CHANGELOG.md)** - Release history and changes

## 🤝 Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for more details on how to get involved.

## 💬 Community

Have questions or want to chat with other users? Join our community:

* [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues)
* [Community Forum](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions)

## 📝 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## 🙏 Show Your Support

If you find this project useful, please give it a ⭐️ on [GitHub](https://github.com/hkjarral/Asterisk-AI-Voice-Agent)! It helps us gain visibility and encourages more people to contribute.
