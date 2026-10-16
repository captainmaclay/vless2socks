#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automation tool to create completely isolated Vendor C Code CLI test environments on Windows
with custom SOCKS5/HTTP proxy routing, dedicated configuration directories,
Windows Credential Manager token separation, and Desktop shortcuts.

Разработано строго в исследовательских и образовательных целях
для изучения изоляции процессов, разделения хранилищ токенов и предотвращения утечек сетевого трафика.

Usage:
    python add_vendor_c_cli_instance.py --id 3 --port 1020 --color purple --name "Vendor C Socket 1020 (Profile 3)"
    python add_vendor_c_cli_instance.py --interactive
"""

import argparse
import colorsys
import os
import shutil
import subprocess
import sys
from PIL import Image

COLOR_HUES = {
    "gold": 0.12,
    "orange": 0.07,
    "purple": 0.77,
    "blue": 0.60,
    "cyan": 0.50,
    "green": 0.33,
    "red": 0.00,
    "pink": 0.88,
    "black": 0.0,
}

PORT_THEMES = {
    1015: ("gold", "Vendor C Socket 1015"),
    1016: ("gold", "Vendor C Socket 1016 (Secondary)"),
    1020: ("purple", "Vendor C Socket 1020 (Profile 3)"),
    1021: ("orange", "Vendor C Socket 1021 (Profile 4)"),
    1030: ("blue", "Vendor C Socket 1030"),
    1081: ("black", "Vendor C Socket 1081"),
    1082: ("green", "Vendor C Socket 1082"),
    1083: ("red", "Vendor C Socket 1083"),
    1084: ("cyan", "Vendor C Socket 1084"),
    1085: ("pink", "Vendor C Socket 1085"),
}

def generate_colored_ico(base_ico_path, out_ico_path, color_name):
    """Generates a multi-resolution colored icon from base ico using HSV transformation."""
    if not os.path.exists(base_ico_path):
        raise FileNotFoundError(f"Base icon not found: {base_ico_path}")

    color_lower = color_name.lower()
    target_hue = COLOR_HUES.get(color_lower, 0.12)

    img = Image.open(base_ico_path).convert("RGBA")
    pixels = img.load()
    w, h = img.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 10:
                continue
            nr, ng, nb = r / 255.0, g / 255.0, b / 255.0
            h_val, s_val, v_val = colorsys.rgb_to_hsv(nr, ng, nb)

            if s_val < 0.12:
                continue

            if color_lower == "black":
                new_s = 0.0
                new_v = v_val * 0.35
                new_r, new_g, new_b = new_v, new_v, new_v
            else:
                new_r, new_g, new_b = colorsys.hsv_to_rgb(target_hue, s_val, v_val)

            pixels[x, y] = (int(new_r * 255), int(new_g * 255), int(new_b * 255), a)

    os.makedirs(os.path.dirname(out_ico_path), exist_ok=True)
    img.save(out_ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"[+] Generated colored icon: {out_ico_path}")


def deploy_cli_bat(instance_id, socks_port, config_dir_name, work_dir=None):
    """Generates isolated Vendor C Code CLI .bat launcher in C:\\MyFiles\\Proxy."""
    proxy_dir = r"C:\MyFiles\Proxy"
    os.makedirs(proxy_dir, exist_ok=True)

    bat_filename = f"Vendor_C_Socket_{socks_port}.bat"
    bat_path = os.path.join(proxy_dir, bat_filename)
    legacy_bat_path = os.path.join(proxy_dir, f"Claude_Socket_{socks_port}.bat")

    user_home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cli_exe = os.path.join(user_home, "AppData", "Roaming", "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
    if not work_dir:
        work_dir = os.path.join(user_home, "Downloads", f"Vendor_C_{socks_port}")
        # fallback to existing folder if already created
        legacy_work_dir = os.path.join(user_home, "Downloads", f"Claude_{socks_port}")
        if not os.path.exists(work_dir) and os.path.exists(legacy_work_dir):
            work_dir = legacy_work_dir
    os.makedirs(work_dir, exist_ok=True)
    config_dir_path = os.path.join(user_home, config_dir_name)

    content = f"""@echo off
chcp 65001 >nul
title Vendor C Code (Instance {instance_id} - Socket {socks_port})

REM 1. Pre-launch IP Verification with Mac UI floating notification (Research Sandbox)
python "C:\\MyFiles\\vless2socks\\new_claude\\verify_and_notify.py" --port {socks_port} --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] IP verification failed. Launch aborted to prevent leak!
    timeout /t 3 >nul
    exit /b 1
)

REM 2. Vendor C Code CLI strictly via socket 127.0.0.1:{socks_port}
set HTTP_PROXY=http://127.0.0.1:{socks_port}
set HTTPS_PROXY=http://127.0.0.1:{socks_port}
set ALL_PROXY=http://127.0.0.1:{socks_port}
set http_proxy=http://127.0.0.1:{socks_port}
set https_proxy=http://127.0.0.1:{socks_port}
set all_proxy=http://127.0.0.1:{socks_port}
set NO_PROXY=

REM Isolated configuration directory for profile {instance_id}
set CLAUDE_CONFIG_DIR={config_dir_path}
set CLAUDE_SECURESTORAGE_CONFIG_DIR={config_dir_path}
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 3. Synchronize Vendor C profile metadata with vless2socks
python "C:\\MyFiles\\vless2socks\\vendor_c_sync.py" --port {socks_port} --quiet

cd /d "{work_dir}"
"{cli_exe}" --dangerously-skip-permissions %*

REM 4. Update status on exit in case session state changed
python "C:\\MyFiles\\vless2socks\\vendor_c_sync.py" --port {socks_port} --quiet
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] Created CLI batch file: {bat_path}")

    # For backward compatibility, also update/create legacy bat path
    try:
        with open(legacy_bat_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[+] Updated legacy CLI batch file for compatibility: {legacy_bat_path}")
    except Exception as e:
        print(f"[*] Note on legacy bat: {e}")

    # Ensure profile directory exists
    os.makedirs(config_dir_path, exist_ok=True)
    return bat_path


def create_cli_shortcut(shortcut_name, bat_path, icon_path):
    """Creates Desktop & Proxy/ink shortcuts for Vendor C CLI."""
    desktop_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")
    proxy_ink_dir = r"C:\MyFiles\Proxy\ink"
    work_dir = os.path.dirname(bat_path)
    cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")

    targets = [
        os.path.join(desktop_dir, f"{shortcut_name}.lnk"),
        os.path.join(proxy_ink_dir, f"{shortcut_name}.lnk")
    ]

    ps_code = f"""
$wsh = New-Object -ComObject WScript.Shell
$cmd = '{cmd_exe}'
$args = '/k "{bat_path}"'
$work = '{work_dir}'
$ico = '{icon_path},0'

$targets = @(
    '{targets[0]}',
    '{targets[1]}'
)

foreach ($t in $targets) {{
    $dir = Split-Path $t
    if (!(Test-Path $dir)) {{ New-Item -ItemType Directory -Path $dir -Force | Out-Null }}
    $lnk = $wsh.CreateShortcut($t)
    $lnk.TargetPath = $cmd
    $lnk.Arguments = $args
    $lnk.WorkingDirectory = $work
    $lnk.IconLocation = $ico
    $lnk.Description = 'Vendor C Code CLI ({shortcut_name})'
    $lnk.Save()
    Write-Host "[OK] Created shortcut: $t"
}}

try {{
    Start-Process "ie4uinit.exe" -ArgumentList "-show" -Wait -NoNewWindow -ErrorAction SilentlyContinue
}} catch {{}}
"""

    temp_ps1 = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "create_cli_sc.ps1")
    with open(temp_ps1, "w", encoding="utf-8-sig") as f:
        f.write(ps_code)

    subprocess.check_call(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", temp_ps1])
    if os.path.exists(temp_ps1):
        os.remove(temp_ps1)


def main():
    parser = argparse.ArgumentParser(description="Add an isolated Vendor C Code CLI instance on Windows (Research Sandbox).")
    parser.add_argument("--id", help="Instance ID or suffix (e.g. 3, 4, tertiary, 1020)")
    parser.add_argument("--port", type=int, help="SOCKS5 port (e.g. 1020, 1030, 1081)")
    parser.add_argument("--name", help="Display name for Desktop shortcut")
    parser.add_argument("--color", help="Icon color theme (gold, purple, blue, cyan, green, red, orange, pink, black)")
    parser.add_argument("--interactive", action="store_true", help="Prompt for parameters interactively")

    args = parser.parse_args()

    if args.interactive or not args.id or not args.port:
        print("=== Interactive Setup: New Isolated Vendor C Code CLI (Research Environment) ===")
        if not args.id:
            args.id = input("Enter Instance ID (e.g., 3, 4, 1020): ").strip()
        if not args.port:
            port_str = input("Enter SOCKS5 port (e.g., 1020, 1030, 1081): ").strip()
            args.port = int(port_str)

    instance_id = str(args.id).strip()
    socks_port = int(args.port)

    default_color, default_label = PORT_THEMES.get(socks_port, ("purple", f"Vendor C Socket {socks_port} (Profile {instance_id})"))
    color = (args.color or default_color).lower()
    shortcut_name = args.name or f"CLI_{socks_port}"

    base_ico = r"C:\MyFiles\Proxy\ico\claude.ico"
    out_ico = os.path.join(r"C:\MyFiles\Proxy\ico", f"claude_{color}.ico")

    if not os.path.exists(out_ico):
        generate_colored_ico(base_ico, out_ico, color)
    else:
        print(f"[*] Icon already exists: {out_ico}")

    config_dir_name = f".claude-{instance_id}" if instance_id not in ("2", "secondary") else ".claude-secondary"
    bat_path = deploy_cli_bat(instance_id, socks_port, config_dir_name)
    create_cli_shortcut(shortcut_name, bat_path, out_ico)

    print("\n[SUCCESS] Vendor C CLI Setup complete!")
    print(f"  - Instance ID: {instance_id}")
    print(f"  - SOCKS5 Port: {socks_port}")
    print(f"  - Config Directory: {config_dir_path}")
    print(f"  - Batch Launcher: {bat_path}")
    print(f"  - Desktop Shortcut: {shortcut_name}.lnk")


if __name__ == "__main__":
    main()
