#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pre-launch IP Verification and Mac UI Notification Toast for Vendor C GUI and CLI.
Разработано исключительно в исследовательских и образовательных целях
для валидации изоляции сетевых сокетов и предотвращения утечек трафика.

Features:
- Queries actual outgoing IP through the target SOCKS5/HTTP proxy.
- Compares against expected IP configured in vless2socks (via instances.json or geo_ip).
- Guards against IP leaks (e.g. falling back to base 1015 or direct ISP IP).
- Renders an elegant macOS-style floating dark-mode notification toast in bottom-right corner.
- 4.0s display on success with smooth fadeout -> exits 0 (launch permitted).
- 5.0s display on failure with smooth fadeout -> exits 1 (launch blocked).
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import tkinter as tk

# Add vless2socks directory to path to use its internal geo_ip resolution
VLESS2SOCKS_DIR = r"C:\MyFiles\vless2socks"
if VLESS2SOCKS_DIR not in sys.path:
    sys.path.insert(0, VLESS2SOCKS_DIR)

try:
    from geo_ip import fetch_geo_via_socks, extract_country_hint
except ImportError:
    fetch_geo_via_socks = None
    extract_country_hint = None


def is_port_listening(host: str = "127.0.0.1", port: int = 1016, timeout: float = 0.3) -> bool:
    """Fast probe to check if the local proxy port is listening."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def get_expected_vless2socks_ip(port: int) -> tuple[str, str]:
    """Finds expected IP and country from vless2socks instances.json and geo_ip."""
    instances_path = os.path.join(VLESS2SOCKS_DIR, "instances.json")
    expected_ip = ""
    country = ""

    if os.path.exists(instances_path):
        try:
            with open(instances_path, "r", encoding="utf-8") as f:
                instances = json.load(f)
            for inst in instances:
                listen = inst.get("listen", "")
                if f":{port}" in listen:
                    url = inst.get("url", "")
                    if url:
                        # Extract country hint offline first (instant!)
                        if extract_country_hint:
                            try:
                                hint = extract_country_hint(url)
                                if hint.get("country") and hint["country"] != "Unknown":
                                    country = hint["country"]
                            except Exception:
                                pass
                        # Resolve target host offline
                        try:
                            clean_url = url.split("://", 1)[1] if "://" in url else url
                            after_at = clean_url.split("@", 1)[1] if "@" in clean_url else clean_url
                            hostport = after_at.split("?", 1)[0].split("#", 1)[0]
                            host = hostport.split(":")[0].strip().lower()
                            import socket
                            resolved = socket.gethostbyname(host)
                            if resolved:
                                expected_ip = resolved
                        except Exception:
                            pass
                    break
        except Exception:
            pass

    # Only query geo_ip network call if expected_ip is still not found and port is actively listening
    if not expected_ip and fetch_geo_via_socks and is_port_listening("127.0.0.1", port):
        try:
            geo_info = fetch_geo_via_socks("127.0.0.1", port, timeout=2.0)
            if geo_info:
                if geo_info.get("ip"):
                    expected_ip = geo_info["ip"]
                if not country and geo_info.get("country"):
                    country = geo_info["country"]
        except Exception:
            pass

    # Default fallback
    if not expected_ip:
        expected_ip = ""

    return expected_ip, country


def fetch_live_proxy_ip(port: int, timeout: float = 2.0) -> str:
    """Queries external IP through 127.0.0.1:port via HTTP CONNECT or SOCKS."""
    if not is_port_listening("127.0.0.1", port):
        return ""

    proxy_url = f"http://127.0.0.1:{port}"
    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url
    })
    opener = urllib.request.build_opener(proxy_handler)
    
    # Fast HTTP plain endpoint first (no TLS handshake overhead)
    endpoints = [
        "http://api.ipify.org?format=json",
        "http://ip-api.com/json",
        "https://api.ipify.org?format=json"
    ]
    
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ip = data.get("ip") or data.get("query")
                if ip:
                    return ip.strip()
        except Exception:
            continue

    # Fallback to geo_ip module
    if fetch_geo_via_socks:
        try:
            info = fetch_geo_via_socks("127.0.0.1", port, timeout=timeout)
            if info and info.get("ip"):
                return info["ip"].strip()
        except Exception:
            pass

    return ""


def show_mac_toast(success: bool, app_label: str, detected_ip: str, expected_ip: str, country: str = ""):
    """Renders a sleek macOS-style dark floating notification toast in the bottom-right corner."""
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.0)

    # macOS Dark Mode System Palette
    bg_color = "#1e1e24"
    border_color = "#3a3a46"
    text_primary = "#f5f5f7"
    text_secondary = "#9b9ba4"
    tag_bg = "#2a2a35"
    tag_fg = "#0a84ff"
    accent_green = "#30d158"
    accent_red = "#ff453a"

    # Positioning at bottom-right corner above taskbar
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    w = 370
    h = 115
    margin_x = 24
    margin_y = 65  # Clear taskbar
    x = sw - w - margin_x
    y = sh - h - margin_y
    root.geometry(f"{w}x{h}+{x}+{y}")

    # Rounded outer border simulation
    outer = tk.Frame(root, bg=border_color, padx=1, pady=1)
    outer.pack(fill=tk.BOTH, expand=True)

    inner = tk.Frame(outer, bg=bg_color, padx=14, pady=12)
    inner.pack(fill=tk.BOTH, expand=True)

    # 1. Header row: vless2socks tag + instance title
    top_row = tk.Frame(inner, bg=bg_color)
    top_row.pack(fill=tk.X)

    badge = tk.Label(top_row, text="GUI vless2socks", font=("Segoe UI Variable Text", 8, "bold"),
                     bg=tag_bg, fg=tag_fg, padx=7, pady=2)
    badge.pack(side=tk.LEFT)

    title_lbl = tk.Label(top_row, text=app_label, font=("Segoe UI Variable Text", 9, "bold"),
                         bg=bg_color, fg=text_primary, padx=8)
    title_lbl.pack(side=tk.LEFT)

    # 2. Main content row: Status badge + IP
    mid_row = tk.Frame(inner, bg=bg_color, pady=6)
    mid_row.pack(fill=tk.X)

    status_char = "✓" if success else "✕"
    status_color = accent_green if success else accent_red

    icon_lbl = tk.Label(mid_row, text=status_char, font=("Segoe UI Variable Text", 15, "bold"),
                        bg=bg_color, fg=status_color)
    icon_lbl.pack(side=tk.LEFT, padx=(0, 10))

    if success:
        loc_str = f" ({country})" if country else ""
        main_text = f"IP: {detected_ip}{loc_str}"
        sub_text = "IP совпадает с vless2socks · Запуск разрешен"
        sub_color = "#30d158"
    else:
        ip_display = detected_ip if detected_ip else "Недоступен"
        main_text = f"Ошибка IP: {ip_display}"
        sub_text = f"Ожидался: {expected_ip} · Запуск заблокирован!"
        sub_color = "#ff453a"

    msg_lbl = tk.Label(mid_row, text=main_text, font=("Segoe UI Variable Text", 10, "bold"),
                       bg=bg_color, fg=text_primary, anchor="w")
    msg_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # 3. Bottom row: Status description
    bot_lbl = tk.Label(inner, text=sub_text, font=("Segoe UI Variable Text", 8),
                       bg=bg_color, fg=sub_color, anchor="w")
    bot_lbl.pack(fill=tk.X)

    # Smooth animation: Fade-in -> Hold (4s/5s) -> Fade-out
    def fade_in(alpha=0.0):
        if alpha < 0.96:
            alpha += 0.12
            root.attributes("-alpha", min(0.96, alpha))
            root.after(15, fade_in, alpha)
        else:
            root.attributes("-alpha", 0.96)
            duration_ms = 4000 if success else 5000
            root.after(duration_ms, fade_out, 0.96)

    def fade_out(alpha):
        if alpha > 0.05:
            alpha -= 0.08
            root.attributes("-alpha", max(0.0, alpha))
            root.after(20, fade_out, alpha)
        else:
            root.destroy()

    root.after(10, fade_in)
    root.mainloop()


def spawn_detached_toast(success: bool, app_label: str, detected_ip: str, expected_ip: str, country: str = ""):
    """Launches the Mac UI toast in an independent detached pythonw process without blocking the caller."""
    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    script_path = os.path.abspath(__file__)
    cmd = [
        pythonw,
        script_path,
        "--show-toast-only",
        "--status", "success" if success else "failure",
        "--app-label", str(app_label),
        "--detected-ip", str(detected_ip or ""),
        "--expected-ip", str(expected_ip or ""),
        "--country", str(country or ""),
    ]

    # Windows flags: DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP (0x200)
    DETACHED_FLAGS = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(cmd, creationflags=DETACHED_FLAGS, close_fds=True)
    except Exception:
        try:
            subprocess.Popen(cmd)
        except Exception:
            pass


def verify_and_notify(port: int, app_name: str, target_ip_override: str = None) -> int:
    """Performs the check, displays the Mac UI toast (non-blocking on success), and returns 0 or 1."""
    # 1. Determine expected IP
    if target_ip_override:
        expected_ip = target_ip_override
        country = ""
    else:
        expected_ip, country = get_expected_vless2socks_ip(port)

    # 2. Query live IP through socket
    detected_ip = fetch_live_proxy_ip(port)

    # 3. Verification criteria
    base_1015_ip, _ = get_expected_vless2socks_ip(1015)
    success = False

    if detected_ip and expected_ip:
        if detected_ip == expected_ip:
            success = True
        elif port != 1015 and detected_ip == base_1015_ip:
            # Dangerous leak into base 1015
            success = False
        else:
            success = False
    elif detected_ip and not expected_ip:
        # If no expected IP recorded, accept any non-empty IP that is not 1015 if port!=1015
        if port != 1015 and detected_ip == base_1015_ip:
            success = False
        else:
            success = True
    else:
        success = False

    app_label = f"{app_name} ({port})"

    # 4. Handle Notification & Return Code
    if success:
        # Synchronize Vendor C profile metadata with vless2socks
        try:
            from vendor_c_accounts import sync_vendor_c_account
            sync_vendor_c_account(port)
        except Exception:
            try:
                from claude_accounts import sync_claude_account
                sync_claude_account(port)
            except Exception:
                pass

        # NON-BLOCKING: launch toast in background and return 0 IMMEDIATELY!
        # Vendor C GUI / CLI launches with zero delay while toast displays in background!
        spawn_detached_toast(
            success=True,
            app_label=app_label,
            detected_ip=detected_ip,
            expected_ip=expected_ip or "vless2socks proxy",
            country=country
        )
        return 0
    else:
        # BLOCKING on failure: show error toast prominently (5.0s fadeout) and return 1 so program does NOT launch
        show_mac_toast(
            success=False,
            app_label=app_label,
            detected_ip=detected_ip,
            expected_ip=expected_ip or "vless2socks proxy",
            country=country
        )
        return 1


def main():
    parser = argparse.ArgumentParser(description="Pre-launch IP verification and Mac UI notification toast.")
    parser.add_argument("--port", type=int, default=1016, help="Proxy socket port (default: 1016)")
    parser.add_argument("--app", default="Vendor C GUI", help="Application name for the notification (default: Vendor C GUI)")
    parser.add_argument("--target-ip", default=None, help="Explicit target IP to check against")

    # Standalone detached toast mode
    parser.add_argument("--show-toast-only", action="store_true", help="Directly render toast window and exit")
    parser.add_argument("--status", choices=["success", "failure"], default="success")
    parser.add_argument("--app-label", default="Vendor C GUI")
    parser.add_argument("--detected-ip", default="")
    parser.add_argument("--expected-ip", default="")
    parser.add_argument("--country", default="")

    args = parser.parse_args()

    if args.show_toast_only:
        show_mac_toast(
            success=(args.status == "success"),
            app_label=args.app_label,
            detected_ip=args.detected_ip,
            expected_ip=args.expected_ip,
            country=args.country
        )
        sys.exit(0)

    exit_code = verify_and_notify(port=args.port, app_name=args.app, target_ip_override=args.target_ip)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
