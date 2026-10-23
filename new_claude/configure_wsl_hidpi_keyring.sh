#!/bin/bash
# ==============================================================================
# WSL2 / WSLg Configuration: Eliminate Keyring Popups & Enable High-DPI Scaling
# ==============================================================================
set -e

echo "[*] Step 1: Eliminating GNOME Keyring lockouts and prompt dialogs..."

# Kill any lingering gnome-keyring-daemon
pkill -9 -f gnome-keyring 2>/dev/null || true

# Clean up any encrypted binary keyrings
for udir in /home/ubuntu /home/ubuntu/.claude-1 /home/ubuntu/.claude-2; do
    if [ -d "$udir" ]; then
        kdir="$udir/.local/share/keyrings"
        rm -rf "$kdir"
        mkdir -m 700 -p "$kdir"
        cat << 'EOF' > "$kdir/login.keyring"
[keyring]
display-name=Login
ctime=1695477404
mtime=0
lock-on-idle=false
lock-after=false
EOF
        chmod 600 "$kdir/login.keyring"
        echo "login" > "$kdir/default"
        chmod 644 "$kdir/default"
        chown -R ubuntu:ubuntu "$udir/.local" 2>/dev/null || true
    fi
done

echo "[*] Step 2: Configuring High-DPI & Wayland environment in /etc/profile.d/wsl_hidpi.sh..."

# Weston already scales the RDP output 2x (Windows 200% -> logical 1536x960 grid), so toolkits must
# render at 1x; GDK_SCALE=2 / QT_SCALE_FACTOR=2 on top of that gives 400% (double scaling bug).
cat << 'EOF' > /etc/profile.d/wsl_hidpi.sh
# WSLg High-DPI and Wayland vector scaling configuration
export ELECTRON_OZONE_PLATFORM_HINT=auto
export GDK_SCALE=1
export GDK_DPI_SCALE=1
export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_SCALE_FACTOR=1
export XCURSOR_SIZE=24
EOF
chmod 644 /etc/profile.d/wsl_hidpi.sh

echo "[*] Step 3: Setting up desktop scaling parameters in WSL..."
# Set xrdb Xft.dpi if xrdb is available (96 = 1x on the already 2x-scaled Weston output)
if command -v xrdb >/dev/null 2>&1; then
    echo "Xft.dpi: 96" | xrdb -merge 2>/dev/null || true
fi

echo "[SUCCESS] WSL Keyring unlocked and High-DPI profile installed."
