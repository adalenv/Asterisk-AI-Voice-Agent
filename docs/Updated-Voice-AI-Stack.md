# Updated Local Voice AI Stack for Asterisk-AI-Voice-Agent

## Executive Summary - Tailored to Your Requirements

Based on your clarifications:
- **Current STT**: VOSK → **Recommended Upgrade**: Faster-Whisper base
- **Conversation Type**: General assistant with role-playing capability
- **Response Style**: Brief and to-the-point, elaborate only if needed
- **Hardware**: CPU-only currently (16 cores, 30GB RAM), install script should support GPU detection

## Critical Recommendation: Upgrade from VOSK to Faster-Whisper

### Why VOSK is Limiting Your Assistant

**VOSK Performance:**
- Accuracy: 70-85% Word Error Rate
- Vocabulary: Limited, basic models
- Robustness: Struggles with accents, background noise, complex speech[244][253]
- Best For: Simple command recognition ("turn on lights", "open door")

**Your Use Case Problem:**
For a **general assistant with role-playing**, users will say things like:
- "Pretend you're a technical support agent and help me troubleshoot my router"
- "Act as a travel advisor and suggest a 3-day itinerary for Paris"
- Complex, nuanced requests that VOSK will frequently misunderstand

**Impact**: Your LLM receives **wrong transcription** → generates **wrong response** → poor user experience

### Faster-Whisper: The Essential Upgrade

**Faster-Whisper Performance on Your Hardware:**
- **Accuracy**: 90-95% WER (significantly better understanding)[241][247][250]
- **Latency**: 200-400ms for base model (still real-time capable)[221][244]
- **Memory**: ~1GB (leaves plenty for LLM + TTS)
- **CPU Usage**: 2-4 cores efficiently
- **Speed**: 4-10x faster than original Whisper, up to 380x on long audio[247][250]

**Key Advantages:**
1. **CTranslate2 Optimization**: Specifically optimized for CPU inference[221]
2. **Robust**: Handles accents, conversational speech, background noise much better[241]
3. **Streaming Support**: Can process audio in real-time chunks[244]
4. **Multiple Model Sizes**: tiny/base/small/medium - choose based on accuracy vs speed needs

### Performance Comparison Table

| Metric | VOSK | Faster-Whisper (base) | Impact |
|--------|------|----------------------|--------|
| **Accuracy** | 70-85% | 90-95% | 10-25% fewer errors |
| **Latency** | 100-200ms | 200-400ms | +200ms (worth it!) |
| **Robustness** | Basic | Excellent | Better role-play understanding |
| **Memory** | 50-300MB | ~1GB | Still plenty left |
| **CPU Cores** | 1-2 | 2-4 | Fits your 16-core system |

**Verdict**: The +200ms latency is **absolutely worth** the accuracy improvement for a general assistant with role-playing. Users won't tolerate an assistant that frequently misunderstands them, even if it responds quickly.

## Recommended TTS: Piper (Primary) or Kokoro-82M (Quality)

### Piper TTS ⚡ FASTEST FOR TELEPHONY

**Performance:**
- **Latency**: 100-200ms (industry-leading for CPU)[242][248][254]
- **Quality**: Good (clear and understandable, slightly robotic)[242][257]
- **Memory**: 50-100MB (extremely lightweight)
- **CPU**: Very low usage (can run on Raspberry Pi)[248][254]
- **Streaming**: Yes (generates audio as text arrives)

**Why Piper:**
- Proven in production telephony systems[254][257]
- Extremely reliable and stable
- Multiple voice options available
- Perfect for "brief and to-the-point" responses
- Works exceptionally well on CPU-only systems

**Installation:**
```bash
# Install Piper
pip install piper-tts

# Download voice model
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/voice-en-us-lessac-medium.tar.gz
tar -xvf voice-en-us-lessac-medium.tar.gz

# Test
echo "Hello, this is a test" | piper --model voice-en-us-lessac-medium.onnx --output_file test.wav
```

### Kokoro-82M 🎯 BEST BALANCE

**Performance:**
- **Latency**: <300ms consistently[242]
- **Quality**: Excellent (natural prosody and emotion)[242]
- **Memory**: 200-300MB
- **CPU**: Low usage
- **Streaming**: Yes

**Why Kokoro:**
- Best speed-to-quality ratio in 2025 benchmarks[242]
- More natural-sounding than Piper
- Still fast enough for real-time telephony
- Better for role-playing scenarios where tone matters

**When to Choose Which:**
- **Piper**: Speed is critical, resources are limited, simple informational responses
- **Kokoro**: Quality matters, role-playing needs natural tone, slightly more resources available

## Updated LLM Configuration for Brief Responses

### System Prompt for Role-Playing + Brevity

```python
SYSTEM_PROMPT = """You are a versatile AI assistant that can take on different roles as requested by the user.

Guidelines:
- Keep responses brief and to-the-point (1-3 sentences for simple questions)
- Only provide detailed explanations when explicitly asked or when complexity requires it
- For telephony conversations: Be conversational, not essay-like
- Adapt your role based on user requests (customer service, technical expert, travel advisor, etc.)
- Ask ONE clarifying question if the user's intent is unclear, don't ask multiple questions
- When role-playing, embody that role naturally but still keep responses concise

Examples:
User: "What's the weather like?"
Assistant: "I don't have access to real-time weather data, but I can help you find weather information. What city are you asking about?"

User: "Pretend you're a technical support agent. My internet isn't working."
Assistant: "I understand your internet is down. Let's troubleshoot - first, can you see any lights on your router or modem?"

Remember: You're on a phone call - be concise, helpful, and adapt to the role requested."""
```

### Optimized Parameters for Telephony

```python
LLM_CONFIG = {
    "model": "qwen2.5:1.5b-instruct-q4_K_M",
    "temperature": 0.7,        # Natural conversation
    "top_p": 0.9,
    "repeat_penalty": 1.1,     # Avoid repetition
    "num_predict": 128,        # Reduced from 256 for brevity
    "stop": [                  # Stop on natural conversation breaks
        "User:",
        "Assistant:", 
        "\n\n\n",
        "Human:",
        "Question:"
    ],
    "stream": True             # CRITICAL for perceived low latency
}
```

### Integration Example

```python
def query_llm_brief(prompt, role_context=None):
    """Query LLM with brief response optimization"""
    
    # Add role context if provided
    if role_context:
        full_prompt = f"[ROLE: {role_context}]\n\n{prompt}"
    else:
        full_prompt = prompt
    
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "qwen2.5:1.5b-instruct-q4_K_M",
        "prompt": full_prompt,
        "system": SYSTEM_PROMPT,
        "stream": True,
        "options": LLM_CONFIG
    }
    
    # Stream response
    response_tokens = []
    for chunk in requests.post(url, json=payload, stream=True).iter_lines():
        if chunk:
            data = json.loads(chunk)
            if "response" in data:
                token = data["response"]
                response_tokens.append(token)
                yield token  # Stream to TTS immediately
                
                # Early stopping if response is getting too long
                if len(response_tokens) > 150:  # ~120 words max
                    break
```

## Complete Updated Pipeline Architecture

```
┌─────────────┐
│   Caller    │
└──────┬──────┘
       │ Audio (μ-law 8kHz)
       ▼
┌─────────────────┐
│  Asterisk SIP   │
└──────┬──────────┘
       │ PCM16 8kHz
       ▼
┌─────────────────────────────┐
│   AudioSocket (Your Engine) │
└──────┬──────────────────────┘
       │
       ├──► STT: Faster-Whisper base (CPU) ⭐ UPGRADED
       │    • Latency: 200-400ms
       │    • Accuracy: 90-95% (vs VOSK 70-85%)
       │    • Memory: ~1GB
       │    • CPU: 2-4 cores
       │
       ├──► LLM: Qwen2.5-1.5B (CPU) ⭐ OPTIMIZED FOR BREVITY
       │    • Latency: 300-500ms first token
       │    • Streaming: 15-25 tokens/sec
       │    • Brief responses: 128 token limit
       │    • Role-playing capable
       │    • CPU: 12-14 cores
       │
       └──► TTS: Piper or Kokoro-82M (CPU) ⭐ YOUR CHOICE
            • Piper: 100-200ms (fastest)
            • Kokoro: <300ms (best quality)
            • CPU: 2-4 cores

Total End-to-End: 1.0-1.5 seconds (HIGH ACCURACY + FAST)
Memory Usage: ~3-4GB total (plenty of room in 30GB)
```

### vs. Your Current VOSK Setup

```
❌ Current (VOSK):
User speaks → VOSK (0.15s, 75% accurate) ← BOTTLENECK
           → LLM (processes WRONG text)
           → TTS (0.2s)
           = 0.8-1.3 seconds BUT POOR ACCURACY

✅ Recommended (Faster-Whisper):
User speaks → Faster-Whisper (0.3s, 93% accurate) ← MUCH BETTER
           → LLM (processes CORRECT text)
           → Piper TTS (0.15s)
           = 1.0-1.5 seconds WITH HIGH ACCURACY

+200ms latency = Worth it for 20% accuracy improvement!
```

## Enhanced install.sh with GPU Detection

### GPU Detection Logic

```bash
#!/bin/bash

# GPU Detection and Configuration
# For Asterisk-AI-Voice-Agent install.sh

detect_gpu() {
    echo "Detecting available GPU hardware..."
    
    HAS_NVIDIA=false
    HAS_AMD=false
    HAS_INTEL=false
    GPU_TYPE="none"
    
    # Check for NVIDIA GPU
    if command -v nvidia-smi &> /dev/null; then
        if nvidia-smi &> /dev/null; then
            HAS_NVIDIA=true
            GPU_TYPE="nvidia"
            NVIDIA_GPU_COUNT=$(nvidia-smi --list-gpus | wc -l)
            NVIDIA_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1)
            echo "✓ Detected NVIDIA GPU: $NVIDIA_GPU_COUNT GPU(s) with ${NVIDIA_VRAM}MB VRAM"
            
            # Check CUDA availability in Python
            CUDA_AVAILABLE=$(python3 -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "false")
            if [ "$CUDA_AVAILABLE" = "True" ]; then
                echo "✓ CUDA is available in PyTorch"
            else
                echo "⚠ CUDA not available in PyTorch - will install CUDA-enabled PyTorch"
            fi
        fi
    fi
    
    # Check for AMD GPU (ROCm)
    if command -v rocm-smi &> /dev/null; then
        if rocm-smi &> /dev/null; then
            HAS_AMD=true
            GPU_TYPE="amd"
            echo "✓ Detected AMD GPU with ROCm support"
        fi
    fi
    
    # Check for Intel GPU
    if lspci | grep -i 'VGA.*Intel' &> /dev/null; then
        HAS_INTEL=true
        echo "✓ Detected Intel integrated GPU"
    fi
    
    # If no GPU detected, use CPU
    if [ "$GPU_TYPE" = "none" ]; then
        echo "ℹ No dedicated GPU detected - will use CPU-optimized models"
        CPU_CORES=$(nproc)
        TOTAL_RAM=$(free -g | awk '/^Mem:/{print $2}')
        echo "  CPU: $CPU_CORES cores"
        echo "  RAM: ${TOTAL_RAM}GB"
    fi
    
    export HAS_NVIDIA HAS_AMD HAS_INTEL GPU_TYPE
}

install_stt_model() {
    echo ""
    echo "Installing STT (Speech-to-Text) model..."
    
    if [ "$GPU_TYPE" = "nvidia" ] && [ "${NVIDIA_VRAM:-0}" -gt 6000 ]; then
        echo "GPU detected with sufficient VRAM - installing Faster-Whisper with GPU support"
        pip install faster-whisper
        
        # Choose model size based on VRAM
        if [ "${NVIDIA_VRAM:-0}" -gt 12000 ]; then
            STT_MODEL="medium"
            echo "Selected: Whisper medium (best accuracy, needs 12GB+ VRAM)"
        elif [ "${NVIDIA_VRAM:-0}" -gt 8000 ]; then
            STT_MODEL="small"
            echo "Selected: Whisper small (good accuracy, needs 8GB+ VRAM)"
        else
            STT_MODEL="base"
            echo "Selected: Whisper base (balanced, needs 6GB+ VRAM)"
        fi
    else
        echo "CPU-only or limited VRAM - installing CPU-optimized Faster-Whisper"
        pip install faster-whisper
        
        # CPU-optimized model selection
        if [ "${TOTAL_RAM:-0}" -gt 24 ]; then
            STT_MODEL="small"
            echo "Selected: Whisper small (best accuracy for CPU)"
        else
            STT_MODEL="base"
            echo "Selected: Whisper base (recommended for CPU)"
        fi
    fi
    
    echo "Downloading Faster-Whisper $STT_MODEL model..."
    python3 -c "from faster_whisper import WhisperModel; model = WhisperModel('${STT_MODEL}', device='cpu', compute_type='int8')"
    
    echo "✓ STT model installed: Faster-Whisper ${STT_MODEL}"
    export STT_MODEL
}

install_tts_model() {
    echo ""
    echo "Installing TTS (Text-to-Speech) model..."
    echo "Select TTS engine:"
    echo "1) Piper (Fastest - 100-200ms, good quality)"
    echo "2) Kokoro-82M (Best balance - <300ms, excellent quality)"
    echo "3) Coqui TTS (High quality - slower, 500ms+)"
    read -p "Enter choice [1-3] (default: 1): " TTS_CHOICE
    TTS_CHOICE=${TTS_CHOICE:-1}
    
    case $TTS_CHOICE in
        1)
            echo "Installing Piper TTS..."
            pip install piper-tts
            
            # Download voice model
            mkdir -p models/tts
            cd models/tts
            wget -q https://github.com/rhasspy/piper/releases/download/v1.2.0/voice-en-us-lessac-medium.tar.gz
            tar -xzf voice-en-us-lessac-medium.tar.gz
            cd ../..
            
            TTS_ENGINE="piper"
            TTS_MODEL="models/tts/voice-en-us-lessac-medium.onnx"
            echo "✓ Piper TTS installed with en-us-lessac voice"
            ;;
        2)
            echo "Installing Kokoro-82M..."
            pip install kokoro-tts  # Hypothetical - adjust to actual installation
            TTS_ENGINE="kokoro"
            TTS_MODEL="kokoro-82m"
            echo "✓ Kokoro-82M TTS installed"
            ;;
        3)
            echo "Installing Coqui TTS..."
            pip install TTS
            TTS_ENGINE="coqui"
            TTS_MODEL="tts_models/en/ljspeech/tacotron2-DDC"
            echo "✓ Coqui TTS installed"
            ;;
    esac
    
    export TTS_ENGINE TTS_MODEL
}

install_llm() {
    echo ""
    echo "Installing Local LLM..."
    
    # Detect if GPU can accelerate LLM
    if [ "$GPU_TYPE" = "nvidia" ] && [ "${NVIDIA_VRAM:-0}" -gt 4000 ]; then
        echo "NVIDIA GPU detected - can use GPU acceleration for LLM"
        USE_GPU="yes"
    else
        echo "Using CPU-optimized LLM"
        USE_GPU="no"
    fi
    
    # Choose installation method
    echo ""
    echo "Choose LLM framework:"
    echo "1) Ollama (Recommended - easier)"
    echo "2) llama.cpp (Advanced - more control)"
    read -p "Enter choice [1-2]: " LLM_FRAMEWORK
    
    if [ "$LLM_FRAMEWORK" -eq 1 ]; then
        # Install Ollama
        curl -fsSL https://ollama.com/install.sh | sh
        
        # Pull model with GPU consideration
        if [ "$USE_GPU" = "yes" ]; then
            echo "Pulling Qwen2.5-3B (using GPU acceleration)..."
            ollama pull qwen2.5:3b-instruct-q4_K_M
            LLM_MODEL="qwen2.5:3b-instruct-q4_K_M"
        else
            echo "Pulling Qwen2.5-1.5B (CPU-optimized)..."
            ollama pull qwen2.5:1.5b-instruct-q4_K_M
            LLM_MODEL="qwen2.5:1.5b-instruct-q4_K_M"
        fi
        
        # Start Ollama service
        systemctl --user enable ollama
        systemctl --user start ollama
        
        echo "✓ Ollama installed with model: ${LLM_MODEL}"
    else
        # Install llama.cpp with GPU support if available
        cd /opt
        sudo git clone https://github.com/ggerganov/llama.cpp
        cd llama.cpp
        
        if [ "$USE_GPU" = "yes" ]; then
            echo "Building llama.cpp with CUDA support..."
            sudo make -j $(nproc) LLAMA_CUDA=1
        else
            echo "Building llama.cpp for CPU..."
            sudo make -j $(nproc) LLAMA_AVX2=1 LLAMA_AVX512=1
        fi
        
        echo "✓ llama.cpp installed"
    fi
    
    export LLM_MODEL USE_GPU
}

# Main installation flow
main() {
    echo "=================================="
    echo "Voice AI Stack Installation"
    echo "=================================="
    
    detect_gpu
    install_stt_model
    install_tts_model
    install_llm
    
    # Generate config file
    cat > config/voice_stack.yaml <<EOF
voice_stack:
  stt:
    engine: faster-whisper
    model: ${STT_MODEL}
    device: ${GPU_TYPE}
    
  llm:
    model: ${LLM_MODEL}
    use_gpu: ${USE_GPU}
    max_tokens: 128  # Brief responses
    temperature: 0.7
    system_prompt: |
      You are a versatile AI assistant that can take on different roles.
      Keep responses brief (1-3 sentences for simple questions).
      Adapt your role as requested. Only elaborate when needed.
      
  tts:
    engine: ${TTS_ENGINE}
    model: ${TTS_MODEL}
    
hardware:
  gpu_type: ${GPU_TYPE}
  gpu_vram: ${NVIDIA_VRAM:-0}
  cpu_cores: ${CPU_CORES:-16}
  ram_gb: ${TOTAL_RAM:-30}
EOF
    
    echo ""
    echo "=================================="
    echo "Installation Complete!"
    echo "=================================="
    echo ""
    echo "Voice Stack Configuration:"
    echo "  STT: Faster-Whisper ${STT_MODEL}"
    echo "  LLM: ${LLM_MODEL}"
    echo "  TTS: ${TTS_ENGINE}"
    echo "  Hardware: ${GPU_TYPE} (CPU cores: ${CPU_CORES:-16})"
    echo ""
    echo "Config saved to: config/voice_stack.yaml"
    echo ""
    echo "Next steps:"
    echo "1. Test STT: python3 -m faster_whisper.test"
    echo "2. Test LLM: curl http://localhost:11434/api/generate -d '{\"model\":\"${LLM_MODEL}\",\"prompt\":\"Hello\"}'"
    echo "3. Start voice agent: python3 src/main.py"
}

main
```

## Testing Your Upgraded Stack

### 1. Test Faster-Whisper STT

```python
from faster_whisper import WhisperModel

# Initialize model
model = WhisperModel("base", device="cpu", compute_type="int8")

# Test with audio file
segments, info = model.transcribe("test_audio.wav", beam_size=5)

print(f"Detected language: {info.language} (probability: {info.language_probability})")

for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
```

### 2. Test Piper TTS

```bash
# Generate test audio
echo "Hello, I am your AI assistant. How can I help you today?" | \
  piper --model models/tts/voice-en-us-lessac-medium.onnx \
  --output_file test_output.wav

# Play it
aplay test_output.wav
```

### 3. Test Complete Pipeline with Role-Playing

```python
import time
from faster_whisper import WhisperModel
import subprocess
import requests
import json

def test_voice_pipeline_with_roles():
    """Test complete pipeline with role-playing"""
    
    # Initialize STT
    whisper = WhisperModel("base", device="cpu", compute_type="int8")
    
    # Test scenarios
    test_cases = [
        {
            "audio": "test_general_question.wav",
            "expected_behavior": "Brief answer",
            "role": None
        },
        {
            "audio": "test_roleplay_request.wav",
            "expected_behavior": "Adopt technical support role",
            "role": "technical support"
        }
    ]
    
    for test in test_cases:
        print(f"\nTesting: {test['expected_behavior']}")
        
        # STT
        start = time.time()
        segments, _ = whisper.transcribe(test["audio"])
        transcription = " ".join([s.text for s in segments])
        stt_time = time.time() - start
        print(f"  STT ({stt_time*1000:.0f}ms): {transcription}")
        
        # LLM
        start = time.time()
        llm_prompt = transcription
        if test["role"]:
            llm_prompt = f"[ROLE: {test['role']}]\n\n{transcription}"
        
        response = requests.post("http://localhost:11434/api/generate", json={
            "model": "qwen2.5:1.5b-instruct-q4_K_M",
            "prompt": llm_prompt,
            "stream": False,
            "options": {
                "num_predict": 128,
                "temperature": 0.7
            }
        })
        
        llm_response = response.json()["response"]
        llm_time = time.time() - start
        print(f"  LLM ({llm_time*1000:.0f}ms): {llm_response}")
        
        # TTS
        start = time.time()
        subprocess.run([
            "piper",
            "--model", "models/tts/voice-en-us-lessac-medium.onnx",
            "--output_file", "response.wav"
        ], input=llm_response.encode(), check=True)
        tts_time = time.time() - start
        print(f"  TTS ({tts_time*1000:.0f}ms): Generated audio")
        
        total = stt_time + llm_time + tts_time
        print(f"  Total: {total:.2f}s")
        
        # Check response length (should be brief)
        word_count = len(llm_response.split())
        if word_count > 50:
            print(f"  ⚠ Response too long: {word_count} words (should be <50 for brief response)")
        else:
            print(f"  ✓ Response length appropriate: {word_count} words")

if __name__ == "__main__":
    test_voice_pipeline_with_roles()
```

## Migration Guide: VOSK → Faster-Whisper

### 1. Update Dependencies

```bash
# Remove VOSK
pip uninstall vosk

# Install Faster-Whisper
pip install faster-whisper
```

### 2. Update Your STT Code

**Old (VOSK):**
```python
from vosk import Model, KaldiRecognizer

model = Model("model-path")
recognizer = KaldiRecognizer(model, 16000)

# Process audio
if recognizer.AcceptWaveform(audio_data):
    result = json.loads(recognizer.Result())
    text = result["text"]
```

**New (Faster-Whisper):**
```python
from faster_whisper import WhisperModel

model = WhisperModel("base", device="cpu", compute_type="int8")

# Process audio
segments, info = model.transcribe(audio_file)
text = " ".join([segment.text for segment in segments])
```

### 3. Update Config

```yaml
# config.yaml
stt:
  engine: faster-whisper  # Changed from vosk
  model: base            # Changed from vosk-model-small-en-us-0.15
  device: cpu
  compute_type: int8      # CPU optimization
  beam_size: 5           # Accuracy vs speed tradeoff
  vad_filter: true       # Enable voice activity detection
```

## Summary: Your Updated Voice AI Stack

```
✅ RECOMMENDED CONFIGURATION

STT: Faster-Whisper base
  • 90-95% accuracy (vs VOSK 70-85%)
  • 200-400ms latency
  • Essential for role-playing understanding
  • 1GB memory, 2-4 CPU cores

LLM: Qwen2.5-1.5B-Instruct Q4_K_M
  • 15-25 tokens/sec streaming
  • Brief response optimization (128 token limit)
  • Role-playing capable with system prompt
  • 2GB memory, 12-14 CPU cores

TTS: Piper (primary) or Kokoro-82M (quality)
  • Piper: 100-200ms, proven telephony
  • Kokoro: <300ms, excellent prosody
  • Both support streaming
  • <300MB memory, 2-4 CPU cores

TOTAL PERFORMANCE:
  • Latency: 1.0-1.5 seconds end-to-end
  • Memory: 3-4GB total (fits comfortably in 30GB)
  • CPU: 16 cores efficiently utilized
  • Accuracy: High quality understanding and responses
```

**Key Takeaway**: The upgrade from VOSK to Faster-Whisper is CRITICAL for your use case. Role-playing and general assistance require accurate transcription - VOSK's 70-85% accuracy will lead to frequent misunderstandings and poor user experience. The additional 200ms latency is worth the 20% accuracy improvement!