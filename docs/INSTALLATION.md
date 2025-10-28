# Asterisk AI Voice Agent - Installation Guide

This guide provides detailed instructions for setting up the Asterisk AI Voice Agent on your server.

## 1. Prerequisites

Before you begin, ensure your system meets the following requirements:

- **Operating System**: A modern Linux distribution (e.g., Ubuntu 20.04+, CentOS 7+).
- **Asterisk**: Version 18 or newer. FreePBX 15+ is also supported.
- **ARI (Asterisk REST Interface)**: Enabled and configured on your Asterisk server.
- **Docker**: Latest stable version of Docker and Docker Compose.
- **Git**: Required to clone the project repository.
- **Network Access**: Your server must be able to make outbound connections to the internet for Docker image downloads and API access to AI providers.

### Prerequisite checks

- Verify required Asterisk modules are loaded:

  ```bash
  asterisk -rx "module show like res_ari_applications"
  asterisk -rx "module show like app_audiosocket"
  ```

  Expected example output:

  ```
  Module                         Description                               Use Count  Status   Support Level
  res_ari_applications.so        RESTful API module - Stasis application   0          Running  core
  1 modules loaded

  Module                         Description                               Use Count  Status   Support Level
  app_audiosocket.so             AudioSocket Application                    20         Running  extended
  1 modules loaded
  ```

  If Asterisk < 18, on FreePBX Distro run:

  ```bash
  asterisk-switch-version   # aka asterisk-version-switch
  ```

  and select Asterisk 18+.

- Quick install Docker
  - Ubuntu:

    ```bash
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker $USER && newgrp docker
    docker --version && docker compose version
    ```

  - CentOS/Rocky/Alma:

    ```bash
    sudo dnf -y install dnf-plugins-core
    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io
    sudo systemctl enable --now docker
    docker --version && docker compose version
    ```

## 2. Installation Steps

The installation is handled by an interactive script that will guide you through the process.

### Step 2.1: Clone the Repository

First, clone the project repository to a directory on your server.

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
```

### Step 2.2: Run the Installation Script

Execute the `install.sh` script. You will need to run it with `sudo` if your user does not have permissions to run Docker.

```bash
./install.sh
```

The script will perform the following actions:

1. **System Checks**: Verify that Docker is installed and running.
2. **Interactive Setup**: Launch a wizard to collect configuration details.

### Step 2.3: Interactive Setup Wizard

The wizard will prompt you for the following information.

#### AI Provider Selection

You will be asked to choose an AI provider.

- **[1] OpenAI Realtime (Default, GA)**: Out-of-the-box realtime voice path.
- **[2] Deepgram Voice Agent**: Cloud STT/TTS with strong latency/quality.
- **[3] Local Models**: Offline option (Vosk STT, TinyLlama/other LLM, Piper TTS).

#### Provider Configuration

Based on your selection, you will need to provide API keys.

- **Deepgram API Key**: Required if you select the Deepgram provider.
- **OpenAI API Key**: Required if you select any OpenAI-based pipeline.

#### Asterisk ARI Configuration

You will need to provide the connection details for your Asterisk server's ARI.

- **Asterisk Host**: The hostname or IP address of your Asterisk server.
- **ARI Username**: The username for an ARI user.
- **ARI Password**: The password for the ARI user.

### What You'll Need (at a glance)

- A Linux server with Docker + Docker Compose
- Asterisk 18+ or FreePBX 15+ with ARI enabled
- API keys for your chosen provider (optional): `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`

### Step 2.4: Configuration File Generation

After you complete the wizard, the script will create a `.env` file in the project root with all your settings. You can manually edit this file later if you need to make changes.

### Step 2.5: Start the Service

Once the configuration is complete, the script will prompt you to build and start the Docker container. You can also do this manually.

```bash
docker-compose up --build -d
```

> IMPORTANT: First startup time (local models)
>
> If you selected a Local or Hybrid workflow, the `local-ai-server` may take 15–20 minutes on first startup to load LLM/TTS models depending on your CPU, RAM, and disk speed. This is expected and readiness may show degraded until models have fully loaded. Monitor with:
>
> ```bash
> docker-compose logs -f local-ai-server
> ```
>
> Subsequent restarts are typically much faster due to OS page cache. If startup is too slow for your hardware, consider using MEDIUM or LIGHT tier models and update the `.env` model paths accordingly.

## 3. Verifying the Installation

After starting the service, you can check that it is running correctly.

### Check Docker Container Status

```bash
docker-compose ps
```

You should see the `ai-engine` and `local-ai-server` containers running.

### Check Container Logs

```bash
docker-compose logs -f ai-engine
```

Look for a message indicating a successful connection to the Asterisk ARI and that the AudioSocket listener is ready on port 8090.

### Make a Test Call (AudioSocket + Stasis)

Use an AudioSocket-first dialplan so Asterisk streams raw audio to the agent before handing control to the Stasis app.

**Example Dialplan (`extensions_custom.conf`):**

[from-ai-agent]
exten => s,1,NoOp(Handing call directly to AI engine (default provider))
  same => n,Set(AI_PROVIDER=local_only)
  same => n,Stasis(asterisk-ai-voice-agent)
  same => n,Hangup()

[from-ai-agent-custom]
exten => s,1,NoOp(Handing call to AI engine with Deepgram override)
 same => n,Set(AI_PROVIDER=hybrid_support)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-deepgram]
exten => s,1,NoOp(Handing call to AI engine with Deepgram override)
 same => n,Set(AI_PROVIDER=deepgram)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-openai]
exten => s,1,NoOp(Handing call to AI engine with Deepgram override)
 same => n,Set(AI_PROVIDER=openai_realtime)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

## 4. Troubleshooting

- **Media path not found (Playback fails on sound:ai-generated/...)**:
  - Ensure the media directory and symlink exist on the host:

    ```bash
    sudo mkdir -p /var/lib/asterisk/sounds
    # Use real asterisk UID/GID if present; FreePBX often uses 995:995
    AST_UID=$(id -u asterisk 2>/dev/null || echo 995)
    AST_GID=$(id -g asterisk 2>/dev/null || echo 995)
    sudo chown -R $AST_UID:$AST_GID /mnt/asterisk_media
    sudo chmod 775 /mnt/asterisk_media /mnt/asterisk_media/ai-generated
    sudo ln -sfn /mnt/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated
    ls -ld /var/lib/asterisk/sounds/ai-generated
    ```

  - Optional for performance (Linux): mount tmpfs

    ```bash
    sudo mount -t tmpfs -o size=128m,mode=0775,uid=$AST_UID,gid=$AST_GID tmpfs /mnt/asterisk_media
    ```

  - Verify at runtime that `ai-engine` logs show files being written to `/mnt/asterisk_media/ai-generated` and calls can play `sound:ai-generated/<name>`.

## 4. Troubleshooting

- **Cannot connect to ARI**:
  - Verify that your Asterisk `host`, `username`, and `password` are correct in the `.env` file.
  - Ensure that the ARI port (usually 8088) is accessible from the Docker container.
  - Check your `ari.conf` and `http.conf` in Asterisk.
- **AI does not respond**:
  - Check that your API keys in the `.env` file are correct.
- **Audio Quality Issues**:
  - Confirm AudioSocket is connected (see Asterisk CLI and `ai-engine` logs).
  - Use a tmpfs for media files (e.g., `/mnt/asterisk_media`) to minimize I/O latency for file-based playback.
  - Verify you are not appending file extensions to ARI `sound:` URIs (Asterisk will add them automatically).

- **No host Python 3 installed (scripts/Makefile)**:
  - The Makefile auto-falls back to running helper scripts inside the `ai-engine` container. You’ll see a hint when it does.
  - Check your environment:

        ```bash
        make check-python
        ```

  - Run helpers directly in the container if desired:

        ```bash
        docker-compose exec -T ai-engine python /app/scripts/validate_externalmedia_config.py
        docker-compose exec -T ai-engine python /app/scripts/test_externalmedia_call.py
        docker-compose exec -T ai-engine python /app/scripts/monitor_externalmedia.py
        docker-compose exec -T ai-engine python /app/scripts/capture_test_logs.py --duration 40
        docker-compose exec -T ai-engine python /app/scripts/analyze_logs.py /app/logs/latest.json
        ```

For more advanced troubleshooting, refer to the project's main `README.md` or open an issue in the repository.
