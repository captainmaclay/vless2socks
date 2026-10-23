#!/bin/bash
# ==============================================================================
# Isolated Claude Desktop GUI Launcher for WSL2 (WSLg)
# Instance: {INSTANCE_ID}
# Network: SOCKS5 127.0.0.1:{SOCKS_PORT} | HTTP 127.0.0.1:{HTTP_PORT}
# Profile: /home/{WSL_USER}/.claude-{INSTANCE_ID}/.config/Claude
# ==============================================================================

# 1. Source system WSL environment
source /etc/profile.d/wsl_hidpi.sh 2>/dev/null
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
GEOMETRY_CONF="$USER_DATA_DIR/window-geometry.conf"
SCALE_CONF="$USER_DATA_DIR/scale-factor.conf"
STATE_JSON="$USER_DATA_DIR/window-state.json"

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

# 6a. Persistent launcher log (stdout/stderr of launcher and Electron), rotated at 1 MB
LAUNCH_LOG_DIR="$HOME/.local/state/claude-gui-{INSTANCE_ID}"
LAUNCH_LOG="$LAUNCH_LOG_DIR/launcher.log"
mkdir -m 700 -p "$LAUNCH_LOG_DIR" 2>/dev/null
if [ -f "$LAUNCH_LOG" ] && [ "$(stat -c %s "$LAUNCH_LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv -f "$LAUNCH_LOG" "$LAUNCH_LOG.1" 2>/dev/null
fi
exec >>"$LAUNCH_LOG" 2>&1
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [launcher $$] $*"
}
log "start: DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY args=$*"
[ -S /tmp/.X11-unix/X0 ] || log "warn: /tmp/.X11-unix/X0 missing (WSLg not ready?)"

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
    eval $(printf "" | gnome-keyring-daemon --daemonize --login --components=secrets 2>/dev/null)
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

focus_existing_instance() {
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
    local win
    win=$(find_instance_win "$pid") || return 1
    log "focusing window $win of running instance (pid $pid)"
    # Minimize + activate forces WSLg RAIL to restore and raise the Windows-side window
    xdotool windowminimize "$win" 2>/dev/null
    sleep 0.3
    xdotool windowactivate "$win" 2>/dev/null
    return 0
}

# 8a. Single-launch lock: only one launcher per instance at a time (held until exit).
# A second click while the first launcher is alive never reaches the cleanup in step 10:
#   - window visible            -> focus it and exit;
#   - window hidden (closed to tray, no tray in WSLg) -> ask the holder to shut down now
#     via REOPEN_FLAG, wait for the lock and start a fresh instance;
#   - holder still starting     -> wait until its window appears (then focus) or the lock frees.
LOCK_FILE="$XDG_RUNTIME_DIR/claude-gui-{INSTANCE_ID}.lock"
REOPEN_FLAG="$XDG_RUNTIME_DIR/claude-gui-{INSTANCE_ID}.reopen"
LOCK_WAIT_STEPS=60   # x 0.5s = 30s
exec 9<>"$LOCK_FILE"  # open without truncating, so a losing launcher keeps the holder's PID
if ! flock -n 9; then
    log "launcher already running (lock busy, holder $(cat "$LOCK_FILE" 2>/dev/null))"
    if focus_existing_instance; then
        exit 0
    fi
    log "no visible window, requesting reopen and waiting for the lock"
    touch "$REOPEN_FLAG"
    GOT_LOCK=false
    for i in $(seq 1 "$LOCK_WAIT_STEPS"); do
        sleep 0.5
        if flock -n 9; then
            GOT_LOCK=true
            break
        fi
        if focus_existing_instance; then
            rm -f "$REOPEN_FLAG" 2>/dev/null
            exit 0
        fi
    done
    if [ "$GOT_LOCK" != "true" ]; then
        log "error: lock still busy after $(( LOCK_WAIT_STEPS / 2 ))s, giving up"
        rm -f "$REOPEN_FLAG" 2>/dev/null
        exit 1
    fi
    log "lock acquired after waiting, starting fresh instance"
fi
rm -f "$REOPEN_FLAG" 2>/dev/null
echo "$$" > "$LOCK_FILE"

# 9. If window is already open, focus it and exit immediately
if [ -f "$PID_FILE" ] && command -v xdotool >/dev/null 2>&1; then
    if focus_existing_instance; then
        exit 0
    fi
fi

# 10. Clean up only this instance's lingering processes and singleton locks
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ]; then
        log "cleaning up stale instance (pid $OLD_PID)"
        pkill -9 -P "$OLD_PID" 2>/dev/null
        kill -9 "$OLD_PID" 2>/dev/null
    fi
    rm -f "$PID_FILE" 2>/dev/null
fi
pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
rm -f "$USER_DATA_DIR/Singleton"* 2>/dev/null

if ! command -v claude-desktop >/dev/null 2>&1; then
    log "error: claude-desktop not found in PATH ($PATH)"
    exit 1
fi

# ==============================================================================
# 11. Scale Factor & Window Geometry Persistence (HiDPI 2K/3K/4K Support)
# ==============================================================================

# Scale factor: default 1.0 (optimal 1:1 logical mapping on WSLg / Weston RDP)
SCALE_FACTOR="1.0"
if [ -n "$CLAUDE_SCALE_FACTOR" ]; then
    SCALE_FACTOR="$CLAUDE_SCALE_FACTOR"
elif [ -f "$SCALE_CONF" ]; then
    CONF_SCALE=$(cat "$SCALE_CONF" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$CONF_SCALE" ]; then
        SCALE_FACTOR="$CONF_SCALE"
    fi
fi

# Determine current display geometry (e.g. 1536 960)
DISP_W=1536
DISP_H=960
if command -v xdotool >/dev/null 2>&1; then
    DISP_GEO=$(xdotool getdisplaygeometry 2>/dev/null)
    if [ -n "$DISP_GEO" ]; then
        DW=$(echo "$DISP_GEO" | awk '{print $1}')
        DH=$(echo "$DISP_GEO" | awk '{print $2}')
        if [ -n "$DW" ] && [ "$DW" -ge 800 ] 2>/dev/null; then
            DISP_W="$DW"
            DISP_H="$DH"
        fi
    fi
fi

# Defaults scaled to current display: ~75% width, ~80% height, centered
DEFAULT_W=$(( DISP_W * 75 / 100 ))
DEFAULT_H=$(( DISP_H * 80 / 100 ))
[ "$DEFAULT_W" -lt 900 ] && DEFAULT_W=900
[ "$DEFAULT_H" -lt 650 ] && DEFAULT_H=650
DEFAULT_X=$(( (DISP_W - DEFAULT_W) / 2 ))
DEFAULT_Y=$(( (DISP_H - DEFAULT_H) / 2 ))
[ "$DEFAULT_X" -lt 0 ] && DEFAULT_X=50
[ "$DEFAULT_Y" -lt 0 ] && DEFAULT_Y=50

TARGET_W="$DEFAULT_W"
TARGET_H="$DEFAULT_H"
TARGET_X="$DEFAULT_X"
TARGET_Y="$DEFAULT_Y"
TARGET_MAXIMIZED=false

# Read saved geometry if available
if [ -f "$GEOMETRY_CONF" ]; then
    SAVED_W=$(grep -E '^WIDTH=' "$GEOMETRY_CONF" 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
    SAVED_H=$(grep -E '^HEIGHT=' "$GEOMETRY_CONF" 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
    SAVED_X=$(grep -E '^X=' "$GEOMETRY_CONF" 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
    SAVED_Y=$(grep -E '^Y=' "$GEOMETRY_CONF" 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
    SAVED_MAX=$(grep -E '^IS_MAXIMIZED=' "$GEOMETRY_CONF" 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')

    if [ -n "$SAVED_W" ] && [ "$SAVED_W" -ge 400 ] 2>/dev/null && [ "$SAVED_W" -le "$DISP_W" ] 2>/dev/null; then
        TARGET_W="$SAVED_W"
    fi
    if [ -n "$SAVED_H" ] && [ "$SAVED_H" -ge 300 ] 2>/dev/null && [ "$SAVED_H" -le "$DISP_H" ] 2>/dev/null; then
        TARGET_H="$SAVED_H"
    fi
    if [ -n "$SAVED_X" ] && [ "$SAVED_X" -ge 0 ] 2>/dev/null && [ "$SAVED_X" -lt "$DISP_W" ] 2>/dev/null; then
        TARGET_X="$SAVED_X"
    fi
    if [ -n "$SAVED_Y" ] && [ "$SAVED_Y" -ge 0 ] 2>/dev/null && [ "$SAVED_Y" -lt "$DISP_H" ] 2>/dev/null; then
        TARGET_Y="$SAVED_Y"
    fi
    if [ "$SAVED_MAX" = "true" ]; then
        TARGET_MAXIMIZED=true
    fi
elif [ -f "$STATE_JSON" ]; then
    # Read from existing window-state.json if geometry.conf not yet present
    SW=$(jq -r '.width // empty' "$STATE_JSON" 2>/dev/null)
    SH=$(jq -r '.height // empty' "$STATE_JSON" 2>/dev/null)
    SX=$(jq -r '.x // empty' "$STATE_JSON" 2>/dev/null)
    SY=$(jq -r '.y // empty' "$STATE_JSON" 2>/dev/null)
    SMAX=$(jq -r '.isMaximized // false' "$STATE_JSON" 2>/dev/null)

    # Only accept if width/height are reasonable (ignore stale microscopic 411x312 from 2x scale)
    if [ -n "$SW" ] && [ "$SW" -ge 600 ] 2>/dev/null && [ "$SW" -le "$DISP_W" ] 2>/dev/null; then
        TARGET_W="$SW"
    fi
    if [ -n "$SH" ] && [ "$SH" -ge 450 ] 2>/dev/null && [ "$SH" -le "$DISP_H" ] 2>/dev/null; then
        TARGET_H="$SH"
    fi
    if [ -n "$SX" ] && [ "$SX" -ge 0 ] 2>/dev/null && [ "$SX" -lt "$DISP_W" ] 2>/dev/null; then
        TARGET_X="$SX"
    fi
    if [ -n "$SY" ] && [ "$SY" -ge 0 ] 2>/dev/null && [ "$SY" -lt "$DISP_H" ] 2>/dev/null; then
        TARGET_Y="$SY"
    fi
    if [ "$SMAX" = "true" ]; then
        TARGET_MAXIMIZED=true
    fi
fi

# Pre-populate window-state.json so Electron initializes with exact target geometry and displayBounds
cat << EOF > "$STATE_JSON.tmp"
{
  "x": $TARGET_X,
  "y": $TARGET_Y,
  "width": $TARGET_W,
  "height": $TARGET_H,
  "isMaximized": $TARGET_MAXIMIZED,
  "isFullScreen": false,
  "displayBounds": {
    "x": 0,
    "y": 0,
    "width": $DISP_W,
    "height": $DISP_H
  }
}
EOF
mv -f "$STATE_JSON.tmp" "$STATE_JSON" 2>/dev/null

# Also ensure geometry.conf matches
cat << EOF > "$GEOMETRY_CONF.tmp"
WIDTH=$TARGET_W
HEIGHT=$TARGET_H
X=$TARGET_X
Y=$TARGET_Y
IS_MAXIMIZED=$TARGET_MAXIMIZED
EOF
mv -f "$GEOMETRY_CONF.tmp" "$GEOMETRY_CONF" 2>/dev/null

# WSLg falls back to "[WARN:COPY MODE]" when its shared memory is broken (distro restarted
# inside a running VM); windows are then created but never painted on the Windows side.
# Only `wsl --shutdown` from Windows fixes it (the VBS launcher does that), here we just log it.
if grep -q 'rdp_allocate_shared_memory: Failed' /mnt/wslg/weston.log 2>/dev/null; then
    log "warn: WSLg is in COPY MODE (shared memory unavailable), windows may stay blank; run 'wsl --shutdown'"
fi

# ==============================================================================
# 12. Launch Claude Desktop with HiDPI scaling, Wayland support, and basic password store
# ==============================================================================
start_instance() {
    claude-desktop --no-sandbox \
                   --ozone-platform-hint=auto \
                   --enable-features=WaylandWindowDecorations \
                   --force-device-scale-factor="$SCALE_FACTOR" \
                   --password-store=basic \
                   --proxy-server="socks5://127.0.0.1:{SOCKS_PORT}" \
                   --user-data-dir="$USER_DATA_DIR" "$@" 9>&- &
    MAIN_PID=$!
    echo "$MAIN_PID" > "$PID_FILE"
    log "claude-desktop started (attempt $ATTEMPT/$MAX_ATTEMPTS, pid $MAIN_PID, scale $SCALE_FACTOR, geometry ${TARGET_W}x${TARGET_H}+${TARGET_X}+${TARGET_Y})"
}

stop_instance() {
    pkill -9 -P "$MAIN_PID" 2>/dev/null
    kill -9 "$MAIN_PID" 2>/dev/null
    wait "$MAIN_PID" 2>/dev/null
    pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
    pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
    rm -f "$USER_DATA_DIR/Singleton"* "$PID_FILE" 2>/dev/null
}

# ==============================================================================
# 13. Window Discovery (with launch retries), Geometry Enforcement & Persistence Loop
# ==============================================================================
MAX_ATTEMPTS=4
RETRY_DELAY=1.5
WINDOW_WAIT_STEPS=40   # x 0.5s = 20s per attempt

if ! command -v xdotool >/dev/null 2>&1; then
    ATTEMPT=1
    start_instance "$@"
else
    WIN_ID=""
    for ATTEMPT in $(seq 1 "$MAX_ATTEMPTS"); do
        start_instance "$@"
        for i in $(seq 1 "$WINDOW_WAIT_STEPS"); do
            if ! kill -0 "$MAIN_PID" 2>/dev/null; then
                wait "$MAIN_PID" 2>/dev/null
                log "attempt $ATTEMPT: claude-desktop exited before showing a window (exit code $?)"
                break
            fi
            WIN_ID=$(find_instance_win "$MAIN_PID")
            if [ -n "$WIN_ID" ]; then
                break
            fi
            sleep 0.5
        done
        [ -n "$WIN_ID" ] && break

        if kill -0 "$MAIN_PID" 2>/dev/null; then
            log "attempt $ATTEMPT: no visible window after $(( WINDOW_WAIT_STEPS / 2 ))s, killing"
        fi
        stop_instance
        if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
            sleep "$RETRY_DELAY"
        fi
    done

    if [ -z "$WIN_ID" ]; then
        log "error: no window after $MAX_ATTEMPTS attempts, giving up"
        exit 1
    fi

    if [ -n "$WIN_ID" ]; then
        log "window found: $WIN_ID"
        # Check initial window geometry; enforce saved size/pos if Electron opened differently
        sleep 0.5
        eval $(xdotool getwindowgeometry --shell "$WIN_ID" 2>/dev/null)
        if [ "$TARGET_MAXIMIZED" = "true" ]; then
            if command -v xprop >/dev/null 2>&1; then
                IS_MAX=$(xprop -id "$WIN_ID" _NET_WM_STATE 2>/dev/null | grep -E '_NET_WM_STATE_MAXIMIZED_HORZ|_NET_WM_STATE_MAXIMIZED_VERT')
                if [ -z "$IS_MAX" ]; then
                    xdotool windowsize "$WIN_ID" "$DISP_W" "$DISP_H" 2>/dev/null
                fi
            fi
        else
            if [ -n "$WIDTH" ] && [ -n "$HEIGHT" ]; then
                DIFF_W=$(( WIDTH > TARGET_W ? WIDTH - TARGET_W : TARGET_W - WIDTH ))
                DIFF_H=$(( HEIGHT > TARGET_H ? HEIGHT - TARGET_H : TARGET_H - HEIGHT ))
                if [ "$DIFF_W" -gt 60 ] || [ "$DIFF_H" -gt 60 ]; then
                    xdotool windowsize "$WIN_ID" "$TARGET_W" "$TARGET_H" 2>/dev/null
                    xdotool windowmove "$WIN_ID" "$TARGET_X" "$TARGET_Y" 2>/dev/null
                fi
            fi
        fi

        LAST_SAVED_W="$TARGET_W"
        LAST_SAVED_H="$TARGET_H"
        LAST_SAVED_X="$TARGET_X"
        LAST_SAVED_Y="$TARGET_Y"
        LAST_SAVED_MAX="$TARGET_MAXIMIZED"

        save_geometry() {
            local w="$1"
            local h="$2"
            local x="$3"
            local y="$4"
            local max="$5"
            cat << EOF > "$GEOMETRY_CONF.tmp"
WIDTH=$w
HEIGHT=$h
X=$x
Y=$y
IS_MAXIMIZED=$max
EOF
            mv -f "$GEOMETRY_CONF.tmp" "$GEOMETRY_CONF" 2>/dev/null

            cat << EOF > "$STATE_JSON.tmp"
{
  "x": $x,
  "y": $y,
  "width": $w,
  "height": $h,
  "isMaximized": $max,
  "isFullScreen": false,
  "displayBounds": {
    "x": 0,
    "y": 0,
    "width": $DISP_W,
    "height": $DISP_H
  }
}
EOF
            mv -f "$STATE_JSON.tmp" "$STATE_JSON" 2>/dev/null
        }

        # Tolerate transient window loss (reload, re-map, compositor hiccup):
        # shut down only after MAX_MISSES consecutive checks without a visible window (~9s)
        MAX_MISSES=6
        MISSES=0
        while kill -0 "$MAIN_PID" 2>/dev/null; do
            sleep 1.5
            CURRENT_WIN=$(find_instance_win "$MAIN_PID")
            if [ -z "$CURRENT_WIN" ]; then
                MISSES=$(( MISSES + 1 ))
                [ "$MISSES" -eq 1 ] && log "window not visible, waiting (up to $MAX_MISSES checks)"
                if [ "$MISSES" -ge "$MAX_MISSES" ]; then
                    log "window gone for $MISSES consecutive checks, shutting down instance"
                    break
                fi
                # A new launcher is waiting (user clicked the shortcut): don't wait out the grace period
                if [ -f "$REOPEN_FLAG" ]; then
                    log "window hidden and reopen requested, shutting down instance for relaunch"
                    break
                fi
                continue
            fi
            if [ "$MISSES" -gt 0 ]; then
                log "window visible again after $MISSES missed checks: $CURRENT_WIN"
                MISSES=0
            fi

            IS_MAX_NOW=false
            if command -v xprop >/dev/null 2>&1; then
                if xprop -id "$CURRENT_WIN" _NET_WM_STATE 2>/dev/null | grep -q 'MAXIMIZED'; then
                    IS_MAX_NOW=true
                fi
            fi

            unset WIDTH HEIGHT X Y
            eval $(xdotool getwindowgeometry --shell "$CURRENT_WIN" 2>/dev/null)
            if [ -n "$WIDTH" ] && [ "$WIDTH" -ge 400 ] 2>/dev/null && [ -n "$HEIGHT" ] && [ "$HEIGHT" -ge 300 ] 2>/dev/null; then
                if [ "$IS_MAX_NOW" = "true" ]; then
                    if [ "$LAST_SAVED_MAX" != "true" ]; then
                        LAST_SAVED_MAX=true
                        save_geometry "$LAST_SAVED_W" "$LAST_SAVED_H" "$LAST_SAVED_X" "$LAST_SAVED_Y" "true"
                    fi
                else
                    if [ "$WIDTH" != "$LAST_SAVED_W" ] || [ "$HEIGHT" != "$LAST_SAVED_H" ] || \
                       [ "$X" != "$LAST_SAVED_X" ] || [ "$Y" != "$LAST_SAVED_Y" ] || [ "$LAST_SAVED_MAX" = "true" ]; then
                        LAST_SAVED_W="$WIDTH"
                        LAST_SAVED_H="$HEIGHT"
                        LAST_SAVED_X="$X"
                        LAST_SAVED_Y="$Y"
                        LAST_SAVED_MAX=false
                        save_geometry "$WIDTH" "$HEIGHT" "$X" "$Y" "false"
                    fi
                fi
            fi
        done

        # Clean shutdown of lingering background processes of THIS instance
        kill -TERM "$MAIN_PID" 2>/dev/null
        sleep 1
        pkill -9 -P "$MAIN_PID" 2>/dev/null
        kill -9 "$MAIN_PID" 2>/dev/null
        pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
        pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
        rm -f "$USER_DATA_DIR/Singleton"* 2>/dev/null
        rm -f "$PID_FILE" 2>/dev/null
        log "instance stopped"
        exit 0
    fi
fi

wait "$MAIN_PID"
log "claude-desktop exited (exit code $?)"
rm -f "$PID_FILE" 2>/dev/null
