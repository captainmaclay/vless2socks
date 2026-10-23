#!/bin/bash
# ==============================================================================
# WSLg render health check (called by run_claude_gui_*.vbs before launch)
# Exit codes:
#   0  - WSLg OK (shared memory available)
#   10 - WSLg in COPY MODE, no Claude GUI running -> safe to `wsl --shutdown`
#   11 - WSLg in COPY MODE, but Claude GUI processes are running -> leave as is
# ==============================================================================
WESTON_LOG=/mnt/wslg/weston.log

# Weston decides on shared memory before enabling the RDP output; wait for that marker
for i in $(seq 1 16); do
    grep -q "Output 'rdp-0' enabled" "$WESTON_LOG" 2>/dev/null && break
    sleep 0.5
done

# Note: "enable_copy_warning_title = 1" is only a setting and is logged in healthy sessions too
if ! grep -q 'rdp_allocate_shared_memory: Failed' "$WESTON_LOG" 2>/dev/null; then
    exit 0
fi

if pgrep -f '/usr/lib/claude-desktop/claude-desktop' >/dev/null 2>&1; then
    exit 11
fi
exit 10
