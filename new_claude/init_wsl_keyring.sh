#!/bin/bash
# ==============================================================================
# WSL2 / WSLg GNOME Keyring & Secret Service Provisioner for Claude Desktop
# Provisions a passwordless, unlocked login.keyring so Secret Service clients never show
# the "Unlock Login Keyring" prompt.
# NOTE: the Claude Desktop launchers run Electron with --password-store=basic, so Electron does NOT
# use this keyring: safeStorage.isEncryptionAvailable() is false (backend=basic_text) and app
# secrets are stored in plaintext inside the isolated profile. See skills/claude_wsl_gui/SKILL.md.
# ==============================================================================
set -e

USER_UID=$(id -u)
USER_GID=$(id -g)
USER_NAME=$(id -un)
TARGET_HOME="${1:-$HOME}"

echo "=========================================================="
echo " [Keyring Provisioner] Setting up Secret Service for:"
echo " User: $USER_NAME (UID: $USER_UID, GID: $USER_GID)"
echo " Target Home: $TARGET_HOME"
echo "=========================================================="

# 1. Verify required system packages
MISSING_PKGS=()
for pkg in gnome-keyring libsecret-tools dbus; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "[*] Installing missing dependencies: ${MISSING_PKGS[*]}..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${MISSING_PKGS[@]}"
fi

# 2. Ensure /run/user/<uid> base directory exists with 700 permissions
RUNTIME_BASE="/run/user/$USER_UID"
if [ ! -d "$RUNTIME_BASE" ]; then
    echo "[*] Creating runtime directory: $RUNTIME_BASE"
    mkdir -m 700 -p "$RUNTIME_BASE" 2>/dev/null || sudo mkdir -m 700 -p "$RUNTIME_BASE"
    sudo chown "$USER_UID:$USER_GID" "$RUNTIME_BASE" 2>/dev/null
fi

# 3. Ensure primary DBus session bus is active
BUS_SOCKET="$RUNTIME_BASE/bus"
if [ ! -e "$BUS_SOCKET" ] && command -v dbus-daemon >/dev/null 2>&1; then
    echo "[*] Starting user DBus session daemon on $BUS_SOCKET..."
    dbus-daemon --session --fork --address="unix:path=$BUS_SOCKET" 2>/dev/null
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=$BUS_SOCKET"

# 4. Provision passwordless login.keyring
KEYRINGS_DIR="$TARGET_HOME/.local/share/keyrings"
mkdir -m 700 -p "$KEYRINGS_DIR" 2>/dev/null

LOGIN_KEYRING="$KEYRINGS_DIR/login.keyring"
if [ ! -f "$LOGIN_KEYRING" ]; then
    echo "[*] Creating passwordless login.keyring at $LOGIN_KEYRING..."
    cat << 'EOF' > "$LOGIN_KEYRING"
[keyring]
display-name=Login
ctime=1695477404
mtime=0
lock-on-idle=false
lock-after=false
EOF
    chmod 600 "$LOGIN_KEYRING"
fi

DEFAULT_FILE="$KEYRINGS_DIR/default"
if [ ! -f "$DEFAULT_FILE" ] || [ "$(cat "$DEFAULT_FILE" 2>/dev/null)" != "login" ]; then
    echo "login" > "$DEFAULT_FILE"
    chmod 644 "$DEFAULT_FILE"
fi

# 5. Start and unlock gnome-keyring-daemon if not running
if ! pgrep -u "$USER_UID" -f "gnome-keyring-daemon" >/dev/null 2>&1; then
    echo "[*] Launching gnome-keyring-daemon..."
    # Empty password on stdin (not "\n"): matches the passwordless login.keyring, same as the launchers
    eval $(printf "" | gnome-keyring-daemon --daemonize --login --components=secrets 2>/dev/null)
    sleep 0.5
fi

# 6. Self-test Secret Service read/write via secret-tool
echo "[*] Validating Secret Service encryption backend..."
TEST_VAL="safestorage_probe_$(date +%s)"
if echo "$TEST_VAL" | secret-tool store --label="Claude Keyring Probe" service claude_probe user test 2>/dev/null; then
    READ_VAL=$(secret-tool lookup service claude_probe user test 2>/dev/null)
    secret-tool clear service claude_probe user test 2>/dev/null || true
    if [ "$READ_VAL" = "$TEST_VAL" ]; then
        echo "[+] SUCCESS: GNOME Keyring is active, unlocked, and responsive on DBus!"
        echo "[i] Claude Desktop launchers use --password-store=basic: Electron safeStorage stays on"
        echo "    basic_text (isEncryptionAvailable=false); this keyring only prevents unlock prompts."
        exit 0
    fi
fi

echo "[-] ERROR: Secret Service test failed. Check dbus-daemon and gnome-keyring-daemon logs." >&2
exit 1
