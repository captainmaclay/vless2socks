#!/usr/bin/env python3
"""
Update Claude Desktop GUI launchers and templates in WSL with HiDPI support and geometry persistence.

Each instance is re-rendered from template_claude_gui.sh / template_run_gui.vbs (lockfile, launch retries,
reopen handling, launcher log) and /usr/local/bin/wslg-health is (re)installed from wslg_health.sh.
"""
import os
import subprocess
import sys

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    distro = "Ubuntu-24.04"

    # Re-deploy instances 1, 2, and 3 using the main deployment script
    deploy_script = os.path.join(script_dir, "add_vendor_c_gui_instance.py")
    cmd_1 = [sys.executable, deploy_script, "--id", "1", "--port", "1015", "--color", "terracotta", "--name", "Claude GUI", "--distro", distro]
    cmd_2 = [sys.executable, deploy_script, "--id", "2", "--port", "1016", "--color", "gold", "--name", "Claude Code GUI 2", "--distro", distro]
    cmd_3 = [sys.executable, deploy_script, "--id", "3", "--port", "1017", "--color", "gold", "--name", "Claude Code GUI 3", "--distro", distro]

    print("[*] Re-deploying Claude GUI Instance 1 (1015)...")
    subprocess.check_call(cmd_1)

    print("[*] Re-deploying Claude GUI Instance 2 (1016)...")
    subprocess.check_call(cmd_2)

    print("[*] Re-deploying Claude GUI Instance 3 (1017)...")
    subprocess.check_call(cmd_3)

    print("[SUCCESS] All launchers updated: HiDPI, window persistence, lockfile, launch retries and WSLg health check.")

if __name__ == "__main__":
    main()
