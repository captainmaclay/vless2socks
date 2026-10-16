#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automation tool to create completely isolated Vendor C Desktop GUI instances in WSL2 (WSLg)
with custom SOCKS5/HTTP proxy routing, dedicated DBus session, PID tracking,
and Windows Desktop / Proxy shortcut integration.

Разработано исключительно в исследовательских и образовательных целях
для тестирования изоляции окружений, разделения сессионных шин DBus, Secret Service и сокетов.

Usage:
    python add_vendor_c_gui_instance.py --id 3 --port 1020 --color purple --name "Vendor C Desktop (Instance 3)"
    python add_vendor_c_gui_instance.py --interactive
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
    1015: ("gold", "System Proxy 1015"),
    1016: ("gold", "Vendor C Secondary 1016"),
    1020: ("purple", "Russia-0.1 1020"),
    1021: ("orange", "02-Russia 1021"),
    1030: ("blue", "WorProxy 1030"),
    1081: ("black", "FI FINLAND 3 1081"),
    1082: ("green", "FI FINLAND 4 1082"),
    1083: ("red", "FR FRANCE 1 1083"),
    1084: ("cyan", "FI FINLAND 2 1084"),
    1085: ("pink", "Latvia 1085"),
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


def deploy_wsl_launcher(distro, instance_id, socks_port, http_port, wsl_user="79251", uid=1000):
    """Installs isolated GUI launcher inside WSL /usr/local/bin."""
    template_path = os.path.join(os.path.dirname(__file__), "template_claude_gui.sh")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    rendered = (
        template.replace("{INSTANCE_ID}", str(instance_id))
        .replace("{SOCKS_PORT}", str(socks_port))
        .replace("{HTTP_PORT}", str(http_port))
        .replace("{WSL_USER}", str(wsl_user))
        .replace("{UID}", str(uid))
    )

    temp_script_path = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), f"claude-gui-{instance_id}.sh")
    with open(temp_script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)

    wsl_temp_path = subprocess.check_output(
        ["wsl.exe", "-d", distro, "wslpath", "-u", temp_script_path.replace("\\", "/")],
        text=True
    ).strip()

    target_bin = f"/usr/local/bin/claude-gui-{instance_id}"
    cmd = (
        f"sudo tr -d '\\r' < {wsl_temp_path} > {target_bin} && "
        f"sudo chmod +x {target_bin} && "
        f"mkdir -m 700 -p /home/{wsl_user}/.claude-{instance_id}/.config /run/user/{uid}/claude-{instance_id} 2>/dev/null && "
        f"if [ -f /usr/local/bin/init-wsl-keyring.sh ]; then /usr/local/bin/init-wsl-keyring.sh /home/{wsl_user}/.claude-{instance_id} 2>/dev/null || true; fi"
    )

    subprocess.check_call(["wsl.exe", "-d", distro, "sudo", "bash", "-c", cmd])
    print(f"[+] Installed WSL launcher: {target_bin}")

    if os.path.exists(temp_script_path):
        os.remove(temp_script_path)


def deploy_windows_launchers(distro, instance_id, socks_port, shortcut_name, icon_path):
    """Creates VBS silent runner with pre-launch IP verification, batch file, and Windows shortcuts."""
    herdr_dir = r"C:\MyFiles\herdrCenter"
    proxy_dir = r"C:\MyFiles\Proxy"
    os.makedirs(herdr_dir, exist_ok=True)
    os.makedirs(proxy_dir, exist_ok=True)

    # 1. Create VBS Silent Runner with Mac UI IP pre-verification
    template_path = os.path.join(os.path.dirname(__file__), "template_run_gui.vbs")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
        vbs_content = template.format(
            INSTANCE_ID=instance_id,
            SOCKS_PORT=socks_port,
            DISTRO=distro
        )
    else:
        vbs_content = (
            'Set WshShell = CreateObject("WScript.Shell")\n'
            f'ret = WshShell.Run("pythonw ""C:\\MyFiles\\vless2socks\\new_claude\\verify_and_notify.py"" --port {socks_port} --app ""Vendor C GUI""", 0, True)\n'
            'If ret = 0 Then\n'
            f'    WshShell.Run "wsl.exe -d {distro} bash -lc ""/usr/local/bin/claude-gui-{instance_id}""", 0, False\n'
            'End If\n'
        )

    vbs_path = os.path.join(herdr_dir, f"run_claude_gui_{instance_id}.vbs")
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
    print(f"[+] Created VBS launcher: {vbs_path}")

    # 2. Create Batch Launcher (both Vendor_C_GUI and Claude_GUI for backward compatibility)
    for bat_name in [f"Vendor_C_GUI_{instance_id}.bat", f"Claude_GUI_{instance_id}.bat"]:
        bat_path = os.path.join(proxy_dir, bat_name)
        bat_content = (
            "@echo off\n"
            "chcp 65001 >nul\n"
            f'REM Silent launcher for Vendor C Desktop GUI Instance {instance_id} (Research Sandbox)\n'
            f'start "" wscript.exe "{vbs_path}"\n'
        )
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        print(f"[+] Created batch launcher: {bat_path}")

    # 3. Create Desktop & Proxy Shortcuts via PowerShell helper
    ps_helper = os.path.join(os.path.dirname(__file__), "create_desktop_shortcut.ps1")
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ps_helper,
        "-InstanceId", str(instance_id),
        "-ShortcutName", shortcut_name,
        "-VbsPath", vbs_path,
        "-IconPath", icon_path
    ]
    subprocess.check_call(cmd)


def main():
    parser = argparse.ArgumentParser(description="Add an isolated Vendor C Desktop GUI instance in WSL2 (Research Sandbox).")
    parser.add_argument("--id", help="Instance ID or suffix (e.g. 3, 4, tertiary, 1020)")
    parser.add_argument("--port", type=int, help="SOCKS5 port (e.g. 1020, 1030, 1081)")
    parser.add_argument("--http-port", type=int, help="HTTP proxy port (defaults to port + 10000)")
    parser.add_argument("--name", help="Display name for Desktop shortcut")
    parser.add_argument("--color", help="Icon color theme (gold, purple, blue, cyan, green, red, orange, pink, black)")
    parser.add_argument("--distro", default="Ubuntu", help="WSL distribution name (default: Ubuntu)")
    parser.add_argument("--interactive", action="store_true", help="Prompt for parameters interactively")

    args = parser.parse_args()

    if args.interactive or not args.id or not args.port:
        print("=== Interactive Setup: New Isolated Vendor C Desktop GUI (Research Environment) ===")
        if not args.id:
            args.id = input("Enter Instance ID (e.g., 3, 4, 1020): ").strip()
        if not args.port:
            port_str = input("Enter SOCKS5 port (e.g., 1020, 1030, 1081): ").strip()
            args.port = int(port_str)

    instance_id = str(args.id).strip()
    socks_port = int(args.port)
    http_port = args.http_port if args.http_port else (socks_port + 10000)

    default_color, default_label = PORT_THEMES.get(socks_port, ("purple", f"Vendor C Desktop (Instance {instance_id})"))
    color = (args.color or default_color).lower()
    shortcut_name = args.name or f"GUI_{socks_port}"

    base_ico = r"C:\MyFiles\Proxy\ico\claude.ico"
    out_ico = os.path.join(r"C:\MyFiles\Proxy\ico", f"claude_{color}.ico")

    if not os.path.exists(out_ico):
        generate_colored_ico(base_ico, out_ico, color)
    else:
        print(f"[*] Icon already exists: {out_ico}")

    # Also copy to herdrCenter for consistency
    herdr_ico = os.path.join(r"C:\MyFiles\herdrCenter", f"claude_{color}.ico")
    shutil.copy2(out_ico, herdr_ico)

    # Deploy WSL launcher
    deploy_wsl_launcher(
        distro=args.distro,
        instance_id=instance_id,
        socks_port=socks_port,
        http_port=http_port,
        wsl_user="79251",
        uid=1000
    )

    # Deploy Windows launchers & shortcuts
    deploy_windows_launchers(
        distro=args.distro,
        instance_id=instance_id,
        socks_port=socks_port,
        shortcut_name=shortcut_name,
        icon_path=out_ico
    )

    print("\n[SUCCESS] Setup complete!")
    print(f"  - Instance ID: {instance_id}")
    print(f"  - SOCKS5 Port: {socks_port}")
    print(f"  - HTTP Port: {http_port}")
    print(f"  - WSL Profile: /home/79251/.claude-{instance_id}/.config/Claude")
    print(f"  - WSL Launcher: /usr/local/bin/claude-gui-{instance_id}")
    print(f"  - Windows VBS: C:\\MyFiles\\herdrCenter\\run_claude_gui_{instance_id}.vbs")
    print(f"  - Desktop Shortcut: {shortcut_name}.lnk")


if __name__ == "__main__":
    main()
