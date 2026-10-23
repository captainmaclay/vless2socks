#!/usr/bin/env python3
"""
Update Claude Desktop GUI launchers and templates in WSL with GNOME Keyring Secret Service integration.
"""
import os
import subprocess

PRIMARY_LAUNCHER = """#!/bin/bash
source /etc/profile.d/herdr_claude_env.sh 2>/dev/null

export DISPLAY=${DISPLAY:-:0}
USER_UID=$(id -u)
USER_GID=$(id -g)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$USER_UID}
export USER_DATA_DIR="$HOME/.config/Claude"
PID_FILE="$XDG_RUNTIME_DIR/claude-gui-primary.pid"

# Ensure /run/user/<uid> directory exists
if [ ! -d "/run/user/$USER_UID" ]; then
    mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null || sudo mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null
    sudo chown "$USER_UID:$USER_GID" "/run/user/$USER_UID" 2>/dev/null
fi

# Ensure user DBus session is running for single-instance IPC
if [ ! -e "/run/user/$USER_UID/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
    dbus-daemon --session --fork --address="unix:path=/run/user/$USER_UID/bus" 2>/dev/null
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus"

# Ensure default passwordless keyring exists
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

# Auto-start and unlock gnome-keyring-daemon if not running
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

# 1. If an actual visible Primary Claude window is currently open, activate/focus it and exit
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

# 2. No visible window exists: terminate any lingering Primary processes and clear locks
# Note: ONLY terminate Primary instance processes, NEVER touch Secondary instance!
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
    echo "claude-desktop не найден в PATH. Убедитесь, что deb-пакет установлен." >&2
    exit 1
fi

# 3. Launch Primary claude-desktop in background with secure GNOME libsecret keyring
claude-desktop --no-sandbox --ozone-platform-hint=auto --enable-features=WaylandWindowDecorations --force-device-scale-factor=2 --password-store=basic --user-data-dir="$USER_DATA_DIR" "$@" &
MAIN_PID=$!
echo "$MAIN_PID" > "$PID_FILE"

# 4. Wait for window to appear (up to 15 seconds)
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

    # 5. Monitor the window: when the window closes, cleanly terminate background processes
    if [ -n "$WIN_ID" ]; then
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep 2
            CURRENT_WIN=$(find_instance_win "$MAIN_PID")
            if [ -z "$CURRENT_WIN" ]; then
                # User closed the window: terminate background instance so it doesn't linger
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

# Fallback
wait "$MAIN_PID"
rm -f "$PID_FILE" 2>/dev/null
"""

SECONDARY_LAUNCHER = """#!/bin/bash
source /etc/profile.d/herdr_claude_env.sh 2>/dev/null

# Override proxy strictly for Secondary Claude GUI to Socket 1016 (SOCKS5 1016, HTTP 11016)
# while preserving global WSL base proxy 1015
export HTTPS_PROXY='http://127.0.0.1:11016'
export HTTP_PROXY='http://127.0.0.1:11016'
export ALL_PROXY='socks5h://127.0.0.1:1016'
export https_proxy='http://127.0.0.1:11016'
export http_proxy='http://127.0.0.1:11016'
export all_proxy='socks5h://127.0.0.1:1016'

export DISPLAY=${DISPLAY:-:0}
USER_UID=$(id -u)
USER_GID=$(id -g)
export HOME="/home/79251/.claude-secondary"
export XDG_CONFIG_HOME="$HOME/.config"
export USER_DATA_DIR="$HOME/.config/Claude"
export XDG_RUNTIME_DIR="/run/user/$USER_UID/claude-2"
PID_FILE="$XDG_RUNTIME_DIR/claude-gui-secondary.pid"

# Ensure /run/user/<uid> base directory exists
if [ ! -d "/run/user/$USER_UID" ]; then
    mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null || sudo mkdir -m 700 -p "/run/user/$USER_UID" 2>/dev/null
    sudo chown "$USER_UID:$USER_GID" "/run/user/$USER_UID" 2>/dev/null
fi

# Ensure user DBus session is running on the primary user bus
if [ ! -e "/run/user/$USER_UID/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
    dbus-daemon --session --fork --address="unix:path=/run/user/$USER_UID/bus" 2>/dev/null
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus"

# Ensure isolated home, config, and runtime directories exist
mkdir -m 700 -p "$HOME" "$XDG_CONFIG_HOME" "$USER_DATA_DIR" "$XDG_RUNTIME_DIR" 2>/dev/null

# Ensure keyring directories and default login keyring exist in secondary home
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

# Auto-start and unlock gnome-keyring-daemon if not already running
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

# 1. If an actual visible Secondary Claude window is currently open, activate/focus it and exit
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

# 2. No visible window exists: terminate any lingering Secondary processes and clear locks
# Note: ONLY terminate Secondary instance processes, NEVER touch Primary instance!
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
    echo "claude-desktop не найден в PATH. Убедитесь, что deb-пакет установлен." >&2
    exit 1
fi

# 3. Launch Secondary claude-desktop in background routed strictly via Socket 1016 with GNOME libsecret
claude-desktop --no-sandbox --ozone-platform-hint=auto --enable-features=WaylandWindowDecorations --force-device-scale-factor=2 --password-store=basic --proxy-server="socks5://127.0.0.1:1016" --user-data-dir="$USER_DATA_DIR" "$@" &
MAIN_PID=$!
echo "$MAIN_PID" > "$PID_FILE"

# 4. Wait for window to appear (up to 15 seconds)
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

    # 5. Monitor the window: when the window closes, cleanly terminate background processes
    if [ -n "$WIN_ID" ]; then
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep 2
            CURRENT_WIN=$(find_instance_win "$MAIN_PID")
            if [ -z "$CURRENT_WIN" ]; then
                # User closed the window: terminate background instance so it doesn't linger
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

# Fallback
wait "$MAIN_PID"
rm -f "$PID_FILE" 2>/dev/null
"""

def main():
    # Write temporary files and move via sudo in WSL
    temp_dir = r"\\wsl.localhost\Ubuntu\tmp"
    with open(os.path.join(temp_dir, "claude-gui.new"), "w", newline="\n") as f:
        f.write(PRIMARY_LAUNCHER)
    with open(os.path.join(temp_dir, "claude-gui-2.new"), "w", newline="\n") as f:
        f.write(SECONDARY_LAUNCHER)

    # Move to /usr/local/bin via sudo
    cmd = (
        "sudo mv /tmp/claude-gui.new /usr/local/bin/claude-gui && "
        "sudo chmod 755 /usr/local/bin/claude-gui && "
        "sudo mv /tmp/claude-gui-2.new /usr/local/bin/claude-gui-2 && "
        "sudo chmod 755 /usr/local/bin/claude-gui-2"
    )
    res = subprocess.run(["wsl.exe", "-d", "Ubuntu", "bash", "-c", cmd], capture_output=True, text=True)
    if res.returncode != 0:
        print("Error updating launchers:", res.stderr)
        exit(1)
    print("Successfully updated /usr/local/bin/claude-gui and /usr/local/bin/claude-gui-2")

if __name__ == "__main__":
    main()
