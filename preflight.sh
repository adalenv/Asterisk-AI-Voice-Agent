#!/bin/bash
# preflight.sh - Prepare system for AAVA Admin UI
# AAVA-126: Cross-Platform Support
#
# Usage:
#   ./preflight.sh              # Check system, show issues
#   ./preflight.sh --apply-fixes # Auto-fix what we can
#   ./preflight.sh --help        # Show usage
#
# Exit codes:
#   0 = All checks passed
#   1 = Warnings only (can proceed)
#   2 = Failures (blocking issues)

# NOTE: No 'set -e' - we want to collect ALL issues before exiting

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_ok() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; WARNINGS+=("$1"); }
log_fail() { echo -e "${RED}✗${NC} $1"; FAILURES+=("$1"); }
log_info() { echo -e "${BLUE}ℹ${NC} $1"; }

# State
WARNINGS=()
FAILURES=()
FIX_CMDS=()          # Commands that --apply-fixes will run
MANUAL_CMDS=()       # Commands user must run manually (e.g., reboot/logout)
APPLY_FIXES=false
DOCKER_ROOTLESS=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detected values
OS_ID="unknown"
OS_VERSION="unknown"
OS_FAMILY="unknown"
ARCH=""
ASTERISK_DIR=""
ASTERISK_FOUND=false
COMPOSE_CMD=""

# Parse args
for arg in "$@"; do
    case $arg in
        --apply-fixes) APPLY_FIXES=true ;;
        --help|-h) 
            echo "AAVA Pre-flight Check"
            echo ""
            echo "Usage: ./preflight.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --apply-fixes  Apply fixes automatically (may require sudo)"
            echo "  --help         Show this help message"
            echo ""
            echo "Exit codes:"
            echo "  0 = All checks passed"
            echo "  1 = Warnings only (can proceed)"
            echo "  2 = Failures (blocking issues)"
            exit 0 
            ;;
    esac
done

# ============================================================================
# OS Detection
# ============================================================================
detect_os() {
    IS_SANGOMA=false
    
    # Check for Sangoma/FreePBX first (it's RHEL/CentOS-based)
    if [ -f /etc/sangoma/pbx ] || [ -f /etc/freepbx.conf ]; then
        IS_SANGOMA=true
        OS_ID="sangoma"
        OS_FAMILY="rhel"  # Sangoma Linux is CentOS 7 based
    fi
    
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "$IS_SANGOMA" = false ]; then
            OS_ID="$ID"
        fi
        OS_VERSION="${VERSION_ID:-unknown}"
        if [ "$IS_SANGOMA" = false ]; then
            case "$ID" in
                ubuntu|debian|linuxmint) OS_FAMILY="debian" ;;
                centos|rhel|rocky|almalinux|fedora|sangoma) OS_FAMILY="rhel" ;;
            esac
        fi
    fi
    
    # Check architecture (HARD FAIL for non-x86_64)
    ARCH=$(uname -m)
    if [ "$ARCH" != "x86_64" ]; then
        log_fail "Unsupported architecture: $ARCH (x86_64 required - AAVA images are 64-bit only)"
    else
        log_ok "Architecture: $ARCH"
    fi
    
    # Check EOL status (WARNING only - we still support if Docker works)
    case "$OS_ID" in
        ubuntu)
            case "$OS_VERSION" in
                18.04) log_warn "Ubuntu 18.04 is EOL - consider upgrading to 22.04+" ;;
                20.04) log_warn "Ubuntu 20.04 standard support ends Apr 2025" ;;
            esac
            ;;
        debian)
            case "$OS_VERSION" in
                9) log_warn "Debian 9 is EOL - consider upgrading to 11+" ;;
                10) log_warn "Debian 10 LTS ended Jun 2024 - consider upgrading" ;;
            esac
            ;;
        centos)
            case "$OS_VERSION" in
                7) log_warn "CentOS 7 is EOL (Jun 2024) - consider migrating to Rocky/Alma" ;;
                8) log_warn "CentOS 8 is EOL (Dec 2021) - consider migrating to Rocky/Alma" ;;
            esac
            ;;
    esac
    
    log_ok "OS: $OS_ID $OS_VERSION ($OS_FAMILY family)"
}

# ============================================================================
# Docker Installation (for --apply-fixes)
# ============================================================================
install_docker_rhel() {
    log_info "Installing Docker for RHEL/CentOS family..."
    
    # Remove old Docker if present
    yum remove -y docker docker-client docker-client-latest docker-common \
        docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null
    
    # Install prerequisites
    yum install -y yum-utils
    
    # For Sangoma/FreePBX Distro, we need to create the repo manually
    # because yum-config-manager uses the distro version string which Docker doesn't recognize
    if [ "$OS_ID" = "sangoma" ] || [ -f /etc/sangoma/pbx ]; then
        log_info "Detected Sangoma/FreePBX - using CentOS 7 Docker repo"
        cat > /etc/yum.repos.d/docker-ce.repo << 'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/centos/7/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://download.docker.com/linux/centos/gpg
EOF
    else
        # Standard CentOS/RHEL - use yum-config-manager
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    fi
    
    # Install Docker CE
    yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    # Verify
    if docker --version &>/dev/null; then
        log_ok "Docker installed successfully"
        return 0
    else
        log_fail "Docker installation failed"
        return 1
    fi
}

install_docker_debian() {
    log_info "Installing Docker for Debian/Ubuntu family..."
    
    # Remove old Docker if present
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null
    
    # Install prerequisites
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    # Add Docker repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Install Docker CE
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    # Verify
    if docker --version &>/dev/null; then
        log_ok "Docker installed successfully"
        return 0
    else
        log_fail "Docker installation failed"
        return 1
    fi
}

# ============================================================================
# Docker Checks
# ============================================================================
check_docker() {
    if ! command -v docker &>/dev/null; then
        log_fail "Docker not installed"
        
        # Offer to install based on OS family
        if [ "$APPLY_FIXES" = true ]; then
            case "$OS_FAMILY" in
                rhel)
                    install_docker_rhel
                    ;;
                debian)
                    install_docker_debian
                    ;;
                *)
                    log_info "  Install manually: https://docs.docker.com/engine/install/"
                    ;;
            esac
        else
            case "$OS_FAMILY" in
                rhel)
                    log_info "  Fix: Run with --apply-fixes to auto-install Docker"
                    log_info "  Or manually: yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
                    FIX_CMDS+=("# Docker will be installed automatically with --apply-fixes")
                    ;;
                debian)
                    log_info "  Fix: Run with --apply-fixes to auto-install Docker"
                    log_info "  Or manually: apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
                    FIX_CMDS+=("# Docker will be installed automatically with --apply-fixes")
                    ;;
                *)
                    log_info "  Install: https://docs.docker.com/engine/install/"
                    ;;
            esac
        fi
        return 1
    fi
    
    # Detect rootless Docker BEFORE trying to access
    if [ -n "$DOCKER_HOST" ]; then
        DOCKER_ROOTLESS=true
    elif [ -n "$XDG_RUNTIME_DIR" ] && [ -S "$XDG_RUNTIME_DIR/docker.sock" ]; then
        DOCKER_ROOTLESS=true
        export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
    fi
    
    if ! docker ps &>/dev/null 2>&1; then
        # NOTE: We do NOT auto-start docker - that's a side effect
        if [ "$DOCKER_ROOTLESS" = true ]; then
            log_warn "Rootless Docker not running"
            MANUAL_CMDS+=("systemctl --user start docker")
        else
            # Check if it's a permission issue vs not running
            if sudo docker ps &>/dev/null 2>&1; then
                log_warn "Cannot access Docker daemon (permission denied)"
                MANUAL_CMDS+=("sudo usermod -aG docker \$USER")
                MANUAL_CMDS+=("# Then log out and back in, or run: newgrp docker")
            else
                log_warn "Docker daemon not running"
                MANUAL_CMDS+=("sudo systemctl start docker")
            fi
        fi
        return 1
    fi
    
    # Version check (HARD FAIL below minimum)
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
    DOCKER_MAJOR=$(echo "$DOCKER_VERSION" | cut -d. -f1)
    
    if [ "$DOCKER_MAJOR" -lt 20 ]; then
        log_fail "Docker $DOCKER_VERSION too old (minimum: 20.10) - upgrade required"
    elif [ "$DOCKER_MAJOR" -lt 25 ]; then
        log_warn "Docker $DOCKER_VERSION supported but upgrade to 25.x+ recommended"
    else
        log_ok "Docker: $DOCKER_VERSION"
    fi
    
    if [ "$DOCKER_ROOTLESS" = true ]; then
        log_ok "Docker mode: rootless"
    fi
}

# ============================================================================
# Docker Compose Checks
# ============================================================================
check_compose() {
    COMPOSE_CMD=""
    COMPOSE_VER=""
    
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        COMPOSE_VER=$(docker compose version --short 2>/dev/null | sed 's/^v//')
        
        # Create docker-compose wrapper if it doesn't exist (needed for admin-ui)
        if ! command -v docker-compose &>/dev/null; then
            if [ "$APPLY_FIXES" = true ]; then
                # Remove if it's a directory (broken state)
                [ -d /usr/local/bin/docker-compose ] && rm -rf /usr/local/bin/docker-compose
                
                echo '#!/bin/bash
docker compose "$@"' > /usr/local/bin/docker-compose
                chmod +x /usr/local/bin/docker-compose
                log_ok "Created docker-compose wrapper for compatibility"
            else
                log_warn "docker-compose command not found (admin-ui needs this)"
                FIX_CMDS+=("echo '#!/bin/bash\ndocker compose \"\$@\"' > /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose")
            fi
        fi
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        COMPOSE_VER=$(docker-compose version --short 2>/dev/null | sed 's/^v//')
        # Compose v1 is HARD FAIL - offer to upgrade
        log_fail "Docker Compose v1 detected - EOL July 2023, security risk"
        
        # Manual install works on all distros (including Sangoma/FreePBX)
        log_info "  Fix: Install Docker Compose v2 manually:"
        log_info "    sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose"
        log_info "    sudo chmod +x /usr/local/bin/docker-compose"
        log_info "    sudo mkdir -p /usr/local/lib/docker/cli-plugins"
        log_info "    sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose"
        
        # Add to FIX_CMDS for --apply-fixes
        FIX_CMDS+=("sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo mkdir -p /usr/local/lib/docker/cli-plugins && sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose")
        return 1
    fi
    
    if [ -z "$COMPOSE_CMD" ]; then
        log_fail "Docker Compose not found"
        log_info "  Fix: Install Docker Compose v2 manually:"
        log_info "    sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose"
        log_info "    sudo chmod +x /usr/local/bin/docker-compose"
        log_info "    sudo mkdir -p /usr/local/lib/docker/cli-plugins"
        log_info "    sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose"
        
        FIX_CMDS+=("sudo curl -L 'https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64' -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo mkdir -p /usr/local/lib/docker/cli-plugins && sudo ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose")
        return 1
    fi
    
    # Parse version (e.g., "2.20.0" -> major=2, minor=20)
    COMPOSE_MAJOR=$(echo "$COMPOSE_VER" | cut -d. -f1)
    COMPOSE_MINOR=$(echo "$COMPOSE_VER" | cut -d. -f2)
    
    if [ "$COMPOSE_MAJOR" -eq 2 ] && [ "$COMPOSE_MINOR" -lt 20 ]; then
        log_warn "Compose $COMPOSE_VER - upgrade to 2.20+ recommended (missing profiles, watch)"
    else
        log_ok "Docker Compose: $COMPOSE_VER"
    fi
    
    # Check buildx version (required >= 0.17 for compose build)
    if docker buildx version &>/dev/null 2>&1; then
        BUILDX_VER=$(docker buildx version 2>/dev/null | grep -oP 'v?\K[0-9]+\.[0-9]+' | head -1)
        BUILDX_MAJOR=$(echo "$BUILDX_VER" | cut -d. -f1)
        BUILDX_MINOR=$(echo "$BUILDX_VER" | cut -d. -f2)
        
        if [ "$BUILDX_MAJOR" -eq 0 ] && [ "$BUILDX_MINOR" -lt 17 ]; then
            log_warn "Docker Buildx $BUILDX_VER - requires 0.17+ for compose build"
            log_info "  Fix: curl -L https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 -o /usr/local/lib/docker/cli-plugins/docker-buildx && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx"
            FIX_CMDS+=("curl -L https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 -o /usr/local/lib/docker/cli-plugins/docker-buildx && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx")
        else
            log_ok "Docker Buildx: $BUILDX_VER"
        fi
    fi
}

# ============================================================================
# Directory Setup
# ============================================================================
check_directories() {
    MEDIA_DIR="${MEDIA_DIR:-/mnt/asterisk_media/ai-generated}"
    
    if [ -d "$MEDIA_DIR" ] && [ -w "$MEDIA_DIR" ]; then
        log_ok "Media directory: $MEDIA_DIR"
        return 0
    fi
    
    if [ ! -d "$MEDIA_DIR" ]; then
        log_warn "Media directory missing: $MEDIA_DIR"
    else
        log_warn "Media directory not writable: $MEDIA_DIR"
    fi
    
    # Rootless-aware fix commands
    if [ "$DOCKER_ROOTLESS" = true ]; then
        FIX_CMDS+=("mkdir -p $MEDIA_DIR")
        log_info "  Rootless tip: Use volume with :Z suffix for SELinux compatibility"
    else
        FIX_CMDS+=("sudo mkdir -p $MEDIA_DIR")
        FIX_CMDS+=("sudo chown -R \$(id -u):\$(id -g) $MEDIA_DIR")
    fi
}

# ============================================================================
# SELinux (RHEL family)
# ============================================================================
check_selinux() {
    [ "$OS_FAMILY" != "rhel" ] && return 0
    command -v getenforce &>/dev/null || return 0
    
    SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
    MEDIA_DIR="${MEDIA_DIR:-/mnt/asterisk_media/ai-generated}"
    
    if [ "$SELINUX_MODE" = "Enforcing" ]; then
        # Check if semanage is available
        if ! command -v semanage &>/dev/null; then
            log_warn "SELinux: Enforcing but semanage not installed"
            FIX_CMDS+=("sudo dnf install -y policycoreutils-python-utils")
        fi
        
        log_warn "SELinux: Enforcing (context fix may be needed for media directory)"
        FIX_CMDS+=("sudo semanage fcontext -a -t container_file_t '${MEDIA_DIR}(/.*)?'")
        FIX_CMDS+=("sudo restorecon -Rv ${MEDIA_DIR}")
    else
        log_ok "SELinux: $SELINUX_MODE"
    fi
}

# ============================================================================
# Environment File
# ============================================================================
check_env() {
    if [ -f "$SCRIPT_DIR/.env" ]; then
        log_ok ".env file exists"
        log_info "  Tip: For local_only pipeline, no API keys needed!"
    elif [ -f "$SCRIPT_DIR/.env.example" ]; then
        if [ "$APPLY_FIXES" = true ]; then
            cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
            log_ok "Created .env from .env.example"
            log_info "  Tip: For local_only pipeline, no API keys needed!"
            log_info "  For cloud providers, edit .env to add your API keys"
        else
            log_warn ".env file missing"
            FIX_CMDS+=("cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env")
            log_info "  Tip: For local_only pipeline, no API keys needed!"
        fi
    else
        log_warn ".env.example not found"
    fi
}

# ============================================================================
# Asterisk Detection
# ============================================================================
check_asterisk() {
    ASTERISK_DIR=""
    ASTERISK_FOUND=false
    
    # Common Asterisk config locations
    ASTERISK_PATHS=(
        "/etc/asterisk"
        "/usr/local/etc/asterisk"
        "/opt/asterisk/etc"
    )
    
    # Try to find Asterisk config directory
    for path in "${ASTERISK_PATHS[@]}"; do
        if [ -d "$path" ] && [ -f "$path/asterisk.conf" ]; then
            ASTERISK_DIR="$path"
            ASTERISK_FOUND=true
            break
        fi
    done
    
    # Check if Asterisk binary exists
    if command -v asterisk &>/dev/null; then
        ASTERISK_VERSION=$(asterisk -V 2>/dev/null | head -1 || echo "unknown")
        log_ok "Asterisk binary: $ASTERISK_VERSION"
    else
        log_info "Asterisk binary not found in PATH (may be containerized)"
    fi
    
    if [ "$ASTERISK_FOUND" = true ]; then
        log_ok "Asterisk config: $ASTERISK_DIR"
        
        # Check for FreePBX
        if [ -f "/etc/freepbx.conf" ] || [ -f "/etc/sangoma/pbx" ]; then
            FREEPBX_VERSION=$(fwconsole -V 2>/dev/null | head -1 || echo "detected")
            log_ok "FreePBX: $FREEPBX_VERSION"
        fi
    else
        log_info "Asterisk config directory not found (may be containerized or custom path)"
        
        # Interactive mode: ask user for path (with timeout)
        if [ -t 0 ] && [ -t 1 ]; then
            echo ""
            echo -e "${YELLOW}Enter Asterisk config directory path (or press Enter to skip):${NC}"
            read -t 10 -r USER_ASTERISK_PATH || USER_ASTERISK_PATH=""
            
            if [ -n "$USER_ASTERISK_PATH" ]; then
                if [ -d "$USER_ASTERISK_PATH" ]; then
                    if [ -f "$USER_ASTERISK_PATH/asterisk.conf" ]; then
                        ASTERISK_DIR="$USER_ASTERISK_PATH"
                        ASTERISK_FOUND=true
                        log_ok "Asterisk config: $ASTERISK_DIR (user provided)"
                        
                        # Save to .env for future use
                        if [ -f "$SCRIPT_DIR/.env" ]; then
                            if ! grep -q "ASTERISK_CONFIG_DIR" "$SCRIPT_DIR/.env"; then
                                echo "ASTERISK_CONFIG_DIR=$ASTERISK_DIR" >> "$SCRIPT_DIR/.env"
                                log_ok "Saved ASTERISK_CONFIG_DIR to .env"
                            fi
                        fi
                    else
                        log_warn "No asterisk.conf found in $USER_ASTERISK_PATH"
                    fi
                else
                    log_warn "Directory does not exist: $USER_ASTERISK_PATH"
                fi
            fi
        fi
    fi
}

# ============================================================================
# Asterisk UID/GID Detection
# ============================================================================
check_asterisk_uid_gid() {
    # Skip if Asterisk not found on host
    if ! command -v asterisk &>/dev/null && [ ! -f /etc/asterisk/asterisk.conf ]; then
        log_info "Asterisk not on host - skipping UID/GID detection"
        return 0
    fi
    
    local AST_UID=""
    local AST_GID=""
    
    # Try to get asterisk user UID/GID
    if id asterisk &>/dev/null; then
        AST_UID=$(id -u asterisk 2>/dev/null)
        AST_GID=$(id -g asterisk 2>/dev/null)
    elif getent passwd asterisk &>/dev/null; then
        AST_UID=$(getent passwd asterisk | cut -d: -f3)
        AST_GID=$(getent passwd asterisk | cut -d: -f4)
    fi
    
    if [ -z "$AST_UID" ] || [ -z "$AST_GID" ]; then
        log_warn "Could not detect Asterisk UID/GID - using defaults (995:995)"
        return 0
    fi
    
    log_ok "Asterisk UID:GID = $AST_UID:$AST_GID"
    
    # Set up media directory with setgid bit for group permission inheritance
    MEDIA_DIR="$SCRIPT_DIR/asterisk_media/ai-generated"
    if [ "$APPLY_FIXES" = true ]; then
        # Create directory if it doesn't exist
        mkdir -p "$MEDIA_DIR" 2>/dev/null
        
        # Change group ownership to asterisk group
        if sudo chgrp "$AST_GID" "$MEDIA_DIR" 2>/dev/null; then
            log_ok "Set media directory group to asterisk (GID=$AST_GID)"
        else
            log_warn "Could not set media directory group (may need sudo)"
            FIX_CMDS+=("sudo chgrp $AST_GID $MEDIA_DIR")
        fi
        
        # Set setgid bit so new files inherit group ownership
        if sudo chmod 2775 "$MEDIA_DIR" 2>/dev/null; then
            log_ok "Set media directory permissions (setgid enabled)"
        else
            log_warn "Could not set media directory permissions (may need sudo)"
            FIX_CMDS+=("sudo chmod 2775 $MEDIA_DIR")
        fi
        
        # Also set parent directory
        MEDIA_PARENT="$SCRIPT_DIR/asterisk_media"
        sudo chgrp "$AST_GID" "$MEDIA_PARENT" 2>/dev/null
        sudo chmod 2775 "$MEDIA_PARENT" 2>/dev/null
    else
        # Check if directory setup is needed
        if [ ! -d "$MEDIA_DIR" ]; then
            FIX_CMDS+=("mkdir -p $MEDIA_DIR")
        fi
        FIX_CMDS+=("sudo chgrp $AST_GID $MEDIA_DIR")
        FIX_CMDS+=("sudo chmod 2775 $MEDIA_DIR  # setgid for group inheritance")
    fi
    
    # Check if .env exists and update if needed
    if [ -f "$SCRIPT_DIR/.env" ]; then
        local NEEDS_UPDATE=false
        
        # Check if ASTERISK_UID is set correctly
        if grep -q "^ASTERISK_UID=" "$SCRIPT_DIR/.env"; then
            local CURRENT_UID=$(grep "^ASTERISK_UID=" "$SCRIPT_DIR/.env" | cut -d= -f2)
            if [ "$CURRENT_UID" != "$AST_UID" ]; then
                log_warn "ASTERISK_UID in .env ($CURRENT_UID) doesn't match system ($AST_UID)"
                NEEDS_UPDATE=true
            fi
        else
            # Not set, need to add if not default
            if [ "$AST_UID" != "995" ]; then
                NEEDS_UPDATE=true
            fi
        fi
        
        # Check if ASTERISK_GID is set correctly
        if grep -q "^ASTERISK_GID=" "$SCRIPT_DIR/.env"; then
            local CURRENT_GID=$(grep "^ASTERISK_GID=" "$SCRIPT_DIR/.env" | cut -d= -f2)
            if [ "$CURRENT_GID" != "$AST_GID" ]; then
                log_warn "ASTERISK_GID in .env ($CURRENT_GID) doesn't match system ($AST_GID)"
                NEEDS_UPDATE=true
            fi
        else
            # Not set, need to add if not default
            if [ "$AST_GID" != "995" ]; then
                NEEDS_UPDATE=true
            fi
        fi
        
        # Update .env if needed
        if [ "$NEEDS_UPDATE" = true ]; then
            if [ "$APPLY_FIXES" = true ]; then
                # Remove old entries if they exist
                sed -i.bak '/^ASTERISK_UID=/d' "$SCRIPT_DIR/.env" 2>/dev/null || \
                    sed -i '' '/^ASTERISK_UID=/d' "$SCRIPT_DIR/.env"
                sed -i.bak '/^ASTERISK_GID=/d' "$SCRIPT_DIR/.env" 2>/dev/null || \
                    sed -i '' '/^ASTERISK_GID=/d' "$SCRIPT_DIR/.env"
                
                # Add correct values
                echo "" >> "$SCRIPT_DIR/.env"
                echo "# Asterisk user UID/GID for file permissions (auto-detected by preflight.sh)" >> "$SCRIPT_DIR/.env"
                echo "ASTERISK_UID=$AST_UID" >> "$SCRIPT_DIR/.env"
                echo "ASTERISK_GID=$AST_GID" >> "$SCRIPT_DIR/.env"
                
                # Clean up backup file
                rm -f "$SCRIPT_DIR/.env.bak"
                
                log_ok "Updated .env with ASTERISK_UID=$AST_UID ASTERISK_GID=$AST_GID"
            else
                log_warn "ASTERISK_UID/GID need to be updated in .env"
                FIX_CMDS+=("# Update .env with: ASTERISK_UID=$AST_UID ASTERISK_GID=$AST_GID")
            fi
        fi
    else
        # No .env file yet - will be created by check_env, add to FIX_CMDS
        if [ "$AST_UID" != "995" ] || [ "$AST_GID" != "995" ]; then
            FIX_CMDS+=("echo 'ASTERISK_UID=$AST_UID' >> $SCRIPT_DIR/.env")
            FIX_CMDS+=("echo 'ASTERISK_GID=$AST_GID' >> $SCRIPT_DIR/.env")
        fi
    fi
}

# ============================================================================
# Port Check
# ============================================================================
check_ports() {
    PORT=3003
    if command -v ss &>/dev/null; then
        if ss -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use (Admin UI port)"
        else
            log_ok "Port $PORT available"
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tln | grep -q ":$PORT "; then
            log_warn "Port $PORT already in use (Admin UI port)"
        else
            log_ok "Port $PORT available"
        fi
    fi
}

# ============================================================================
# Apply Fixes
# ============================================================================
apply_fixes() {
    if [ ${#FIX_CMDS[@]} -eq 0 ]; then
        return 0
    fi
    
    echo ""
    echo -e "${YELLOW}Applying fixes...${NC}"
    
    local all_success=true
    for cmd in "${FIX_CMDS[@]}"; do
        echo "  Running: $cmd"
        if eval "$cmd" 2>/dev/null; then
            echo -e "    ${GREEN}✓${NC} Success"
        else
            echo -e "    ${RED}✗${NC} Failed (may need sudo)"
            all_success=false
        fi
    done
    
    # Re-validate after applying fixes
    if [ "$all_success" = true ]; then
        echo ""
        echo -e "${BLUE}Re-validating after fixes...${NC}"
        
        # Clear arrays and re-run checks
        WARNINGS=()
        FAILURES=()
        FIX_CMDS=()
        
        # Re-run the checks silently first, then show summary
        check_docker >/dev/null 2>&1
        check_compose >/dev/null 2>&1
        check_directories >/dev/null 2>&1
        check_selinux >/dev/null 2>&1
        check_env >/dev/null 2>&1
    fi
}

# ============================================================================
# Summary
# ============================================================================
print_summary() {
    echo ""
    echo "========================================"
    echo "Pre-flight Summary"
    echo "========================================"
    
    if [ ${#FAILURES[@]} -gt 0 ]; then
        echo -e "${RED}Failures (${#FAILURES[@]}) - BLOCKING:${NC}"
        for f in "${FAILURES[@]}"; do echo "  ✗ $f"; done
        echo ""
    fi
    
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        echo -e "${YELLOW}Warnings (${#WARNINGS[@]}):${NC}"
        for w in "${WARNINGS[@]}"; do echo "  ⚠ $w"; done
        echo ""
    fi
    
    if [ ${#MANUAL_CMDS[@]} -gt 0 ]; then
        echo -e "${YELLOW}Manual steps required:${NC}"
        for cmd in "${MANUAL_CMDS[@]}"; do echo "  $cmd"; done
        echo ""
    fi
    
    if [ ${#FIX_CMDS[@]} -gt 0 ] && [ "$APPLY_FIXES" = false ]; then
        echo -e "${YELLOW}Auto-fixable issues (run with --apply-fixes):${NC}"
        for cmd in "${FIX_CMDS[@]}"; do echo "  $cmd"; done
        echo ""
    fi
    
    if [ ${#FAILURES[@]} -eq 0 ] && [ ${#WARNINGS[@]} -eq 0 ]; then
        touch "$SCRIPT_DIR/.preflight-ok"
        echo -e "${GREEN}✓ All checks passed!${NC}"
        echo ""
        echo "Next steps:"
        echo ""
        echo "  1. Start the Admin UI:"
        echo "     ${COMPOSE_CMD:-docker compose} up -d admin-ui"
        echo ""
        echo "  2. Open: http://localhost:3003"
        echo ""
        echo "  3. For local_only pipeline, also start:"
        echo "     ${COMPOSE_CMD:-docker compose} up -d local-ai-server"
        echo ""
    elif [ ${#FAILURES[@]} -eq 0 ]; then
        touch "$SCRIPT_DIR/.preflight-ok"
        echo -e "${YELLOW}Checks passed with warnings.${NC}"
        echo ""
        echo "You can proceed, but consider addressing the warnings above."
        echo ""
        echo "Next steps:"
        echo ""
        echo "  1. Start the Admin UI:"
        echo "     ${COMPOSE_CMD:-docker compose} up -d admin-ui"
        echo ""
        echo "  2. Open: http://localhost:3003"
        echo ""
        echo "  3. For local_only pipeline, also start:"
        echo "     ${COMPOSE_CMD:-docker compose} up -d local-ai-server"
        echo ""
    else
        echo -e "${RED}Cannot proceed - fix failures above first.${NC}"
    fi
}

# ============================================================================
# Main
# ============================================================================
main() {
    echo ""
    echo "========================================"
    echo "AAVA Pre-flight Checks"
    echo "========================================"
    echo ""
    
    detect_os
    check_docker
    check_compose
    check_directories
    check_selinux
    check_env
    check_asterisk
    check_asterisk_uid_gid
    check_ports
    
    # Apply fixes if requested
    if [ "$APPLY_FIXES" = true ]; then
        apply_fixes
    fi
    
    print_summary
    
    # Exit code: 2 for failures (blocking), 1 for warnings only, 0 for clean
    if [ ${#FAILURES[@]} -gt 0 ]; then
        exit 2
    elif [ ${#WARNINGS[@]} -gt 0 ]; then
        exit 1
    fi
    exit 0
}

main "$@"
