#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automation tool to create completely isolated Vendor C / Claude Desktop GUI instances in WSL2 (WSLg)
with custom SOCKS5/HTTP proxy routing, dedicated DBus session, PID tracking,
and Windows Desktop / Proxy shortcut integration.

Разработано исключительно в исследовательских и образовательных целях
для тестирования изоляции окружений, разделения сессионных шин DBus, Secret Service и сокетов.

Usage:
    python add_vendor_c_gui_instance.py --id 1 --port 1015 --color terracotta --name "Claude GUI"
    python add_vendor_c_gui_instance.py --id 2 --port 1016 --color gold --name "Claude Code GUI 2"
"""

import argparse
import colorsys
import os
import shutil
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    Image = None

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
    1015: ("terracotta", "Claude GUI (Socket 1015)"),
    1016: ("gold", "Claude Code GUI 2 (Socket 1016)"),
    1017: ("gold", "Claude Code GUI 3 (Socket 1017)"),
    1020: ("purple", "Russia-0.1 1020"),
    1021: ("orange", "02-Russia 1021"),
    1030: ("blue", "WorProxy 1030"),
    1081: ("black", "FI FINLAND 3 1081"),
    1082: ("green", "FI FINLAND 4 1082"),
    1083: ("red", "FR FRANCE 1 1083"),
    1084: ("cyan", "FI FINLAND 2 1084"),
    1085: ("pink", "Latvia 1085"),
}


def get_default_distro():
    """Detects available WSL distribution, favoring Ubuntu-24.04 or Ubuntu."""
    try:
        out = subprocess.check_output(["wsl.exe", "-l", "-q"], text=True, errors="ignore")
        distros = [line.strip().replace('\x00', '') for line in out.splitlines() if line.strip().replace('\x00', '')]
        if "Ubuntu-24.04" in distros:
            return "Ubuntu-24.04"
        if "Ubuntu" in distros:
            return "Ubuntu"
        if distros:
            return distros[0]
    except Exception:
        pass
    return "Ubuntu-24.04"


def get_wsl_user(distro):
    """Detects primary non-root user in WSL (defaulting to UID 1000)."""
    try:
        user = subprocess.check_output(
            ["wsl.exe", "-d", distro, "bash", "-c", "id -un 1000 2>/dev/null || whoami"],
            text=True,
            errors="ignore"
        ).strip()
        if user and user != "root":
            return user
    except Exception:
        pass
    return "ubuntu"


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


def deploy_wsl_launcher(distro, instance_id, socks_port, http_port, wsl_user="ubuntu", uid=1000):
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
    def to_wsl_path(name):
        return subprocess.check_output(
            ["wsl.exe", "-d", distro, "wslpath", "-u",
             os.path.join(os.path.dirname(os.path.abspath(__file__)), name).replace("\\", "/")],
            text=True
        ).strip()

    health_src = to_wsl_path("wslg_health.sh")
    keyring_src = to_wsl_path("init_wsl_keyring.sh")
    # `install` writes a new inode: a launcher that is running right now keeps reading its old copy
    # (bash reads scripts incrementally, overwriting in place with `tee` would corrupt it).
    cmd = (
        f"tr -d '\\r' < {wsl_temp_path} > /tmp/claude-gui-{instance_id}.new && "
        f"sudo install -m 755 -o root -g root /tmp/claude-gui-{instance_id}.new {target_bin} && "
        f"rm -f /tmp/claude-gui-{instance_id}.new && "
        f"tr -d '\\r' < '{health_src}' > /tmp/wslg-health.new && "
        f"sudo install -m 755 -o root -g root /tmp/wslg-health.new /usr/local/bin/wslg-health && "
        f"rm -f /tmp/wslg-health.new && "
        f"tr -d '\\r' < '{keyring_src}' > /tmp/init-wsl-keyring.new && "
        f"sudo install -m 755 -o root -g root /tmp/init-wsl-keyring.new /usr/local/bin/init-wsl-keyring.sh && "
        f"rm -f /tmp/init-wsl-keyring.new && "
        f"if [ '{instance_id}' = '1' ]; then sudo install -m 755 -o root -g root {target_bin} /usr/local/bin/claude-gui; fi && "
        f"mkdir -m 700 -p /home/{wsl_user}/.claude-{instance_id}/.config /run/user/{uid}/claude-{instance_id} 2>/dev/null && "
        f"sudo chown -R {wsl_user}:{wsl_user} /home/{wsl_user}/.claude-{instance_id} 2>/dev/null || true && "
        f"if [ -f /usr/local/bin/init-wsl-keyring.sh ]; then sudo -u {wsl_user} /usr/local/bin/init-wsl-keyring.sh /home/{wsl_user}/.claude-{instance_id} 2>/dev/null || true; fi"
    )

    subprocess.check_call(["wsl.exe", "-d", distro, "bash", "-c", cmd])
    print(f"[+] Installed WSL launcher: {target_bin}")
    print("[+] Installed WSLg health check: /usr/local/bin/wslg-health")
    print("[+] Installed keyring provisioner: /usr/local/bin/init-wsl-keyring.sh")

    if os.path.exists(temp_script_path):
        os.remove(temp_script_path)


def ensure_wslconfig_idle_timeout():
    """Sets [general] instanceIdleTimeout=-1 in %USERPROFILE%\\.wslconfig, keeping all other settings.

    Restarting the distro inside a running WSL VM (idle stop -> next launch) breaks WSLg shared memory
    ("[WARN:COPY MODE]", blank GUI windows), so the distro must never stop on idle.
    Returns True if the file was changed (takes effect after `wsl --shutdown`).
    """
    path = os.path.join(os.path.expanduser("~"), ".wslconfig")
    content = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:  # keep CRLF visible for detection
            content = f.read()
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines()

    section_start = next((i for i, l in enumerate(lines) if l.strip().lower() == "[general]"), None)
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [
            "[general]",
            "# Never stop the distro on idle: restarting it inside a running VM breaks WSLg shared",
            "# memory (\"[WARN:COPY MODE]\", blank GUI windows). -1 = disabled.",
            "instanceIdleTimeout=-1",
        ]
    else:
        section_end = next((i for i in range(section_start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        key_idx = next((i for i in range(section_start + 1, section_end)
                        if lines[i].split("=", 1)[0].strip().lower() == "instanceidletimeout"), None)
        if key_idx is None:
            lines.insert(section_start + 1, "instanceIdleTimeout=-1")
        elif lines[key_idx].split("=", 1)[1].strip() == "-1":
            return False
        else:
            lines[key_idx] = "instanceIdleTimeout=-1"

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + newline)
    print(f"[+] Set [general] instanceIdleTimeout=-1 in {path} (applies after `wsl --shutdown`)")
    return True


def deploy_windows_launchers(distro, instance_id, socks_port, shortcut_name, icon_path, wsl_user="ubuntu"):
    """Creates VBS silent runner with pre-launch IP verification, batch file, and Windows shortcuts."""
    herdr_dir = r"C:\MyFiles\herdrCenter"
    proxy_dir = r"C:\MyFiles\Proxy"
    proxys_repo = r"C:\proj\vless2socks\proxys"
    os.makedirs(herdr_dir, exist_ok=True)
    os.makedirs(proxy_dir, exist_ok=True)
    os.makedirs(proxys_repo, exist_ok=True)

    # 1. Create VBS Silent Runner with Mac UI IP pre-verification
    template_path = os.path.join(os.path.dirname(__file__), "template_run_gui.vbs")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()
        vbs_content = template.format(
            INSTANCE_ID=instance_id,
            SOCKS_PORT=socks_port,
            DISTRO=distro,
            WSL_USER=wsl_user
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
    for bat_name in [f"Vendor_C_GUI_{instance_id}.bat", f"Claude_GUI_{instance_id}.bat", f"Claude_GUI_Socket_{socks_port}.bat"]:
        bat_path = os.path.join(proxy_dir, bat_name)
        bat_content = (
            "@echo off\n"
            "chcp 65001 >nul\n"
            f'REM Silent launcher for Claude Desktop GUI Instance {instance_id} (Port {socks_port})\n'
            f'start "" wscript.exe "{vbs_path}"\n'
        )
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        # Also copy to repo proxys
        shutil.copy2(bat_path, os.path.join(proxys_repo, bat_name))
        print(f"[+] Created batch launcher: {bat_path}")

    # 3. Create Desktop & Proxy Shortcuts via PowerShell helper
    ps_helper = os.path.join(os.path.dirname(__file__), "create_desktop_shortcut.ps1")
    names_to_create = [shortcut_name]
    if shortcut_name != f"GUI_{socks_port}":
        names_to_create.append(f"GUI_{socks_port}")

    for s_name in names_to_create:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", ps_helper,
            "-InstanceId", str(instance_id),
            "-ShortcutName", s_name,
            "-VbsPath", vbs_path,
            "-IconPath", icon_path
        ]
        subprocess.check_call(cmd)


def main():
    parser = argparse.ArgumentParser(description="Add an isolated Claude Desktop GUI instance in WSL2 (Research Sandbox).")
    parser.add_argument("--id", help="Instance ID or suffix (e.g. 1, 2, 3, 1015, 1016)")
    parser.add_argument("--port", type=int, help="SOCKS5 port (e.g. 1015, 1016, 1020)")
    parser.add_argument("--http-port", type=int, help="HTTP proxy port (defaults to port + 10000)")
    parser.add_argument("--name", help="Display name for Desktop shortcut")
    parser.add_argument("--color", help="Icon color theme (terracotta/original, gold, purple, blue, cyan, green, red, orange, pink, black)")
    parser.add_argument("--distro", default=None, help="WSL distribution name (auto-detected if omitted)")
    parser.add_argument("--interactive", action="store_true", help="Prompt for parameters interactively")

    args = parser.parse_args()

    distro = args.distro or get_default_distro()
    wsl_user = get_wsl_user(distro)

    if args.interactive or not args.id or not args.port:
        print("=== Interactive Setup: New Isolated Claude Desktop GUI (Research Environment) ===")
        print(f"[*] Detected WSL Distro: {distro}, User: {wsl_user}")
        if not args.id:
            args.id = input("Enter Instance ID (e.g., 1, 2, 1015): ").strip()
        if not args.port:
            port_str = input("Enter SOCKS5 port (e.g., 1015, 1016, 1020): ").strip()
            args.port = int(port_str)

    instance_id = str(args.id).strip()
    socks_port = int(args.port)
    http_port = args.http_port if args.http_port else (socks_port + 10000)

    default_color, default_label = PORT_THEMES.get(socks_port, ("purple", f"Claude GUI (Instance {instance_id})"))
    color = (args.color or default_color).lower()
    shortcut_name = args.name or f"GUI_{socks_port}"

    base_ico = r"C:\MyFiles\Proxy\ico\claude.ico"
    if not os.path.exists(base_ico):
        # Fallback to repo icon
        repo_ico = r"C:\proj\vless2socks\proxys\ico\claude.ico"
        if os.path.exists(repo_ico):
            os.makedirs(os.path.dirname(base_ico), exist_ok=True)
            shutil.copy2(repo_ico, base_ico)

    if color in ["terracotta", "original", "default", "base"]:
        out_ico = base_ico
    else:
        out_ico = os.path.join(r"C:\MyFiles\Proxy\ico", f"claude_{color}.ico")
        if not os.path.exists(out_ico):
            generate_colored_ico(base_ico, out_ico, color)
        else:
            print(f"[*] Icon already exists: {out_ico}")

    # Also copy to herdrCenter and repo proxys
    os.makedirs(r"C:\MyFiles\herdrCenter", exist_ok=True)
    herdr_ico = os.path.join(r"C:\MyFiles\herdrCenter", os.path.basename(out_ico))
    shutil.copy2(out_ico, herdr_ico)
    repo_proxy_ico = os.path.join(r"C:\proj\vless2socks\proxys\ico", os.path.basename(out_ico))
    os.makedirs(os.path.dirname(repo_proxy_ico), exist_ok=True)
    shutil.copy2(out_ico, repo_proxy_ico)

    # Keep the distro alive between GUI sessions (WSLg COPY MODE prevention)
    ensure_wslconfig_idle_timeout()

    # Deploy WSL launcher
    deploy_wsl_launcher(
        distro=distro,
        instance_id=instance_id,
        socks_port=socks_port,
        http_port=http_port,
        wsl_user=wsl_user,
        uid=1000
    )

    # Deploy Windows launchers & shortcuts
    deploy_windows_launchers(
        distro=distro,
        instance_id=instance_id,
        socks_port=socks_port,
        shortcut_name=shortcut_name,
        icon_path=out_ico,
        wsl_user=wsl_user
    )

    print("\n[SUCCESS] Setup complete!")
    print(f"  - Distro: {distro}")
    print(f"  - User: {wsl_user}")
    print(f"  - Instance ID: {instance_id}")
    print(f"  - SOCKS5 Port: {socks_port}")
    print(f"  - HTTP Port: {http_port}")
    print(f"  - WSL Profile: /home/{wsl_user}/.claude-{instance_id}/.config/Claude")
    print(f"  - WSL Launcher: /usr/local/bin/claude-gui-{instance_id}")
    print(f"  - Windows VBS: C:\\MyFiles\\herdrCenter\\run_claude_gui_{instance_id}.vbs")
    print(f"  - Desktop Shortcuts: {shortcut_name}.lnk, GUI_{socks_port}.lnk")


if __name__ == "__main__":
    main()
