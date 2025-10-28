# Asterisk AI Voice Agent v3.0

An open-source AI Voice Agent that integrates with Asterisk/FreePBX using the Asterisk REST Interface (ARI). It features a **production-ready, two-container architecture** with **Hybrid ARI** call control, **SessionStore** state management, ExternalMedia RTP integration for reliable real-time audio capture and file-based TTS playback for robust conversation handling.

## 🌟 Why Asterisk AI Voice Agent?

This project is designed to be the most powerful, flexible, and easy-to-use open-source AI voice agent for Asterisk. Here’s what makes it different:

* **Asterisk-Native:** No external telephony providers required. It works directly with your existing Asterisk/FreePBX installation.
* **Truly Open Source:** The entire project is open source (MIT licensed), so you have complete transparency and control.
* **Hybrid AI:** Seamlessly switch between cloud and local AI providers, giving you the best of both worlds.
* **Production-Ready:** This isn’t just a demo. It’s a battle-tested, production-ready solution.
* **Cost-Effective:** With local AI, you can have predictable costs without per-minute charges.

## ✨ Features

* **Modular AI Providers**: Easily switch between cloud and local AI providers.
  * ✅ **Deepgram Voice Agent**: Fully implemented for a powerful cloud-based solution.
  * ✅ **Local AI Server**: A dedicated container that runs local models (Vosk for STT, Llama for LLM, and Piper for TTS) for full control and privacy.
* **High-Performance Architecture**: A lean `ai-engine` for call control and a separate `local-ai-server` for heavy AI processing ensures stability and scalability.
* **Hybrid ARI Architecture**: Call control using ARI with "answer caller → create mixing bridge → add caller → create ExternalMedia and add it to bridge" flow.
* **SessionStore State Management**: Centralized, typed store for all call session state, replacing legacy dictionary-based state management.
* **Real-time Communication**: ExternalMedia RTP upstream capture from Asterisk with ARI-commanded file-based playback; engine↔AI servers use WebSocket.
* **Docker-based Deployment**: Simple, two-service orchestration using Docker Compose.
* **Customizable**: Configure greetings, AI roles, and voice personalities in a simple YAML file.

## 🚀 Quick Start

### Prerequisites

* **Asterisk 18+** or **FreePBX 15+** with ARI enabled.
* **Docker** and **Docker Compose** installed.
* **Git** for cloning the repository.

### Installation

1. **Clone the repository**:

    ```bash
    git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
    cd Asterisk-AI-Voice-Agent
    ```

2. **Configure your environment**:
    Copy the example config file `config/ai-agent.example.yaml` to `config/ai-agent.yaml` and the `.env.example` to `.env`. Edit them to match your setup, including Asterisk connection details and API keys.

3. **Download local models (optional but recommended for the local provider)**:

    ```bash
    make model-setup
    ```

    The helper invokes `scripts/model_setup.py`, detects your hardware tier, downloads the STT/LLM/TTS bundles listed in `models/registry.json`, and skips work when everything is already cached. Light systems take ~60–90 seconds per response; heavy systems can respond in ~20 seconds.

4. **Start the services**:

    ```bash
    docker-compose up --build -d
    ```

    This will start both the `ai-engine` and the `local-ai-server`. If you only want to use a cloud provider like Deepgram, you can start just the engine: `docker-compose up -d ai-engine`.

## ⚙️ Configuration

The system is configured via `config/ai-agent.yaml` and a `.env` file for secrets.

### Key `ai-agent.yaml` settings

* `default_provider`: `deepgram` or `local`
* `asterisk`: Connection details for ARI.
* `providers`: Specific configurations for each AI provider.

### Required `.env` variables

* `ASTERISK_ARI_USERNAME` & `ASTERISK_ARI_PASSWORD`
* `DEEPGRAM_API_KEY` (if using Deepgram)

### Optional local AI tuning (set via environment variables)

* `LOCAL_LLM_MODEL_PATH`: absolute path to an alternative GGUF file mounted into the container.
* `LOCAL_LLM_MAX_TOKENS`: cap the number of response tokens (default `48` for faster replies).
* `LOCAL_LLM_TEMPERATURE`, `LOCAL_LLM_TOP_P`, `LOCAL_LLM_REPEAT_PENALTY`: sampling controls for the TinyLlama runtime.
* `LOCAL_LLM_THREADS`, `LOCAL_LLM_CONTEXT`, `LOCAL_LLM_BATCH`: advanced performance knobs; defaults auto-detect CPU cores and favour latency.
* `LOCAL_STT_MODEL_PATH`, `LOCAL_TTS_MODEL_PATH`: override default Vosk/Piper models if you preload alternates under `models/`.

## 🏗️ Project Architecture

The application is split into two Docker containers for performance and scalability:

1. **`ai-engine`**: A lightweight service that connects to Asterisk via ARI, manages the call lifecycle, and communicates with AI providers.
2. **`local-ai-server`**: A dedicated, powerful service that pre-loads and runs local STT, LLM, and TTS models, exposing them via a WebSocket interface.

```
┌─────────────────┐      ┌───────────┐      ┌───────────────────┐
│ Asterisk Server │◀────▶│ ai-engine │◀────▶│ AI Provider       │
│ (ARI, RTP)      │      │ (Docker)  │      │ (Deepgram, etc.)  │
└─────────────────┘      └───────────┘      └───────────────────┘
                           │     ▲
                           │ WSS │ WebSocket
                           ▼     │
                         ┌─────────────────┐
                         │ local-ai-server │
                         │ (Docker)        │
                         └─────────────────┘
```

This separation ensures that the resource-intensive AI models do not impact the real-time call handling performance of the `ai-engine`. The system uses ExternalMedia RTP for reliable audio capture and file-based TTS playback for robust conversation handling. Streaming TTS is planned as a future enhancement.

## 🎯 Current Status

* ✅ **PRODUCTION READY**: Full two-way conversation system working perfectly!
* ✅ **Real-time Audio Processing**: ExternalMedia RTP with SSRC mapping
* ✅ **State Management**: SessionStore-based centralized state management
* ✅ **TTS Gating**: Perfect feedback prevention during AI responses
* ✅ **Local AI Integration**: Vosk STT, TinyLlama LLM, Piper TTS
* ✅ **Conversation Flow**: Complete STT → LLM → TTS pipeline working
* ✅ **Architecture Validation**: Refactored codebase with clean separation of concerns
* ✅ **Observability**: ConversationCoordinator drives `/health` + `/metrics` (Prometheus friendly)

## 🗺️ Roadmap

Current milestones and acceptance criteria live in [`docs/plan/ROADMAP.md`](docs/plan/ROADMAP.md). Update that file after each deliverable so anyone (or any AI assistant) can resume the project with a single reference.

## 🤝 Contributing

Contributions are welcome! Please see our [Contributing Guide](../../CONTRIBUTING.md) for more details on how to get involved.

## 💬 Community

Have questions or want to chat with other users? Join our community:

* [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues)
* Community Forum (coming soon)

## 📝 License

This project is licensed under the MIT License. See the [LICENSE](../../LICENSE) file for details.

## 🙏 Show Your Support

If you find this project useful, please give it a ⭐️ on [GitHub](https://github.com/hkjarral/Asterisk-AI-Voice-Agent)! It helps us gain visibility and encourages more people to contribute.
