#!/bin/bash
# ==============================================================================
# Isolated Claude Desktop GUI Launcher for WSL2 (WSLg)
# Instance: {INSTANCE_ID}
# Network: SOCKS5 127.0.0.1:{SOCKS_PORT} | HTTP 127.0.0.1:{HTTP_PORT}
# Profile: /home/{WSL_USER}/.claude-{INSTANCE_ID}/.config/Claude
# ==============================================================================

# 1. Source system WSL environment
source /etc/profile.d/herdr_claude_env.sh 2>/dev/null

# 2. Override proxy strictly for this isolated instance
export HTTPS_PROXY='http://127.0.0.1:{HTTP_PORT}'
export HTTP_PROXY='http://127.0.0.1:{HTTP_PORT}'
export ALL_PROXY='socks5h://127.0.0.1:{SOCKS_PORT}'
export https_proxy='http://127.0.0.1:{HTTP_PORT}'
export http_proxy='http://127.0.0.1:{HTTP_PORT}'
export all_proxy='socks5h://127.0.0.1:{SOCKS_PORT}'

# 3. Isolated environment variables
export DISPLAY=${DISPLAY:-:0}
USER_UID=$(id -u)
USER_GID=$(id -g)
export HOME="/home/{WSL_USER}/.claude-{INSTANCE_ID}"
export XDG_CONFIG_HOME="$HOME/.config"
export USER_DATA_DIR="$HOME/.config/Claude"
export XDG_RUNTIME_DIR="/run/user/$USER_UID/claude-{INSTANCE_ID}"
PID_FILE="$XDG_RUNTIME_DIR/claude-gui-{INSTANCE_ID}.pid"

# 4. Ensure /run/user/<uid> base directory exists
if [ ! -d "/run/user/$USER_UID" ]; then
    mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null || sudo mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null
    sudo chown "$USER_UID:$USER_GID" "/run/user/$USER_UID" 2>/dev/null
fi

# 5. Ensure user DBus session is running on the primary user bus
if [ ! -e "/run/user/$USER_UID/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
    dbus-daemon --session --fork --address="unix:path=/run/user/$USER_UID/bus" 2>/dev/null
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus"

# 6. Ensure isolated home, config, and runtime directories exist
mkdir -m 700 -p "$HOME" "$XDG_CONFIG_HOME" "$USER_DATA_DIR" "$XDG_RUNTIME_DIR" 2>/dev/null

# 7. Ensure keyring directories and default login keyring exist in isolated home
KEYRINGS_DIR="$HOME/.local/share/keyrings"
mkdir -m 700 -p "$KEYRINGS_DIR" 2>/dev/null
if [ ! -f "$KEYRINGS_DIR/login.keyring" ]; then
    cat << 'KEYRING_EOF' > "$KEYRINGS_DIR/login.keyring"
[keyring]
display-name=Login
ctime=1695477404
mtime=0
lock-on-idle=false
lock-after=false
KEYRING_EOF
    chmod 600 "$KEYRINGS_DIR/login.keyring"
fi
if [ ! -f "$KEYRINGS_DIR/default" ]; then
    echo "login" > "$KEYRINGS_DIR/default"
fi

# 8. Auto-start and unlock gnome-keyring-daemon if not already running
if ! pgrep -u "$USER_UID" -f "gnome-keyring-daemon" >/dev/null 2>&1; then
    eval $(printf "\n" | gnome-keyring-daemon --daemonize --login --components=secrets 2>/dev/null)
fi

find_instance_win() {
    local target_pid="$1"
    local win=""
    win=$(xdotool search --onlyvisible --pid "$target_pid" 2>/dev/null | head -n 1)
    if [ -n "$win" ]; then
        echo "$win"
        return 0
    fi
    for cpid in $(pgrep -P "$target_pid" 2>/dev/null); do
        win=$(xdotool search --onlyvisible --pid "$cpid" 2>/dev/null | head -n 1)
        if [ -n "$win" ]; then
            echo "$win"
            return 0
        fi
    done
    return 1
}

# 6. If window is already open, focus it and exit immediately
if [ -f "$PID_FILE" ] && command -v xdotool >/dev/null 2>&1; then
    SAVED_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$SAVED_PID" ] && kill -0 "$SAVED_PID" 2>/dev/null; then
        VISIBLE_WIN=$(find_instance_win "$SAVED_PID")
        if [ -n "$VISIBLE_WIN" ]; then
            xdotool windowactivate "$VISIBLE_WIN" 2>/dev/null
            exit 0
        fi
    fi
fi

# 7. Clean up only this instance's lingering processes and singleton locks
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ]; then
        pkill -9 -P "$OLD_PID" 2>/dev/null
        kill -9 "$OLD_PID" 2>/dev/null
    fi
    rm -f "$PID_FILE" 2>/dev/null
fi
pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
rm -f "$USER_DATA_DIR/Singleton"* 2>/dev/null

if ! command -v claude-desktop >/dev/null 2>&1; then
    echo "claude-desktop not found in PATH." >&2
    exit 1
fi

# 8. Launch Claude Desktop with Chromium proxy flag and GNOME libsecret keyring
claude-desktop --no-sandbox --ozone-platform=x11 --password-store=gnome-libsecret --proxy-server="socks5://127.0.0.1:{SOCKS_PORT}" --user-data-dir="$USER_DATA_DIR" "$@" &
MAIN_PID=$!
echo "$MAIN_PID" > "$PID_FILE"

# 9. Wait for window and monitor closing
if command -v xdotool >/dev/null 2>&1; then
    WIN_ID=""
    for i in $(seq 1 30); do
        if ! kill -0 "$MAIN_PID" 2>/dev/null; then
            rm -f "$PID_FILE" 2>/dev/null
            exit 1
        fi
        WIN_ID=$(find_instance_win "$MAIN_PID")
        if [ -n "$WIN_ID" ]; then
            break
        fi
        sleep 0.5
    done

    if [ -n "$WIN_ID" ]; then
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep 2
            CURRENT_WIN=$(find_instance_win "$MAIN_PID")
            if [ -z "$CURRENT_WIN" ]; then
                # User closed window: cleanly kill background processes of THIS instance only
                kill -TERM "$MAIN_PID" 2>/dev/null
                sleep 1
                pkill -9 -P "$MAIN_PID" 2>/dev/null
                kill -9 "$MAIN_PID" 2>/dev/null
                pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
                pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
                rm -f "$USER_DATA_DIR/Singleton"* 2>/dev/null
                rm -f "$PID_FILE" 2>/dev/null
                break
            fi
        done
        exit 0
    fi
fi

wait "$MAIN_PID"
rm -f "$PID_FILE" 2>/dev/null
