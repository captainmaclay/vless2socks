#!/usr/bin/env python3
"""vless2socks — Professional Multi-Proxy GUI Manager.

Features:
- Main Overview Dashboard with real-time status and detected country flags
- Unlimited SOCKS5 proxy instances with 10-per-page pagination
- IP Geo-location & Country Zone detection on every page and in overview
- Hidden VLESS URL field with eye toggle (👁️ / 🙈) and copy button (📋)
- Options tab: Force Port Takeover, Auto-start on launch, Start minimized to tray
- Bilingual Localization (English by default, Russian toggle with original phrasing)
- AES-256-GCM Encrypted Backup (.hbak) with Master Password and SHA-256 fingerprint
- Danger Zone / Factory Reset with two-step safety confirmation
- System tray minimization and auto-recovery
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, Optional

import backup_manager
import geo_ip
import settings_manager
from i18n import get_current_language, load_language_preference, save_language_preference, t
from vless2socks.paths import APP_DIR, FROZEN, bundled, unpack_bundled_bin

# ── Tray dependencies ──────────────────────────────────────────
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ── WSL Proxy Isolation Integration ───────────────────────────
_WSL_ISO_DIR = APP_DIR / "wsl-proxy-isolation"
if str(_WSL_ISO_DIR) not in sys.path:
    sys.path.insert(0, str(_WSL_ISO_DIR))

try:
    import wsl_detector
    import firewall_isolate
    import isolation_tester
    HAS_WSL_ISO = True
except Exception:
    HAS_WSL_ISO = False

# ── Paths ──────────────────────────────────────────────────────
ROOT_DIR = APP_DIR
CONFIG_FILE = ROOT_DIR / "config.json"
INSTANCES_FILE = ROOT_DIR / "instances.json"
SETTINGS_FILE = ROOT_DIR / "settings.json"
MAIN_SCRIPT = ROOT_DIR / "main.py"

VENV_PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

#: В собранном виде запускаем этот же исполняемый файл с флагом --cli (или vless2socks-cli если он есть)
CLI_EXE = ROOT_DIR / ("vless2socks-cli.exe" if sys.platform == "win32" else "vless2socks-cli")


def proxy_command(config_path: Path | str) -> list[str]:
    """Команда запуска одного прокси — для скрипта и для сборки по-разному.
    В режиме frozen (один .exe) вызывает сам себя с флагом --cli.
    """
    if FROZEN:
        if CLI_EXE.exists():
            return [str(CLI_EXE), "-c", str(config_path)]
        return [sys.executable, "--cli", "-c", str(config_path)]
    return [PYTHON, str(MAIN_SCRIPT), "-c", str(config_path)]


# ── Xray-core: автоустановка ───────────────────────────────────
# Профили с reality / xtls-flow / ws встроенный Python-клиент поднять не может:
# REALITY прячет ключ внутри TLS ClientHello, стандартному ssl это недоступно.
# Раньше такой профиль просто падал с советом запустить tools/get_xray.py —
# теперь бинарник докачивается сам, до первого запуска прокси.

#: Качаем в один поток на всё приложение, даже если стартуют несколько прокси.
_xray_fetch_lock = threading.Lock()
#: После неудачи не долбим сеть на каждом авто-переподключении.
_xray_fetch_failed = False


class _LogStream(io.TextIOBase):
    """Приёмник вывода загрузчика: отдаёт готовые строки в лог GUI.

    Нужен вдвойне: в windowed-сборке sys.stdout равен None, и обычный print()
    внутри загрузчика свалился бы с AttributeError.
    """

    def __init__(self, emit):
        self._emit = emit
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self._emit(self._buf.strip())
        self._buf = ""


def xray_required_but_missing(config_path: Path | str) -> bool:
    """Профиль пойдёт через xray, а бинарника нет?"""
    from vless2socks.config import load_config
    from vless2socks.url import ConfigError
    from vless2socks.xray import XrayNotFound, find_xray

    try:
        cfg = load_config(config_path, strict=False)
    except ConfigError:
        return False  # пусть прокси сам объяснит, что не так со ссылкой

    backend = (cfg.backend or "auto").lower()
    if backend == "python":
        return False
    from vless2socks.url import SocksServer, WireGuardServer
    if isinstance(cfg.server, (SocksServer, WireGuardServer)):
        pass  # SOCKS5 upstream and WireGuard always require xray
    elif backend == "auto" and not getattr(cfg.server, "unsupported", None):
        return False

    try:
        find_xray(cfg.xray_path or None)
    except XrayNotFound:
        return True
    return False


def download_xray(emit) -> bool:
    """Скачать xray-core в bin/ рядом с приложением. Блокирует свой поток."""
    global _xray_fetch_failed
    from tools.get_xray import DownloadError, install

    dest = ROOT_DIR / "bin"
    stream = _LogStream(emit)
    try:
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            install(None, dest)
        stream.flush()
    except (DownloadError, OSError, ValueError) as exc:
        stream.flush()
        _xray_fetch_failed = True
        emit(f"Не удалось скачать xray-core: {exc}")
        emit("Скачайте вручную: https://github.com/XTLS/Xray-core/releases")
        emit(f"и распакуйте xray.exe, geoip.dat, geosite.dat в {dest}")
        return False
    emit("xray-core установлен.")
    return True

PAGE_SIZE = 10
BASE_PORT = 1081

# ── Colors (Catppuccin Mocha Palette) ──────────────────────────
C = {
    "bg":       "#1e1e2e",
    "surface":  "#181825",
    "overlay":  "#313244",
    "card":     "#252538",
    "text":     "#cdd6f4",
    "subtext":  "#a6adc8",
    "muted":    "#6c7086",
    "red":      "#f38ba8",
    "green":    "#a6e3a1",
    "yellow":   "#f9e2af",
    "blue":     "#89b4fa",
    "teal":     "#94e2d5",
    "purple":   "#cba6f7",
    "hover":    "#45475a",
    "border":   "#45475a",
    "log_bg":   "#11111b",
    "log_fg":   "#a6adc8",
    "orange":   "#ff7700",
}


# ── Port Utilities ─────────────────────────────────────────────
def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check if port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def is_port_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    """Check if port is responding via TCP connect."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def find_free_port(start: int = BASE_PORT, host: str = "127.0.0.1", exclude: set[int] | None = None) -> int:
    """Find first available port."""
    exclude = exclude or set()
    for p in range(start, start + 500):
        if p not in exclude and is_port_free(p, host):
            return p
    return start + len(exclude)


def kill_processes_on_port(port: int, current_pid: int | None = None) -> tuple[bool, str]:
    """Force kill processes holding the given port on Windows."""
    import os
    if current_pid is None:
        current_pid = os.getpid()

    pids_to_kill = set()
    for proto in ("tcp", "udp"):
        try:
            cmd = ["netstat", "-ano", "-p", proto]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    local_addr = parts[1]
                    if local_addr.endswith(f":{port}"):
                        pid_str = parts[-1]
                        try:
                            pid = int(pid_str)
                            if pid != 0 and pid != current_pid:
                                pids_to_kill.add(pid)
                        except ValueError:
                            pass
        except Exception as e:
            return False, f"Port scan error: {e}"

    if not pids_to_kill:
        if is_port_free(port):
            return True, t("port_already_free", port=port)
        return False, t("port_busy_wait", port=port)

    killed_count = 0
    errors = []
    for pid in pids_to_kill:
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if res.returncode == 0:
                killed_count += 1
            else:
                err = res.stderr.strip() or res.stdout.strip()
                errors.append(f"PID {pid}: {err}")
        except Exception as e:
            errors.append(f"PID {pid}: {e}")

    time.sleep(0.4)
    if is_port_free(port) or killed_count > 0:
        return True, t("port_freed", port=port, count=killed_count)
    return False, t("port_fail_free", port=port, error="; ".join(errors))


def extract_server_name(url: str) -> str:
    """Extract readable server host or remark from VLESS URL."""
    hint = geo_ip.extract_country_hint(url)
    if hint.get("remark"):
        return hint["remark"]
    if hint.get("host"):
        return hint["host"]
    try:
        after_at = url.split("@", 1)[1] if "@" in url else url
        hostport = after_at.split("?", 1)[0].split("#", 1)[0]
        host = hostport.split(":")[0]
        return host if host else "Proxy"
    except Exception:
        return "Proxy"


def load_base_config() -> dict:
    """Load config.json as template."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"url": "", "listen": "127.0.0.1:1081"}


def format_order(val: Any) -> str:
    """Format numeric order cleanly: integer as '0', '1'; float as '1.5', '2.25'."""
    try:
        f = float(val)
        if f.is_integer():
            return str(int(f))
        return f"{f:g}"
    except (ValueError, TypeError):
        return str(val) if val is not None else "0"


def ensure_system_proxy_1015(instances: list[dict]) -> list[dict]:
    """Ensure that the default unfilled System Proxy on port 1015 (#0) exists with killswitch=True and flags."""
    found_1015 = False
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        listen = str(inst.get("listen", "")).strip()
        port = None
        if ":" in listen:
            try:
                port = int(listen.rpartition(":")[2])
            except ValueError:
                pass
        elif listen.isdigit():
            port = int(listen)
        if port == 1015:
            found_1015 = True
            if not inst.get("name"):
                inst["name"] = "System Proxy"
            if "order" not in inst:
                inst["order"] = 0
            if "killswitch" not in inst:
                inst["killswitch"] = True
            if "system_proxy" not in inst and "System_Proxy" not in inst:
                inst["system_proxy"] = True
                inst["System_Proxy"] = True
            if "work_proxy" not in inst and "Work_Proxy" not in inst:
                inst["work_proxy"] = False
                inst["Work_Proxy"] = False
        elif port == 1030 or "worproxy" in str(inst.get("name", "")).lower():
            if "work_proxy" not in inst and "Work_Proxy" not in inst:
                inst["work_proxy"] = True
                inst["Work_Proxy"] = True
            if "system_proxy" not in inst and "System_Proxy" not in inst:
                inst["system_proxy"] = False
                inst["System_Proxy"] = False

    if not found_1015:
        system_proxy = {
            "name": "System Proxy",
            "order": 0,
            "url": "",
            "listen": "127.0.0.1:1015",
            "username": "",
            "password": "",
            "udp": True,
            "connectTimeout": 10,
            "udpIdleTimeout": 60,
            "logLevel": "info",
            "backend": "auto",
            "xrayPath": "",
            "xrayLegacyConfig": False,
            "killswitch": True,
            "system_proxy": True,
            "System_Proxy": True,
            "work_proxy": False,
            "Work_Proxy": False,
        }
        instances.insert(0, system_proxy)

    return instances


def load_instances() -> list[dict]:
    """Load list of instances from instances.json, ensuring System Proxy :1015 is present."""
    data = None
    try:
        with open(INSTANCES_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list) and raw:
            data = raw
    except Exception:
        pass

    if not data:
        data = []

    data = ensure_system_proxy_1015(data)
    return data


def save_instances(instances: list[dict]) -> None:
    """Save instances list to instances.json."""
    with open(INSTANCES_FILE, "w", encoding="utf-8") as f:
        json.dump(instances, f, indent=2, ensure_ascii=False)
        f.write("\n")


def restart_wsl() -> tuple[bool, str]:
    """Execute clean WSL restart:
    1. Query currently running distros.
    2. Explicitly terminate running distros (forces GUI/X11 apps to terminate).
    3. Shutdown entire WSL microVM and WSLg subsystem.
    4. Wait until verified stopped.
    5. Clean wake-up probe.
    """
    if sys.platform != "win32":
        return False, "WSL restart is only available on Windows"
    try:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW}

        # Step 1: Query currently running distros
        running_distros: list[str] = []
        try:
            res = subprocess.run(["wsl.exe", "--list", "--verbose"], capture_output=True, timeout=5, **kwargs)
            out = res.stdout.decode("utf-16le", errors="replace")
            for line in out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1].strip() == "Running":
                    name = parts[0].lstrip("*").strip()
                    if name:
                        running_distros.append(name)
                elif len(parts) >= 3 and parts[0] == "*" and parts[2].strip() == "Running":
                    name = parts[1].strip()
                    if name:
                        running_distros.append(name)
        except Exception:
            pass

        # Step 2: Terminate active distros (drops GUI apps like Claude Desktop / Electron)
        for distro in running_distros:
            try:
                subprocess.run(["wsl.exe", "--terminate", distro], capture_output=True, timeout=10, **kwargs)
            except Exception:
                pass

        # Step 3: Shutdown entire WSL microVM and WSLg
        subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=15, **kwargs)

        # Step 4: Wait until stopped (up to 5 seconds)
        for _ in range(10):
            time.sleep(0.5)
            try:
                chk = subprocess.run(["wsl.exe", "--list", "--verbose"], capture_output=True, timeout=3, **kwargs)
                out = chk.stdout.decode("utf-16le", errors="replace")
                if "Running" not in out:
                    break
            except Exception:
                break

        # Step 5: Clean wake-up probe
        wake_cmd = ["wsl.exe", "-e", "/bin/true"]
        if running_distros:
            wake_cmd = ["wsl.exe", "-d", running_distros[0], "-e", "/bin/true"]
        subprocess.run(wake_cmd, capture_output=True, timeout=15, **kwargs)
        return True, "WSL successfully restarted"
    except Exception as e:
        return False, f"WSL restart failed: {e}"


# ── Tray Icon ──────────────────────────────────────────────────
def make_tray_icon(color: str = "green") -> "Image.Image":
    """Create system tray icon image."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {
        "green": (76, 175, 80),
        "red": (244, 67, 54),
        "yellow": (255, 193, 7),
        "gray": (158, 158, 158),
    }
    fill = colors.get(color, colors["gray"])
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=fill, outline=(255, 255, 255, 200), width=2)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("segoeui.ttf", 28)
    except Exception:
        from PIL import ImageFont
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "V", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), "V", fill=(255, 255, 255), font=font)
    return img


# ── Context Menu & Clipboard Helper ────────────────────────────
def attach_clipboard_and_context_menu(widget: tk.Entry) -> None:
    """Enable Ctrl+V (EN/RU layout) and right-click context menu."""
    menu = tk.Menu(
        widget, tearoff=0,
        bg=C["overlay"], fg=C["text"],
        activebackground=C["hover"], activeforeground=C["text"],
        font=("Segoe UI", 9),
    )

    def do_cut():
        try:
            widget.event_generate("<<Cut>>")
        except Exception:
            pass

    def do_copy():
        try:
            widget.event_generate("<<Copy>>")
        except Exception:
            pass

    def do_paste():
        try:
            text = widget.clipboard_get()
            if text:
                try:
                    widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                except tk.TclError:
                    pass
                widget.insert(tk.INSERT, text)
        except Exception:
            pass

    def do_select_all():
        widget.select_range(0, tk.END)
        widget.icursor(tk.END)

    def do_clear():
        widget.delete(0, tk.END)

    menu.add_command(label="Paste", command=do_paste)
    menu.add_command(label="Copy", command=do_copy)
    menu.add_command(label="Cut", command=do_cut)
    menu.add_separator()
    menu.add_command(label="Select All", command=do_select_all)
    menu.add_command(label="Clear", command=do_clear)

    def show_popup(event):
        widget.focus_set()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    widget.bind("<Button-3>", show_popup)

    for seq in ("<Control-v>", "<Control-V>", "<Control-KeyPress-v>", "<Control-KeyPress-V>"):
        widget.bind(seq, lambda e: (do_paste(), "break")[1])
    for seq in ("<Control-c>", "<Control-C>", "<Control-KeyPress-c>", "<Control-KeyPress-C>"):
        widget.bind(seq, lambda e: (do_copy(), "break")[1])
    for seq in ("<Control-x>", "<Control-X>", "<Control-KeyPress-x>", "<Control-KeyPress-X>"):
        widget.bind(seq, lambda e: (do_cut(), "break")[1])
    for seq in ("<Control-a>", "<Control-A>", "<Control-KeyPress-a>", "<Control-KeyPress-A>"):
        widget.bind(seq, lambda e: (do_select_all(), "break")[1])

    # Windows Russian layout support (Keycodes 86=V, 67=C, 88=X, 65=A)
    def on_key(event):
        if event.state & 4:
            if event.keycode == 86 or event.keysym in ("Cyrillic_em", "Cyrillic_EM"):
                do_paste()
                return "break"
            elif event.keycode == 67 or event.keysym in ("Cyrillic_es", "Cyrillic_ES"):
                do_copy()
                return "break"
            elif event.keycode == 88 or event.keysym in ("Cyrillic_che", "Cyrillic_CHE"):
                do_cut()
                return "break"
            elif event.keycode == 65 or event.keysym in ("Cyrillic_ef", "Cyrillic_EF"):
                do_select_all()
                return "break"

    widget.bind("<KeyPress>", on_key, add="+")


# ── Proxy Instance Controller ──────────────────────────────────
class ProxyInstance:
    """Manages the background execution, state, and UI binding for a single SOCKS5 proxy."""

    def __init__(self, app: "VlessApp", cfg: dict, global_id: int):
        self.app = app
        self.cfg = cfg
        self.global_id = global_id
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.healthy = False
        self.log_lines: deque[str] = deque(maxlen=500)
        self.config_path = ROOT_DIR / f".instance_{global_id}.json"

        # Geo status
        self.geo_info: dict[str, Any] = {
            "country": "Unknown",
            "flag": "🌐",
            "ip": "",
            "verified": False,
        }
        self._init_geo_from_url()

        # UI references (when tab is active in view)
        self.frame: Optional[tk.Frame] = None
        self.url_entry: Optional[tk.Entry] = None
        self.host_entry: Optional[tk.Entry] = None
        self.port_entry: Optional[tk.Entry] = None
        self.status_dot: Optional[tk.Label] = None
        self.status_label: Optional[tk.Label] = None
        self.toggle_btn: Optional[tk.Button] = None
        self.log_text: Optional[scrolledtext.ScrolledText] = None
        self.geo_card_label: Optional[tk.Label] = None
        self.eye_btn: Optional[tk.Button] = None
        self._url_revealed = False

        # Protocol & Upstream UI
        self.proto_var: Optional[tk.StringVar] = None
        self.vless_container: Optional[tk.Frame] = None
        self.socks_container: Optional[tk.Frame] = None
        self.socks_summary_lbl: Optional[tk.Label] = None
        self.wireguard_container: Optional[tk.Frame] = None
        self.wireguard_summary_lbl: Optional[tk.Label] = None

        # Killswitch state & UI
        self.killswitch_var: Optional[tk.BooleanVar] = None
        self.ks_badge: Optional[tk.Label] = None

        # Name & Order UI references
        self.name_label: Optional[tk.Label] = None
        self.order_label: Optional[tk.Label] = None

        # Reconnect state
        self._manual_stop = False
        self._reconnect_attempt = 0
        self._reconnect_timer_id: Optional[str] = None

        # Flags: System_Proxy & Work_Proxy
        self.system_proxy_var: Optional[tk.BooleanVar] = None
        self.work_proxy_var: Optional[tk.BooleanVar] = None

        # Ensure port 1015 / 1030 defaults
        _, port = self.get_listen()
        if str(port) == "1015":
            if "killswitch" not in self.cfg:
                self.cfg["killswitch"] = True
            if not self.cfg.get("name"):
                self.cfg["name"] = "System Proxy"
            if "order" not in self.cfg:
                self.cfg["order"] = 0
            if "system_proxy" not in self.cfg and "System_Proxy" not in self.cfg:
                self.cfg["system_proxy"] = True
                self.cfg["System_Proxy"] = True
            if "work_proxy" not in self.cfg and "Work_Proxy" not in self.cfg:
                self.cfg["work_proxy"] = False
                self.cfg["Work_Proxy"] = False
        elif str(port) == "1030" or "worproxy" in str(self.cfg.get("name", "")).lower():
            if "work_proxy" not in self.cfg and "Work_Proxy" not in self.cfg:
                self.cfg["work_proxy"] = True
                self.cfg["Work_Proxy"] = True
            if "system_proxy" not in self.cfg and "System_Proxy" not in self.cfg:
                self.cfg["system_proxy"] = False
                self.cfg["System_Proxy"] = False

    def is_system_proxy(self) -> bool:
        """Check if instance is flagged as System_Proxy."""
        if self.system_proxy_var is not None:
            return bool(self.system_proxy_var.get())
        if "system_proxy" in self.cfg:
            return bool(self.cfg["system_proxy"])
        if "System_Proxy" in self.cfg:
            return bool(self.cfg["System_Proxy"])
        _, port = self.get_listen()
        return str(port) == "1015"

    def is_work_proxy(self) -> bool:
        """Check if instance is flagged as Work_Proxy."""
        if self.work_proxy_var is not None:
            return bool(self.work_proxy_var.get())
        if "work_proxy" in self.cfg:
            return bool(self.cfg["work_proxy"])
        if "Work_Proxy" in self.cfg:
            return bool(self.cfg["Work_Proxy"])
        _, port = self.get_listen()
        return str(port) == "1030" or "worproxy" in str(self.cfg.get("name", "")).lower()

    def set_system_proxy(self, val: bool) -> None:
        """Dynamically set System_Proxy flag and persist."""
        b = bool(val)
        self.cfg["system_proxy"] = b
        self.cfg["System_Proxy"] = b
        if self.system_proxy_var is not None:
            self.system_proxy_var.set(b)
        self.app.save_all()
        self.app.refresh_overview()

    def set_work_proxy(self, val: bool) -> None:
        """Dynamically set Work_Proxy flag and persist."""
        b = bool(val)
        self.cfg["work_proxy"] = b
        self.cfg["Work_Proxy"] = b
        if self.work_proxy_var is not None:
            self.work_proxy_var.set(b)
        self.app.save_all()
        self.app.refresh_overview()

    def _on_system_proxy_toggled(self) -> None:
        val = bool(self.system_proxy_var.get() if self.system_proxy_var else False)
        self.cfg["system_proxy"] = val
        self.cfg["System_Proxy"] = val
        self.app.save_all()
        self.app.refresh_overview()

    def _on_work_proxy_toggled(self) -> None:
        val = bool(self.work_proxy_var.get() if self.work_proxy_var else False)
        self.cfg["work_proxy"] = val
        self.cfg["Work_Proxy"] = val
        self.app.save_all()
        self.app.refresh_overview()

    def get_order(self) -> float:
        """Return numeric order value (>= 0). Default is 0.0 for port 1015, or global_id for others."""
        if "order" in self.cfg:
            try:
                val = float(self.cfg["order"])
                return max(0.0, val)
            except (ValueError, TypeError):
                pass
        _, port = self.get_listen()
        if str(port) == "1015":
            return 0.0
        return float(self.global_id)

    def set_order(self, new_order: float | int) -> None:
        val = max(0.0, float(new_order))
        self.cfg["order"] = int(val) if val.is_integer() else val
        self.app.sort_instances()
        self.app.save_all()
        self.app.refresh_overview()
        self.app.refresh_current_page_tabs()

    def get_display_name(self) -> str:
        """Return configured custom name or fallback to server remark / host."""
        name = str(self.cfg.get("name", "")).strip()
        if name:
            return name
        _, port = self.get_listen()
        if str(port) == "1015":
            return "System Proxy"
        return extract_server_name(self.cfg.get("url", ""))

    def set_name(self, new_name: str) -> None:
        self.cfg["name"] = new_name.strip()
        self.app.save_all()
        self.app.refresh_overview()
        self.app.refresh_current_page_tabs()
        if self.name_label and self.name_label.winfo_exists():
            self.name_label.config(text=self.get_display_name())

    def prompt_rename(self) -> None:
        self.app.prompt_rename_proxy(self)

    def prompt_reorder(self) -> None:
        self.app.prompt_reorder_proxy(self)

    def _cancel_reconnect(self):
        if self._reconnect_timer_id:
            try:
                self.app.after_cancel(self._reconnect_timer_id)
            except Exception:
                pass
            self._reconnect_timer_id = None

    def is_auto_reconnect_enabled(self) -> bool:
        """Check if auto-reconnect / forced reconnect is enabled for this instance / globally."""
        if "auto_reconnect" in self.cfg:
            return bool(self.cfg["auto_reconnect"])
        if "force_restart" in self.cfg:
            return bool(self.cfg["force_restart"])
        return bool(settings_manager.get_setting("auto_reconnect", True))

    def is_force_restart_enabled(self) -> bool:
        return self.is_auto_reconnect_enabled()

    def _schedule_reconnect(self):
        if self._manual_stop:
            return
        if not self.is_force_restart_enabled():
            return
        if self.running or (self.process and self.process.poll() is None):
            return

        raw_intervals = settings_manager.get_setting("reconnect_intervals", settings_manager.DEFAULT_RECONNECT_INTERVALS)
        intervals = settings_manager.parse_reconnect_intervals(raw_intervals)
        if not intervals:
            intervals = [10, 15, 30, 60, 120, 180, 30]

        if self._reconnect_attempt < len(intervals):
            delay = intervals[self._reconnect_attempt]
        else:
            delay = intervals[-1]

        self._reconnect_attempt += 1
        self._log(f"Auto-reconnect #{self._reconnect_attempt} scheduled in {delay}s...")
        self._cancel_reconnect()
        self._reconnect_timer_id = self.app.after(int(delay * 1000), self._do_reconnect)

    def _do_reconnect(self):
        self._reconnect_timer_id = None
        if self._manual_stop:
            return
        self._log(f"Auto-reconnecting proxy (attempt #{self._reconnect_attempt})...")
        self.start()

    def _init_geo_from_url(self):
        url = self.cfg.get("url", "")
        hint = geo_ip.extract_country_hint(url)
        if hint["country"] != "Unknown":
            self.geo_info["country"] = hint["country"]
            self.geo_info["flag"] = hint["flag"]

    def get_listen(self) -> tuple[str, str]:
        listen = self.cfg.get("listen", "127.0.0.1:1081")
        h, _, p = listen.rpartition(":")
        return h or "127.0.0.1", p or "1081"

    def get_http_port(self) -> int:
        _, p = self.get_listen()
        try:
            return int(p) + 10000
        except ValueError:
            return 11081

    def build_tab_ui(self, parent: ttk.Notebook) -> tk.Frame:
        """Build the Tkinter frame for this instance tab."""
        bg = C["bg"]
        fg = C["text"]
        px = 12

        self.frame = tk.Frame(parent, bg=bg)

        # 1. Top Panel: Status + Toggle + Close
        top = tk.Frame(self.frame, bg=bg)
        top.pack(fill=tk.X, padx=px, pady=(10, 4))

        self.status_dot = tk.Label(top, text="●", font=("Segoe UI", 16), fg=C["red"], bg=bg)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))

        self.status_label = tk.Label(
            top, text=t("status_stopped"), font=("Segoe UI", 11, "bold"), fg=fg, bg=bg
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.toggle_btn = tk.Button(
            top, text=t("btn_on"), font=("Segoe UI", 10, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=14, pady=3, command=self.toggle,
        )
        self.toggle_btn.pack(side=tk.RIGHT, padx=(8, 0))

        close_btn = tk.Button(
            top, text="✕", font=("Segoe UI", 9, "bold"),
            bg=C["overlay"], fg=C["red"], activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.close_instance,
        )
        close_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # 1.5. Name & Order Bar inside Proxy Tab
        name_bar = tk.Frame(self.frame, bg=bg)
        name_bar.pack(fill=tk.X, padx=px, pady=(2, 4))

        tk.Label(name_bar, text=t("lbl_proxy_name"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).pack(side=tk.LEFT, padx=(0, 4))
        self.name_label = tk.Label(name_bar, text=self.get_display_name(), font=("Segoe UI", 10, "bold"), fg=fg, bg=bg)
        self.name_label.pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            name_bar, text="✏️", font=("Segoe UI", 9),
            bg=bg, fg=C["subtext"], activebackground=C["hover"], activeforeground=C["text"],
            relief=tk.FLAT, bd=0, padx=4, pady=0, cursor="hand2",
            command=self.prompt_rename
        ).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(name_bar, text=t("lbl_proxy_order"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).pack(side=tk.LEFT, padx=(0, 4))
        self.order_label = tk.Label(name_bar, text=f"#{format_order(self.get_order())}", font=("Consolas", 10, "bold"), fg=C["blue"], bg=bg)
        self.order_label.pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            name_bar, text="🔢", font=("Segoe UI", 9),
            bg=bg, fg=C["subtext"], activebackground=C["hover"], activeforeground=C["text"],
            relief=tk.FLAT, bd=0, padx=4, pady=0, cursor="hand2",
            command=self.prompt_reorder
        ).pack(side=tk.LEFT)

        # 2. Geo IP & Country Zone Card
        geo_box = tk.Frame(self.frame, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        geo_box.pack(fill=tk.X, padx=px, pady=4)

        geo_left = tk.Frame(geo_box, bg=C["card"])
        geo_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)

        tk.Label(
            geo_left, text=t("lbl_geo_zone"), font=("Segoe UI", 9, "bold"),
            fg=C["blue"], bg=C["card"]
        ).pack(anchor="w")

        self.geo_card_label = tk.Label(
            geo_left, text=self._format_geo_text(), font=("Segoe UI", 10),
            fg=C["text"], bg=C["card"]
        )
        self.geo_card_label.pack(anchor="w", pady=(2, 0))

        check_geo_btn = tk.Button(
            geo_box, text=t("btn_check_geo"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=10, pady=3, command=self.check_geo_now,
        )
        check_geo_btn.pack(side=tk.RIGHT, padx=8, pady=6)

        # 3. Network Settings (Host / Port / Free Port)
        settings = tk.Frame(self.frame, bg=bg)
        settings.pack(fill=tk.X, padx=px, pady=4)

        tk.Label(settings, text=t("lbl_host"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.host_entry = tk.Entry(
            settings, font=("Consolas", 10), width=15,
            bg=C["overlay"], fg=fg, insertbackground=fg, relief=tk.FLAT, borderwidth=3,
        )
        self.host_entry.grid(row=0, column=1, sticky="w", padx=(0, 12))
        h, p = self.get_listen()
        self.host_entry.insert(0, h)
        attach_clipboard_and_context_menu(self.host_entry)

        tk.Label(settings, text=t("lbl_port"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.port_entry = tk.Entry(
            settings, font=("Consolas", 10), width=7,
            bg=C["overlay"], fg=fg, insertbackground=fg, relief=tk.FLAT, borderwidth=3,
        )
        self.port_entry.grid(row=0, column=3, sticky="w")
        self.port_entry.insert(0, p)
        attach_clipboard_and_context_menu(self.port_entry)

        free_port_btn = tk.Button(
            settings, text=t("btn_free_port"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["yellow"], activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.free_port,
        )
        free_port_btn.grid(row=0, column=4, sticky="w", padx=(10, 0))

        # 3.5. Killswitch & IP Leak Protection
        ks_frame = tk.Frame(self.frame, bg=bg)
        ks_frame.pack(fill=tk.X, padx=px, pady=(2, 4))

        default_ks = True if str(p) == "1015" else False
        self.killswitch_var = tk.BooleanVar(value=bool(self.cfg.get("killswitch", default_ks)))

        ks_chk = tk.Checkbutton(
            ks_frame, text=f" {t('lbl_killswitch')}", variable=self.killswitch_var,
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["blue"],
            command=self._on_killswitch_toggled,
        )
        ks_chk.pack(side=tk.LEFT)

        self.ks_badge = tk.Label(
            ks_frame,
            text=f"[{t('ks_active')}]" if self.killswitch_var.get() else f"[{t('ks_off')}]",
            font=("Segoe UI", 8, "bold"),
            fg=C["green"] if self.killswitch_var.get() else C["muted"],
            bg=bg,
        )
        self.ks_badge.pack(side=tk.LEFT, padx=(6, 12))

        verify_ks_btn = tk.Button(
            ks_frame, text=t("btn_verify_leak"), font=("Segoe UI", 8),
            bg=C["overlay"], fg=C["subtext"], activebackground=C["hover"], activeforeground=C["text"],
            relief=tk.FLAT, padx=8, pady=1, command=lambda: self.verify_killswitch(manual=True),
        )
        verify_ks_btn.pack(side=tk.LEFT)

        # 3.6. Proxy Role Flags: System_Proxy, Work_Proxy
        flags_frame = tk.Frame(self.frame, bg=bg)
        flags_frame.pack(fill=tk.X, padx=px, pady=(2, 4))

        tk.Label(
            flags_frame, text=t("lbl_proxy_flags"), font=("Segoe UI", 9, "bold"),
            fg=C["subtext"], bg=bg
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.system_proxy_var = tk.BooleanVar(value=self.is_system_proxy())
        sp_chk = tk.Checkbutton(
            flags_frame, text=f" {t('lbl_system_proxy')}", variable=self.system_proxy_var,
            font=("Segoe UI", 9, "bold"), fg=C["yellow"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["yellow"],
            command=self._on_system_proxy_toggled,
        )
        sp_chk.pack(side=tk.LEFT, padx=(0, 12))

        self.work_proxy_var = tk.BooleanVar(value=self.is_work_proxy())
        wp_chk = tk.Checkbutton(
            flags_frame, text=f" {t('lbl_work_proxy')}", variable=self.work_proxy_var,
            font=("Segoe UI", 9, "bold"), fg=C["teal"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["teal"],
            command=self._on_work_proxy_toggled,
        )
        wp_chk.pack(side=tk.LEFT, padx=(0, 12))

        # 4. Upstream Protocol & Server Configuration
        proto_frame = tk.Frame(self.frame, bg=bg)
        proto_frame.pack(fill=tk.X, padx=px, pady=4)

        # Protocol selector row
        proto_row = tk.Frame(proto_frame, bg=bg)
        proto_row.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            proto_row, text=t("lbl_protocol"), font=("Segoe UI", 9, "bold"),
            fg=C["subtext"], bg=bg
        ).pack(side=tk.LEFT, padx=(0, 10))

        current_url = self.cfg.get("url", "").strip()
        is_socks = current_url.startswith(("socks5://", "socks://"))
        is_wg = current_url.startswith(("wireguard://", "wg://")) or self.cfg.get("protocol") in ("wireguard", "wg")
        initial_proto = "wireguard" if is_wg else ("socks5" if is_socks else "vless")
        self.proto_var = tk.StringVar(value=initial_proto)

        rb_vless = tk.Radiobutton(
            proto_row, text=t("proto_vless"), variable=self.proto_var, value="vless",
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["blue"],
            command=self._on_proto_changed,
        )
        rb_vless.pack(side=tk.LEFT, padx=(0, 14))

        rb_socks = tk.Radiobutton(
            proto_row, text=t("proto_socks5"), variable=self.proto_var, value="socks5",
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["blue"],
            command=self._on_proto_changed,
        )
        rb_socks.pack(side=tk.LEFT, padx=(0, 14))

        rb_wg = tk.Radiobutton(
            proto_row, text=t("proto_wireguard"), variable=self.proto_var, value="wireguard",
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["blue"],
            command=self._on_proto_changed,
        )
        rb_wg.pack(side=tk.LEFT)

        # VLESS Container
        self.vless_container = tk.Frame(proto_frame, bg=bg)
        url_header = tk.Frame(self.vless_container, bg=bg)
        url_header.pack(fill=tk.X)
        tk.Label(url_header, text=t("lbl_vless_url"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=bg).pack(side=tk.LEFT)

        input_row = tk.Frame(self.vless_container, bg=bg)
        input_row.pack(fill=tk.X, pady=(2, 4))

        self.url_entry = tk.Entry(
            input_row, font=("Consolas", 9),
            bg=C["overlay"], fg=fg, insertbackground=fg,
            relief=tk.FLAT, borderwidth=5, show="•",
        )
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.url_entry.insert(0, current_url if (not is_socks and not is_wg) else "")
        attach_clipboard_and_context_menu(self.url_entry)

        self.eye_btn = tk.Button(
            input_row, text="👁️", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.toggle_url_visibility,
        )
        self.eye_btn.pack(side=tk.LEFT, padx=(0, 4))

        copy_btn = tk.Button(
            input_row, text="📋", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.copy_url,
        )
        copy_btn.pack(side=tk.LEFT)

        # SOCKS5 Container
        self.socks_container = tk.Frame(proto_frame, bg=bg)

        socks_card = tk.Frame(self.socks_container, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        socks_card.pack(fill=tk.X, pady=(2, 4))

        socks_info = tk.Frame(socks_card, bg=C["card"])
        socks_info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)

        tk.Label(
            socks_info, text=t("lbl_socks_summary"), font=("Segoe UI", 9, "bold"),
            fg=C["blue"], bg=C["card"]
        ).pack(anchor="w")

        self.socks_summary_lbl = tk.Label(
            socks_info, text=self._format_socks_summary(), font=("Consolas", 9),
            fg=C["text"], bg=C["card"], justify=tk.LEFT, wraplength=480
        )
        self.socks_summary_lbl.pack(anchor="w", pady=(2, 0))

        socks_btns = tk.Frame(socks_card, bg=C["card"])
        socks_btns.pack(side=tk.RIGHT, padx=8, pady=8)

        edit_socks_btn = tk.Button(
            socks_btns, text=t("btn_edit_socks"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=4, command=self.open_socks_dialog,
        )
        edit_socks_btn.pack(side=tk.LEFT, padx=(0, 6))

        socks_copy_btn = tk.Button(
            socks_btns, text="📋", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=3, command=self.copy_url,
        )
        socks_copy_btn.pack(side=tk.LEFT)

        # WireGuard Container
        self.wireguard_container = tk.Frame(proto_frame, bg=bg)

        wg_card = tk.Frame(self.wireguard_container, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        wg_card.pack(fill=tk.X, pady=(2, 4))

        wg_info = tk.Frame(wg_card, bg=C["card"])
        wg_info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)

        tk.Label(
            wg_info, text=t("lbl_wireguard_summary"), font=("Segoe UI", 9, "bold"),
            fg=C["blue"], bg=C["card"]
        ).pack(anchor="w")

        self.wireguard_summary_lbl = tk.Label(
            wg_info, text=self._format_wireguard_summary(), font=("Consolas", 9),
            fg=C["text"], bg=C["card"], justify=tk.LEFT, wraplength=480
        )
        self.wireguard_summary_lbl.pack(anchor="w", pady=(2, 0))

        wg_btns = tk.Frame(wg_card, bg=C["card"])
        wg_btns.pack(side=tk.RIGHT, padx=8, pady=8)

        edit_wg_btn = tk.Button(
            wg_btns, text=t("btn_edit_wireguard"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=4, command=self.open_wireguard_dialog,
        )
        edit_wg_btn.pack(side=tk.LEFT, padx=(0, 6))

        wg_copy_btn = tk.Button(
            wg_btns, text="📋", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=3, command=self.copy_url,
        )
        wg_copy_btn.pack(side=tk.LEFT)

        # Show active container
        if initial_proto == "wireguard":
            self.wireguard_container.pack(fill=tk.X)
        elif initial_proto == "socks5":
            self.socks_container.pack(fill=tk.X)
        else:
            self.vless_container.pack(fill=tk.X)

        # Common Save & Apply button
        apply_btn = tk.Button(
            proto_frame, text=t("btn_save_apply"), font=("Segoe UI", 9, "bold"),
            bg=C["hover"], fg=fg, activebackground="#585b70",
            relief=tk.FLAT, padx=12, pady=3, command=self.apply,
        )
        apply_btn.pack(anchor="w", pady=(4, 0))

        # 5. Connection Log
        sep = tk.Frame(self.frame, height=1, bg=C["border"])
        sep.pack(fill=tk.X, padx=px, pady=6)

        tk.Label(self.frame, text=t("lbl_log"), font=("Segoe UI", 8), fg=C["muted"], bg=bg).pack(anchor="w", padx=px)

        self.log_text = scrolledtext.ScrolledText(
            self.frame, font=("Consolas", 9),
            bg=C["log_bg"], fg=C["log_fg"], insertbackground=C["log_fg"],
            relief=tk.FLAT, borderwidth=4, state=tk.DISABLED, height=7,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=px, pady=(2, 8))

        # Populate previous logs
        self.log_text.config(state=tk.NORMAL)
        for line in self.log_lines:
            self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Restore visual status
        self._update_status_ui()
        return self.frame

    def _format_geo_text(self) -> str:
        flag = self.geo_info.get("flag", "🌐")
        country = self.geo_info.get("country", "Unknown")
        ip = self.geo_info.get("ip", "")
        verified = self.geo_info.get("verified", False)

        if verified and ip:
            return t("geo_verified", flag=flag, country=country, ip=ip)
        if country != "Unknown":
            return t("geo_offline_hint", flag=flag, country=country)
        return t("geo_not_checked")

    def _format_socks_summary(self) -> str:
        url = self.cfg.get("url", "").strip()
        if not (url.startswith("socks5://") or url.startswith("socks://")):
            return t("socks_not_configured")
        try:
            from vless2socks.url import parse_socks_url
            srv = parse_socks_url(url)
            name_part = f"[{srv.remark}] " if srv.remark else ""
            user_part = f" • user: {srv.username}" if srv.username else " • no-auth"
            return f"{name_part}{srv.address}:{srv.port} (SOCKS{srv.version}{user_part})"
        except Exception:
            return url

    def _update_socks_summary(self):
        if self.socks_summary_lbl:
            self.socks_summary_lbl.config(text=self._format_socks_summary())

    def _on_killswitch_toggled(self):
        val = bool(self.killswitch_var.get() if self.killswitch_var else False)
        self.cfg["killswitch"] = val
        if self.ks_badge:
            self.ks_badge.config(
                text=f"[{t('ks_active')}]" if val else f"[{t('ks_off')}]",
                fg=C["green"] if val else C["muted"],
            )
        self.app.save_all()
        self.app.refresh_overview()
        status_msg = f"Killswitch {'АКТИВИРОВАН (весь трафик привязан к прокси, утечки блокируются)' if val else 'ВЫКЛЮЧЕН'}."
        self._log(status_msg)
        if val and self.running:
            self.verify_killswitch(manual=False)

    def verify_killswitch(self, manual: bool = False, sync: bool = False):
        """Active leak verification: compare real ISP IP vs proxy exit IP."""
        if not self.running:
            if manual:
                messagebox.showinfo("Killswitch", "Запустите прокси перед проверкой утечки.", parent=self.app)
            return

        host, port_str = self.get_listen()
        try:
            port = int(port_str)
        except ValueError:
            port = 1081

        def worker():
            from vless2socks.ipcheck import check_ip_leak
            self._log("🛡️ [KILLSWITCH] Проверка защиты от утечек...")
            is_leak, direct_ip, tunnel_ip, msg = check_ip_leak(host, port)

            def on_done():
                from vless2socks.ipcheck import is_system_tun_active
                if is_leak:
                    if is_system_tun_active():
                        self._log(f"ℹ️ [KILLSWITCH] Обнаружен системный туннель (Throne). Выходной IP ({tunnel_ip}) совпадает с системным. Прокси продолжает безопасную работу.")
                        return
                    self._log(f"⚠️ [KILLSWITCH ALERT] УТЕЧКА ОБНАРУЖЕНА! Реальный IP ({direct_ip}) == IP прокси ({tunnel_ip})")
                    self._log("🛡️ [KILLSWITCH] Немедленная остановка прокси для предотвращения утечки!")
                    self.stop()
                    self._set_state("error")
                elif tunnel_ip and direct_ip and tunnel_ip != direct_ip:
                    self._log(f"✓ [KILLSWITCH SAFE] Реальный IP: {direct_ip} != Выходной IP: {tunnel_ip}. Утечек нет.")
                    if manual:
                        messagebox.showinfo(
                            "Killswitch",
                            t("killswitch_verified_safe", real_ip=direct_ip, exit_ip=tunnel_ip),
                            parent=self.app,
                        )
                else:
                    self._log(f"ℹ️ [KILLSWITCH] Статус проверки: {msg}")
                    if manual:
                        messagebox.showinfo("Killswitch", f"Статус проверки:\n{msg}", parent=self.app)

            if sync:
                on_done()
            else:
                target = self.frame if self.frame else self.app
                try:
                    target.after(0, on_done)
                except Exception:
                    on_done()

        if sync:
            worker()
            return None

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
        return worker_thread

    def _format_wireguard_summary(self) -> str:
        url = self.cfg.get("url", "").strip()
        if not (url.startswith(("wireguard://", "wg://")) or self.cfg.get("protocol") in ("wireguard", "wg")):
            return t("wireguard_not_configured")
        try:
            from vless2socks.url import parse_wireguard_url, server_from_mapping
            if url.startswith(("wireguard://", "wg://")):
                srv = parse_wireguard_url(url)
            else:
                srv = server_from_mapping(self.cfg)
            name_part = f"[{srv.remark}] " if srv.remark else ""
            ip_part = f" • IP: {','.join(srv.local_address)}" if srv.local_address else ""
            al_count = len(srv.allowed_ips) if getattr(srv, "allowed_ips", None) else 0
            al_part = f" • Allowed: {al_count} subnets" if al_count else ""
            return f"{name_part}{srv.address}:{srv.port} (WireGuard{ip_part}{al_part})"
        except Exception:
            return url

    def _update_wireguard_summary(self):
        if self.wireguard_summary_lbl:
            self.wireguard_summary_lbl.config(text=self._format_wireguard_summary())

    def _on_proto_changed(self):
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "wireguard":
            if self.vless_container:
                self.vless_container.pack_forget()
            if self.socks_container:
                self.socks_container.pack_forget()
            if self.wireguard_container:
                self.wireguard_container.pack(fill=tk.X)
                self._update_wireguard_summary()
            curr_url = self.cfg.get("url", "").strip()
            if not (curr_url.startswith(("wireguard://", "wg://")) or self.cfg.get("protocol") in ("wireguard", "wg")):
                self.open_wireguard_dialog()
        elif val == "socks5":
            if self.vless_container:
                self.vless_container.pack_forget()
            if self.wireguard_container:
                self.wireguard_container.pack_forget()
            if self.socks_container:
                self.socks_container.pack(fill=tk.X)
                self._update_socks_summary()
            curr_url = self.cfg.get("url", "").strip()
            if not curr_url.startswith(("socks5://", "socks://")):
                self.open_socks_dialog()
        else:
            if self.socks_container:
                self.socks_container.pack_forget()
            if self.wireguard_container:
                self.wireguard_container.pack_forget()
            if self.vless_container:
                self.vless_container.pack(fill=tk.X)

    def open_socks_dialog(self):
        """Open SOCKS5 credentials editor dialog matching screenshot."""
        from vless2socks.url import parse_socks_url, SocksServer

        url = self.cfg.get("url", "").strip()
        init_name = ""
        init_addr = ""
        init_port = ""
        init_ver = "5"
        init_user = ""
        init_pass = ""

        if url.startswith(("socks5://", "socks://")):
            try:
                srv = parse_socks_url(url)
                init_name = srv.remark
                init_addr = srv.address
                init_port = str(srv.port) if srv.port else ""
                init_ver = str(srv.version or 5)
                init_user = srv.username
                init_pass = srv.password
            except Exception:
                pass

        dlg = tk.Toplevel(self.app)
        dlg.title(t("dlg_socks_title"))
        dlg.geometry("420x460")
        dlg.resizable(False, False)
        dlg.configure(bg=C["bg"])
        dlg.transient(self.app)
        dlg.grab_set()

        try:
            x = self.app.winfo_x() + (self.app.winfo_width() - 420) // 2
            y = self.app.winfo_y() + (self.app.winfo_height() - 460) // 2
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        # ── Group: Common ─────────────────────────────────────────────
        grp_common = tk.LabelFrame(
            dlg, text=f" {t('grp_common')} ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["text"], bd=1, relief=tk.GROOVE
        )
        grp_common.pack(fill=tk.X, padx=14, pady=(12, 6))

        tk.Label(grp_common, text=t("lbl_name"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=0, column=0, sticky="w", padx=(12, 10), pady=(10, 4)
        )
        name_ent = tk.Entry(grp_common, font=("Consolas", 10), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=30)
        name_ent.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 4))
        name_ent.insert(0, init_name)
        attach_clipboard_and_context_menu(name_ent)

        tk.Label(grp_common, text=t("lbl_address"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=1, column=0, sticky="w", padx=(12, 10), pady=4
        )
        addr_ent = tk.Entry(grp_common, font=("Consolas", 10), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=30)
        addr_ent.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=4)
        addr_ent.insert(0, init_addr)
        attach_clipboard_and_context_menu(addr_ent)

        tk.Label(grp_common, text=t("lbl_port_field"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=2, column=0, sticky="w", padx=(12, 10), pady=4
        )
        port_ent = tk.Entry(grp_common, font=("Consolas", 10), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=30)
        port_ent.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=4)
        port_ent.insert(0, init_port)
        attach_clipboard_and_context_menu(port_ent)

        adv_btn = tk.Button(
            grp_common, text=t("btn_advanced_settings"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["subtext"], activebackground=C["hover"], activeforeground=C["text"],
            relief=tk.GROOVE, bd=1, padx=10, pady=3,
            command=lambda: messagebox.showinfo("vless2socks", "Advanced Settings:\nStandard SOCKS5 with full TCP & UDP stream tunneling via Xray.", parent=dlg),
        )
        adv_btn.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 12))

        # ── Group: Socks ──────────────────────────────────────────────
        grp_socks = tk.LabelFrame(
            dlg, text=f" {t('grp_socks')} ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["text"], bd=1, relief=tk.GROOVE
        )
        grp_socks.pack(fill=tk.X, padx=14, pady=6)

        tk.Label(grp_socks, text=t("lbl_version"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=0, column=0, sticky="w", padx=(12, 10), pady=(10, 4)
        )
        ver_cb = ttk.Combobox(grp_socks, values=["5"], state="readonly", font=("Consolas", 10), width=28)
        ver_cb.set(init_ver if init_ver == "5" else "5")
        ver_cb.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 4))

        tk.Label(grp_socks, text=t("lbl_username"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=1, column=0, sticky="w", padx=(12, 10), pady=4
        )
        user_ent = tk.Entry(grp_socks, font=("Consolas", 10), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=30)
        user_ent.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=4)
        user_ent.insert(0, init_user)
        attach_clipboard_and_context_menu(user_ent)

        tk.Label(grp_socks, text=t("lbl_password"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=2, column=0, sticky="w", padx=(12, 10), pady=(4, 12)
        )
        pass_ent = tk.Entry(grp_socks, font=("Consolas", 10), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=30)
        pass_ent.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(4, 12))
        pass_ent.insert(0, init_pass)
        attach_clipboard_and_context_menu(pass_ent)

        # ── Buttons: OK & Cancel ──────────────────────────────────────
        btn_bar = tk.Frame(dlg, bg=C["bg"])
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=14)

        def on_ok():
            addr = addr_ent.get().strip()
            p_val = port_ent.get().strip()
            name_val = name_ent.get().strip()
            user_val = user_ent.get().strip()
            pass_val = pass_ent.get().strip()

            if not addr:
                messagebox.showwarning("vless2socks", t("msg_addr_required"), parent=dlg)
                addr_ent.focus_set()
                return

            try:
                p_int = int(p_val)
                if not (1 <= p_int <= 65535):
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("vless2socks", t("msg_port_required"), parent=dlg)
                port_ent.focus_set()
                return

            srv = SocksServer(
                address=addr,
                port=p_int,
                username=user_val,
                password=pass_val,
                version=5,
                remark=name_val,
            )
            socks_url = srv.to_url()
            self.cfg["url"] = socks_url
            if self.proto_var:
                self.proto_var.set("socks5")
            self._update_socks_summary()
            self._init_geo_from_url()
            self.app.save_all()
            self.app.refresh_current_page_tabs()
            self.app.refresh_overview()
            self._log(f"Configured SOCKS5 upstream: {srv.describe()}")
            dlg.destroy()

        cancel_btn = tk.Button(
            btn_bar, text=t("btn_cancel"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["text"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=1, padx=16, pady=4, command=dlg.destroy,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        ok_btn = tk.Button(
            btn_bar, text=t("btn_ok"), font=("Segoe UI", 9, "bold"),
            bg=C["card"], fg=C["blue"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=2, highlightthickness=1, highlightbackground=C["blue"],
            padx=20, pady=4, command=on_ok,
        )
        ok_btn.pack(side=tk.RIGHT)

        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        addr_ent.focus_set()

    def open_wireguard_dialog(self):
        """Open WireGuard configuration editor and .conf importer dialog."""
        from vless2socks.url import parse_wireguard_url, parse_wireguard_conf, WireGuardServer, server_from_mapping
        import tkinter.filedialog as fd

        url = self.cfg.get("url", "").strip()
        init_name = self.cfg.get("name", "")
        init_addr = ""
        init_port = "51820"
        init_priv = ""
        init_peer_pub = ""
        init_local_ip = "10.0.0.2/32"
        init_allowed_ips = ""
        init_keepalive = ""
        init_dns = ""
        init_psk = ""
        init_mtu = "1420"

        if url.startswith(("wireguard://", "wg://")):
            try:
                srv = parse_wireguard_url(url)
                init_name = srv.remark or init_name
                init_addr = srv.address
                init_port = str(srv.port or 51820)
                init_priv = srv.secret_key
                init_peer_pub = srv.peer_public_key
                init_local_ip = ",".join(srv.local_address) if srv.local_address else "10.0.0.2/32"
                init_allowed_ips = ",".join(srv.allowed_ips) if srv.allowed_ips else ""
                init_keepalive = str(srv.keep_alive) if srv.keep_alive else ""
                init_dns = ",".join(srv.dns) if srv.dns else ""
                init_psk = srv.preshared_key
                init_mtu = str(srv.mtu or 1420)
            except Exception:
                pass
        elif "[interface]" in url.lower():
            try:
                srv = parse_wireguard_conf(url)
                init_name = srv.remark or init_name
                init_addr = srv.address
                init_port = str(srv.port or 51820)
                init_priv = srv.secret_key
                init_peer_pub = srv.peer_public_key
                init_local_ip = ",".join(srv.local_address) if srv.local_address else "10.0.0.2/32"
                init_allowed_ips = ",".join(srv.allowed_ips) if srv.allowed_ips else ""
                init_keepalive = str(srv.keep_alive) if srv.keep_alive else ""
                init_dns = ",".join(srv.dns) if srv.dns else ""
                init_psk = srv.preshared_key
                init_mtu = str(srv.mtu or 1420)
            except Exception:
                pass
        elif self.cfg.get("protocol") in ("wireguard", "wg"):
            try:
                srv = server_from_mapping(self.cfg)
                if isinstance(srv, WireGuardServer):
                    init_name = srv.remark or init_name
                    init_addr = srv.address
                    init_port = str(srv.port or 51820)
                    init_priv = srv.secret_key
                    init_peer_pub = srv.peer_public_key
                    init_local_ip = ",".join(srv.local_address) if srv.local_address else "10.0.0.2/32"
                    init_allowed_ips = ",".join(srv.allowed_ips) if getattr(srv, "allowed_ips", None) else ""
                    init_keepalive = str(srv.keep_alive) if getattr(srv, "keep_alive", None) else ""
                    init_dns = ",".join(srv.dns) if getattr(srv, "dns", None) else ""
                    init_psk = srv.preshared_key
                    init_mtu = str(srv.mtu or 1420)
            except Exception:
                pass

        dlg = tk.Toplevel(self.app)
        dlg.title(t("dlg_wireguard_title"))
        dlg.geometry("520x680")
        dlg.resizable(False, False)
        dlg.configure(bg=C["bg"])
        dlg.transient(self.app)
        dlg.grab_set()

        try:
            x = self.app.winfo_x() + (self.app.winfo_width() - 520) // 2
            y = self.app.winfo_y() + (self.app.winfo_height() - 680) // 2
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        # ── Group: Interface (Local Client) ───────────────────────────
        grp_iface = tk.LabelFrame(
            dlg, text=f" {t('grp_wireguard_interface')} ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["text"], bd=1, relief=tk.GROOVE
        )
        grp_iface.pack(fill=tk.X, padx=14, pady=(10, 4))

        tk.Label(grp_iface, text=t("lbl_private_key"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=0, column=0, sticky="w", padx=(10, 8), pady=(6, 2)
        )
        priv_ent = tk.Entry(grp_iface, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        priv_ent.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(6, 2))
        priv_ent.insert(0, init_priv)
        attach_clipboard_and_context_menu(priv_ent)

        tk.Label(grp_iface, text=t("lbl_local_ip"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=1, column=0, sticky="w", padx=(10, 8), pady=2
        )
        lip_ent = tk.Entry(grp_iface, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                           insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        lip_ent.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=2)
        lip_ent.insert(0, init_local_ip)
        attach_clipboard_and_context_menu(lip_ent)

        tk.Label(grp_iface, text=t("lbl_dns"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=2, column=0, sticky="w", padx=(10, 8), pady=2
        )
        dns_ent = tk.Entry(grp_iface, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                           insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        dns_ent.grid(row=2, column=1, sticky="ew", padx=(0, 10), pady=2)
        dns_ent.insert(0, init_dns)
        attach_clipboard_and_context_menu(dns_ent)

        tk.Label(grp_iface, text=t("lbl_mtu"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=3, column=0, sticky="w", padx=(10, 8), pady=(2, 6)
        )
        mtu_ent = tk.Entry(grp_iface, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                           insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        mtu_ent.grid(row=3, column=1, sticky="ew", padx=(0, 10), pady=(2, 6))
        mtu_ent.insert(0, init_mtu)
        attach_clipboard_and_context_menu(mtu_ent)

        # ── Group: Peer (WireGuard Server) ───────────────────────────
        grp_peer = tk.LabelFrame(
            dlg, text=f" {t('grp_wireguard_peer')} ", font=("Segoe UI", 9, "bold"),
            bg=C["bg"], fg=C["text"], bd=1, relief=tk.GROOVE
        )
        grp_peer.pack(fill=tk.X, padx=14, pady=4)

        tk.Label(grp_peer, text=t("lbl_peer_public_key"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=0, column=0, sticky="w", padx=(10, 8), pady=(6, 2)
        )
        pub_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                           insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        pub_ent.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(6, 2))
        pub_ent.insert(0, init_peer_pub)
        attach_clipboard_and_context_menu(pub_ent)

        tk.Label(grp_peer, text=t("lbl_endpoint_address"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=1, column=0, sticky="w", padx=(10, 8), pady=2
        )
        addr_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        addr_ent.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=2)
        addr_ent.insert(0, init_addr)
        attach_clipboard_and_context_menu(addr_ent)

        tk.Label(grp_peer, text=t("lbl_endpoint_port"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=2, column=0, sticky="w", padx=(10, 8), pady=2
        )
        port_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        port_ent.grid(row=2, column=1, sticky="ew", padx=(0, 10), pady=2)
        port_ent.insert(0, init_port)
        attach_clipboard_and_context_menu(port_ent)

        tk.Label(grp_peer, text=t("lbl_preshared_key"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=3, column=0, sticky="w", padx=(10, 8), pady=2
        )
        psk_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                           insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        psk_ent.grid(row=3, column=1, sticky="ew", padx=(0, 10), pady=2)
        psk_ent.insert(0, init_psk)
        attach_clipboard_and_context_menu(psk_ent)

        tk.Label(grp_peer, text=t("lbl_allowed_ips"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=4, column=0, sticky="w", padx=(10, 8), pady=2
        )
        allowed_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                               insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        allowed_ent.grid(row=4, column=1, sticky="ew", padx=(0, 10), pady=2)
        allowed_ent.insert(0, init_allowed_ips)
        attach_clipboard_and_context_menu(allowed_ent)

        tk.Label(grp_peer, text=t("lbl_persistent_keepalive"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).grid(
            row=5, column=0, sticky="w", padx=(10, 8), pady=(2, 6)
        )
        keepalive_ent = tk.Entry(grp_peer, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                                 insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=34)
        keepalive_ent.grid(row=5, column=1, sticky="ew", padx=(0, 10), pady=(2, 6))
        keepalive_ent.insert(0, init_keepalive)
        attach_clipboard_and_context_menu(keepalive_ent)

        # ── Group: Common & Actions ────────────────────────────────────
        grp_cmn = tk.Frame(dlg, bg=C["bg"])
        grp_cmn.pack(fill=tk.X, padx=14, pady=(6, 2))

        tk.Label(grp_cmn, text=t("lbl_name"), font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).pack(side=tk.LEFT, padx=(4, 6))
        name_ent = tk.Entry(grp_cmn, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
                            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, width=16)
        name_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        name_ent.insert(0, init_name)
        attach_clipboard_and_context_menu(name_ent)

        def populate_from_srv(s: WireGuardServer):
            if s.secret_key:
                priv_ent.delete(0, tk.END)
                priv_ent.insert(0, s.secret_key)
            if s.local_address:
                lip_ent.delete(0, tk.END)
                lip_ent.insert(0, ",".join(s.local_address))
            if getattr(s, "dns", None):
                dns_ent.delete(0, tk.END)
                dns_ent.insert(0, ",".join(s.dns))
            if s.mtu:
                mtu_ent.delete(0, tk.END)
                mtu_ent.insert(0, str(s.mtu))
            if s.peer_public_key:
                pub_ent.delete(0, tk.END)
                pub_ent.insert(0, s.peer_public_key)
            if s.address:
                addr_ent.delete(0, tk.END)
                addr_ent.insert(0, s.address)
            if s.port:
                port_ent.delete(0, tk.END)
                port_ent.insert(0, str(s.port))
            if s.preshared_key:
                psk_ent.delete(0, tk.END)
                psk_ent.insert(0, s.preshared_key)
            if getattr(s, "allowed_ips", None):
                allowed_ent.delete(0, tk.END)
                allowed_ent.insert(0, ",".join(s.allowed_ips))
            if getattr(s, "keep_alive", None):
                keepalive_ent.delete(0, tk.END)
                keepalive_ent.insert(0, str(s.keep_alive))
            if s.remark and not name_ent.get().strip():
                name_ent.delete(0, tk.END)
                name_ent.insert(0, s.remark)

        def build_current_srv() -> WireGuardServer:
            addr = addr_ent.get().strip()
            p_val = port_ent.get().strip()
            priv = priv_ent.get().strip()
            pub = pub_ent.get().strip()
            lip = lip_ent.get().strip()
            psk = psk_ent.get().strip()
            mtu_val = mtu_ent.get().strip()
            keep_val = keepalive_ent.get().strip()
            dns_val = dns_ent.get().strip()
            name_val = name_ent.get().strip()

            p_int = int(p_val) if p_val.isdigit() else 51820
            mtu_int = int(mtu_val) if mtu_val.isdigit() else 1420
            keep_int = int(keep_val) if keep_val.isdigit() else 0
            addrs = [a.strip() for a in lip.split(",") if a.strip()] or ["10.0.0.2/32"]
            allowed_list = [a.strip() for a in allowed_ent.get().strip().split(",") if a.strip()]
            dns_list = [d.strip() for d in dns_val.split(",") if d.strip()]

            return WireGuardServer(
                address=addr,
                port=p_int,
                secret_key=priv,
                peer_public_key=pub,
                local_address=addrs,
                allowed_ips=allowed_list,
                preshared_key=psk,
                mtu=mtu_int,
                keep_alive=keep_int,
                dns=dns_list,
                remark=name_val,
            )

        def import_conf():
            file_path = fd.askopenfilename(
                title="WireGuard .conf",
                filetypes=[("WireGuard Config (*.conf)", "*.conf"), ("All files", "*.*")],
                parent=dlg,
            )
            if not file_path:
                return
            try:
                s = parse_wireguard_conf(file_path, strict=False)
                populate_from_srv(s)
                if not name_ent.get().strip():
                    name_ent.insert(0, Path(file_path).stem)
            except Exception as e:
                messagebox.showerror("WireGuard", f"{t('msg_invalid_conf')}:\n{e}", parent=dlg)

        def paste_conf():
            try:
                raw_text = dlg.clipboard_get().strip()
            except Exception:
                messagebox.showwarning("WireGuard", "Буфер обмена пуст или недоступен", parent=dlg)
                return
            if not raw_text:
                return
            try:
                if "[interface]" in raw_text.lower():
                    s = parse_wireguard_conf(raw_text, strict=False)
                elif raw_text.startswith(("wireguard://", "wg://")):
                    s = parse_wireguard_url(raw_text, strict=False)
                else:
                    p = Path(raw_text.strip('"').strip("'"))
                    if p.exists() and p.is_file():
                        s = parse_wireguard_conf(p, strict=False)
                    else:
                        s = parse_wireguard_conf(raw_text, strict=False)
                populate_from_srv(s)
            except Exception as e:
                messagebox.showerror("WireGuard", f"Не удалось разобрать конфигурацию:\n{e}", parent=dlg)

        def export_conf():
            try:
                s = build_current_srv()
                conf_text = s.to_conf()
                dlg.clipboard_clear()
                dlg.clipboard_append(conf_text)
                messagebox.showinfo("WireGuard", "Конфигурация в формате WireGuard .conf скопирована в буфер обмена!", parent=dlg)
            except Exception as e:
                messagebox.showerror("WireGuard", f"Ошибка экспорта:\n{e}", parent=dlg)

        # ── Toolbar: Import / Paste / Export ───────────────────────────
        toolbar = tk.Frame(dlg, bg=C["bg"])
        toolbar.pack(fill=tk.X, padx=14, pady=(4, 6))

        imp_btn = tk.Button(
            toolbar, text=t("btn_import_conf"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["text"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=1, padx=8, pady=3, command=import_conf,
        )
        imp_btn.pack(side=tk.LEFT, padx=(0, 6))

        paste_btn = tk.Button(
            toolbar, text=t("btn_paste_conf"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["text"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=1, padx=8, pady=3, command=paste_conf,
        )
        paste_btn.pack(side=tk.LEFT, padx=(0, 6))

        exp_btn = tk.Button(
            toolbar, text=t("btn_export_conf"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["subtext"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=1, padx=8, pady=3, command=export_conf,
        )
        exp_btn.pack(side=tk.LEFT)

        # ── Buttons: OK & Cancel ──────────────────────────────────────
        btn_bar = tk.Frame(dlg, bg=C["bg"])
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=12)

        def on_ok():
            addr = addr_ent.get().strip()
            p_val = port_ent.get().strip()
            priv = priv_ent.get().strip()
            pub = pub_ent.get().strip()
            lip = lip_ent.get().strip()
            psk = psk_ent.get().strip()
            mtu_val = mtu_ent.get().strip()
            keep_val = keepalive_ent.get().strip()
            dns_val = dns_ent.get().strip()
            name_val = name_ent.get().strip()

            if not priv:
                messagebox.showwarning("WireGuard", t("msg_private_key_required"), parent=dlg)
                priv_ent.focus_set()
                return
            if not pub:
                messagebox.showwarning("WireGuard", t("msg_public_key_required"), parent=dlg)
                pub_ent.focus_set()
                return
            if not addr:
                messagebox.showwarning("WireGuard", t("msg_addr_required"), parent=dlg)
                addr_ent.focus_set()
                return

            try:
                p_int = int(p_val)
                if not (1 <= p_int <= 65535):
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("WireGuard", t("msg_port_required"), parent=dlg)
                port_ent.focus_set()
                return

            mtu_int = int(mtu_val) if mtu_val.isdigit() else 1420
            keep_int = int(keep_val) if keep_val.isdigit() else 0
            addrs = [a.strip() for a in lip.split(",") if a.strip()] or ["10.0.0.2/32"]
            allowed_list = [a.strip() for a in allowed_ent.get().strip().split(",") if a.strip()]
            dns_list = [d.strip() for d in dns_val.split(",") if d.strip()]

            srv = WireGuardServer(
                address=addr,
                port=p_int,
                secret_key=priv,
                peer_public_key=pub,
                local_address=addrs,
                allowed_ips=allowed_list,
                preshared_key=psk,
                mtu=mtu_int,
                keep_alive=keep_int,
                dns=dns_list,
                remark=name_val,
            )
            wg_url = srv.to_url()
            self.cfg["url"] = wg_url
            if self.proto_var:
                self.proto_var.set("wireguard")
            self._update_wireguard_summary()
            self._init_geo_from_url()
            self.app.save_all()
            self.app.refresh_current_page_tabs()
            self.app.refresh_overview()
            self._log(f"Configured WireGuard upstream: {srv.describe()}")
            dlg.destroy()

        cancel_btn = tk.Button(
            btn_bar, text=t("btn_cancel"), font=("Segoe UI", 9),
            bg=C["card"], fg=C["text"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=1, padx=16, pady=4, command=dlg.destroy,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        ok_btn = tk.Button(
            btn_bar, text=t("btn_ok"), font=("Segoe UI", 9, "bold"),
            bg=C["card"], fg=C["blue"], activebackground=C["hover"],
            relief=tk.GROOVE, bd=2, highlightthickness=1, highlightbackground=C["blue"],
            padx=20, pady=4, command=on_ok,
        )
        ok_btn.pack(side=tk.RIGHT)

        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        priv_ent.focus_set()

    def toggle_url_visibility(self):
        if not self.url_entry:
            return
        self._url_revealed = not self._url_revealed
        if self._url_revealed:
            self.url_entry.config(show="")
            if self.eye_btn:
                self.eye_btn.config(text="🙈", bg=C["hover"])
        else:
            self.url_entry.config(show="•")
            if self.eye_btn:
                self.eye_btn.config(text="👁️", bg=C["overlay"])

    def copy_url(self):
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "vless" and self.url_entry:
            url = self.url_entry.get().strip()
        else:
            url = self.cfg.get("url", "").strip()
        if url:
            self.app.clipboard_clear()
            self.app.clipboard_append(url)
            self._log(f"URL {t('copied_toast')}")

    def check_geo_now(self):
        """Perform live IP and Geo probe."""
        host, port_str = self.get_listen()
        try:
            port = int(port_str)
        except ValueError:
            port = 1081

        if not self.running or not is_port_alive(host, port):
            self.geo_info["verified"] = False
            self.geo_info["ip"] = ""
            if self.frame and self.geo_card_label:
                try:
                    self.geo_card_label.config(text=f"{self._format_geo_text()} ({t('status_stopped')})")
                except Exception:
                    pass
            self._log(f"Geo IP: {t('status_stopped')} (порт {port} не отвечает)")
            return

        if self.geo_card_label:
            self.geo_card_label.config(text=t("geo_checking"))

        url = self.url_entry.get().strip() if self.url_entry else self.cfg.get("url", "")

        def _on_result(data: dict[str, Any]):
            self.geo_info.update(data)
            self._log(f"Geo IP: {self.geo_info.get('flag')} {self.geo_info.get('country')} ({self.geo_info.get('ip') or 'no IP'})")
            if self.frame and self.geo_card_label:
                try:
                    self.frame.after(0, lambda: self.geo_card_label.config(text=self._format_geo_text()))
                except Exception:
                    pass
            self.app.after(0, self.app.refresh_overview)

        geo_ip.fetch_geo_async(host, port, _on_result, fallback_url=url)

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def free_port(self):
        port_str = self.port_entry.get().strip() if self.port_entry else self.get_listen()[1]
        try:
            port = int(port_str or "1081")
        except ValueError:
            messagebox.showwarning("vless2socks", t("port_invalid"))
            return

        if self.running:
            self.stop()

        self._log(t("port_freeing", port=port, http_port=port + 10000))
        ok, msg = kill_processes_on_port(port)
        kill_processes_on_port(port + 10000)
        self._log(msg)
        if ok:
            messagebox.showinfo("vless2socks", msg)
            self._set_state("stopped")
        else:
            messagebox.showwarning("vless2socks", msg)

    def start(self):
        self._manual_stop = False
        self._cancel_reconnect()

        if self.process and self.process.poll() is None:
            return

        val = self.proto_var.get() if self.proto_var else ("wireguard" if self.cfg.get("url", "").startswith(("wireguard://", "wg://")) else ("socks5" if self.cfg.get("url", "").startswith(("socks5://", "socks://")) else "vless"))
        if val == "vless" and self.url_entry:
            url = self.url_entry.get().strip()
        else:
            url = self.cfg.get("url", "").strip()

        host = self.host_entry.get().strip() if self.host_entry else self.get_listen()[0]
        port = self.port_entry.get().strip() if self.port_entry else self.get_listen()[1]
        host = host or "127.0.0.1"
        port = port or "1081"

        if not url:
            messagebox.showwarning("vless2socks", t("msg_url_required"))
            return
        if not (url.startswith("vless://") or url.startswith("socks5://") or url.startswith("socks://") or url.startswith("wireguard://") or url.startswith("wg://")):
            messagebox.showwarning("vless2socks", t("msg_url_prefix_any"))
            return

        # Force Port Takeover if enabled in Options
        if settings_manager.get_setting("force_port_takeover", True):
            try:
                p_int = int(port)
                http_p_int = self.get_http_port()
                if not is_port_free(p_int, host) or not is_port_free(http_p_int, host):
                    self._log(f"Force takeover: reclaiming busy ports {p_int} & {http_p_int}...")
                    kill_processes_on_port(p_int)
                    kill_processes_on_port(http_p_int)
                    time.sleep(0.3)
            except Exception as e:
                self._log(f"Takeover error: {e}")

        self.cfg["url"] = url
        self.cfg["listen"] = f"{host}:{port}"
        if self.killswitch_var is not None:
            self.cfg["killswitch"] = bool(self.killswitch_var.get())
        self._init_geo_from_url()

        # Write instance config
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)

        self._log(f"Starting proxy on {host}:{port}...")
        self._set_state("starting")

        # Профиль на reality / xtls / ws без xray не поднимется — доставим сами.
        if not _xray_fetch_failed and xray_required_but_missing(self.config_path):
            self._fetch_xray_then_start()
            return

        cmd = proxy_command(self.config_path)
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(ROOT_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            self._log(f"Launch error: {e}")
            self._set_state("error")
            return

        self.running = True
        self._log(f"Started (PID {self.process.pid})")
        self.app.save_all()
        self.app.update_tray_icon()
        self.app.refresh_overview()

        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._watcher, daemon=True).start()

        # Auto-check Geo IP shortly after startup
        self.app.after(3000, self.check_geo_now)

        # If killswitch is active, schedule leak verification
        if self.cfg.get("killswitch", False):
            self._log("🛡️ [KILLSWITCH] Активен: весь трафик привязан к прокси, проверка утечек запущена...")
            self.app.after(3500, lambda: self.verify_killswitch(manual=False))

    def _fetch_xray_then_start(self):
        """Скачать xray-core в фоне и повторить запуск, когда он появится."""
        if not _xray_fetch_lock.acquire(blocking=False):
            self._log("xray-core уже скачивается — повторю через 5 с.")
            self.app.after(5000, self.start)
            return

        self._set_state("starting")
        self._log("Профиль требует xray-core (reality / xtls / ws).")

        def worker():
            emit = lambda msg: self.app.after(0, self._log, msg)
            try:
                ok = download_xray(emit)
            finally:
                _xray_fetch_lock.release()
            self.app.after(0, self.start if ok else lambda: self._set_state("error"))

        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        self._manual_stop = True
        self._cancel_reconnect()
        self._reconnect_attempt = 0

        self._log("Stopping...")

        h, port_str = self.get_listen()
        try:
            port = int(port_str)
            http_port = self.get_http_port()
        except ValueError:
            port = 1081
            http_port = 11081

        proc = self.process
        if proc is not None:
            try:
                # На Windows proc.terminate() убивает только родительский процесс Python,
                # оставляя дочерний xray.exe висеть зомби-процессом на порту!
                # Принудительно уничтожаем всё дерево процессов через taskkill /F /T
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception as e:
                self._log(f"Stop error: {e}")

        # Гарантированное завершение любых процессов (xray/socks), удерживающих порты SOCKS5 и HTTP CONNECT
        try:
            kill_processes_on_port(port)
            kill_processes_on_port(http_port)
        except Exception as e:
            self._log(f"Port cleanup error: {e}")

        # Очистка файла конфигурации xray в runtime для предотвращения коллизий
        for cfg_name in (f"xray-config-{port}.json", "xray-config.json"):
            cfg_p = ROOT_DIR / "runtime" / cfg_name
            if cfg_p.exists():
                try:
                    cfg_p.unlink()
                except Exception:
                    pass

        # Сбрасываем кэш GeoIP и очищаем verified IP для данного порта
        geo_ip.invalidate_cache(h, port)
        self.geo_info["verified"] = False
        self.geo_info["ip"] = ""
        if self.frame and self.geo_card_label:
            try:
                self.geo_card_label.config(text=self._format_geo_text())
            except Exception:
                pass

        self.process = None
        self.running = False
        self.healthy = False
        self._set_state("stopped")
        self._log("Stopped")
        self.app.update_tray_icon()
        self.app.refresh_overview()
        self.app._update_header_stats()

    def restart(self):
        """Cleanly restart this proxy instance."""
        url = self.cfg.get("url", "").strip()
        if not url:
            try:
                if self.url_entry and self.url_entry.winfo_exists():
                    url = self.url_entry.get().strip()
            except Exception:
                pass
        if not url:
            return
        if self.running or (self.process and self.process.poll() is None):
            self.stop()
            time.sleep(0.3)
        self.start()

    def apply(self):
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "vless" and self.url_entry:
            raw_url = self.url_entry.get().strip()
            if "[interface]" in raw_url.lower() or raw_url.startswith(("wireguard://", "wg://")) or (raw_url.lower().endswith(".conf") and "\n" not in raw_url):
                try:
                    from vless2socks.url import parse_proxy_url, WireGuardServer
                    srv = parse_proxy_url(raw_url)
                    if isinstance(srv, WireGuardServer):
                        self.cfg["url"] = srv.to_url()
                        if self.proto_var:
                            self.proto_var.set("wireguard")
                        self._on_proto_changed()
                    else:
                        self.cfg["url"] = raw_url
                except Exception:
                    self.cfg["url"] = raw_url
            else:
                self.cfg["url"] = raw_url
        if self.host_entry and self.port_entry:
            h = self.host_entry.get().strip() or "127.0.0.1"
            p = self.port_entry.get().strip() or "1081"
            self.cfg["listen"] = f"{h}:{p}"
            if p == "1015":
                if "killswitch" not in self.cfg:
                    self.cfg["killswitch"] = True
                    if self.killswitch_var is not None:
                        self.killswitch_var.set(True)
                if not self.cfg.get("name"):
                    self.cfg["name"] = "System Proxy"
                if "order" not in self.cfg:
                    self.cfg["order"] = 0
        if self.killswitch_var is not None:
            self.cfg["killswitch"] = bool(self.killswitch_var.get())
        self._init_geo_from_url()
        self.app.save_all()
        self.app.refresh_current_page_tabs()
        self.app.refresh_overview()

        if self.running:
            self._log("Restarting proxy...")
            self.stop()
            self.app.after(500, self.start)
        else:
            self.start()

    def close_instance(self):
        if len(self.app.instances) <= 1:
            messagebox.showinfo("vless2socks", t("msg_cannot_close_last"))
            return
        if not messagebox.askyesno(t("msg_confirm_delete_title"), t("msg_confirm_delete")):
            return
        self.destroy()
        self.app.remove_instance(self)

    def destroy(self):
        self._manual_stop = True
        self._cancel_reconnect()
        self.stop()
        if self.config_path.exists():
            try:
                self.config_path.unlink()
            except Exception:
                pass

    def _reader(self):
        proc = self.process
        if not proc or not proc.stderr:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._log(line)
                    if "готов:" in line or "ready:" in line.lower():
                        self.healthy = True
                        self._reconnect_attempt = 0
                        if self.frame:
                            self.frame.after(0, lambda: self._set_state("running"))
        except Exception:
            pass

    def _watcher(self):
        proc = self.process
        if not proc:
            return
        proc.wait()
        code = proc.returncode
        if self._manual_stop:
            return
        self._log(f"Process terminated (exit code {code})")
        self.running = False
        self.process = None
        self.healthy = False
        h, port_str = self.get_listen()
        try:
            p = int(port_str)
            kill_processes_on_port(p)
            kill_processes_on_port(self.get_http_port())
            geo_ip.invalidate_cache(h, p)
        except Exception:
            pass
        try:
            if self.frame:
                self.frame.after(0, lambda: self._set_state("error"))
            self.app.after(0, self.app.update_tray_icon)
            self.app.after(0, self.app.refresh_overview)

            if self.is_auto_reconnect_enabled():
                self.app.after(0, self._schedule_reconnect)
        except Exception:
            pass

    def poll_health(self):
        h, p_str = self.get_listen()
        try:
            p = int(p_str)
        except ValueError:
            return
        alive = is_port_alive(h, p)

        if self.running:
            if alive and not self.healthy:
                self.healthy = True
                self._reconnect_attempt = 0
                self._set_state("running")
                self.app.update_tray_icon()
                self.app.refresh_overview()
                self.app._update_header_stats()
            elif not alive and self.healthy:
                self.healthy = False
                if self.process and self.process.poll() is None:
                    self._set_state("starting")
                else:
                    self._set_state("error")
                    if not self._manual_stop and self.is_auto_reconnect_enabled():
                        self._schedule_reconnect()
                self.app.update_tray_icon()
                self.app.refresh_overview()
                self.app._update_header_stats()
        else:
            if alive:
                if self._manual_stop:
                    # User stopped proxy, but an orphan process is still holding the port
                    kill_processes_on_port(p)
                    kill_processes_on_port(self.get_http_port())
                    geo_ip.invalidate_cache(h, p)
                elif self._reconnect_timer_id is not None:
                    # Reconnect in progress, do not mark running prematurely
                    pass
                else:
                    # Preexisting process detected on port
                    self.running = True
                    self.healthy = True
                    self._set_state("running")
                    self.app.update_tray_icon()
                    self.app.refresh_overview()
                    self.app._update_header_stats()
            else:
                if self.healthy:
                    self.healthy = False
                    self._set_state("stopped")
                    self.app.update_tray_icon()
                    self.app.refresh_overview()
                    self.app._update_header_stats()

    def _set_state(self, state: str):
        if threading.current_thread() is not threading.main_thread():
            try:
                self.app.after(0, lambda s=state: self._set_state(s))
            except Exception:
                pass
            return
        h, port = self.get_listen()
        http_port = self.get_http_port()
        states = {
            "stopped":  (C["red"],    t("status_stopped"), t("btn_on"),  C["green"]),
            "starting": (C["yellow"], t("status_starting"), t("btn_off"), C["red"]),
            "running":  (C["green"],  t("status_running", port=port, http_port=http_port), t("btn_off"), C["red"]),
            "error":    (C["red"],    t("status_error"), t("btn_on"),  C["green"]),
        }
        dot_color, label_text, btn_text, btn_color = states.get(state, states["stopped"])
        try:
            if self.status_dot and self.status_dot.winfo_exists():
                self.status_dot.config(fg=dot_color)
            if self.status_label and self.status_label.winfo_exists():
                self.status_label.config(text=label_text)
            if self.toggle_btn and self.toggle_btn.winfo_exists():
                self.toggle_btn.config(text=btn_text, bg=btn_color)
        except Exception:
            pass

    def _update_status_ui(self):
        if self.running and self.healthy:
            self._set_state("running")
        elif self.running:
            self._set_state("starting")
        else:
            self._set_state("stopped")

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        if self.frame and self.log_text:
            try:
                self.frame.after(0, lambda l=line: self._append_log(l))
            except Exception:
                pass

    def _append_log(self, line: str):
        if not self.log_text:
            return
        try:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def get_config(self) -> dict:
        cfg = dict(self.cfg)
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "vless" and self.url_entry:
            cfg["url"] = self.url_entry.get().strip()
        if self.host_entry and self.port_entry:
            h = self.host_entry.get().strip() or "127.0.0.1"
            p = self.port_entry.get().strip() or "1081"
            cfg["listen"] = f"{h}:{p}"
        if self.killswitch_var is not None:
            cfg["killswitch"] = bool(self.killswitch_var.get())
        if self.system_proxy_var is not None:
            sp_val = bool(self.system_proxy_var.get())
            cfg["system_proxy"] = sp_val
            cfg["System_Proxy"] = sp_val
        elif "system_proxy" in self.cfg or "System_Proxy" in self.cfg:
            sp_val = bool(self.cfg.get("system_proxy", self.cfg.get("System_Proxy", False)))
            cfg["system_proxy"] = sp_val
            cfg["System_Proxy"] = sp_val
        else:
            sp_val = self.is_system_proxy()
            cfg["system_proxy"] = sp_val
            cfg["System_Proxy"] = sp_val

        if self.work_proxy_var is not None:
            wp_val = bool(self.work_proxy_var.get())
            cfg["work_proxy"] = wp_val
            cfg["Work_Proxy"] = wp_val
        elif "work_proxy" in self.cfg or "Work_Proxy" in self.cfg:
            wp_val = bool(self.cfg.get("work_proxy", self.cfg.get("Work_Proxy", False)))
            cfg["work_proxy"] = wp_val
            cfg["Work_Proxy"] = wp_val
        else:
            wp_val = self.is_work_proxy()
            cfg["work_proxy"] = wp_val
            cfg["Work_Proxy"] = wp_val

        if "name" in self.cfg:
            cfg["name"] = self.cfg["name"]
        if "order" in self.cfg:
            cfg["order"] = self.cfg["order"]
        if "sendThrough" in self.cfg:
            cfg["sendThrough"] = self.cfg["sendThrough"]
        return cfg


# ── Custom Styled Orange Checkbox ──────────────────────────────
class OrangeCheckbox(tk.Canvas):
    """Custom high-contrast checkbox widget with a bright orange checkmark."""

    def __init__(
        self,
        parent,
        variable: tk.BooleanVar,
        command=None,
        bg: str = "#252538",
        orange_color: str = "#ff7700",
        size: int = 18,
    ):
        super().__init__(
            parent,
            width=size,
            height=size,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
        )
        self.variable = variable
        self.command = command
        self.orange_color = orange_color
        self.bg_color = bg
        self.size = size
        self.bind("<Button-1>", self._toggle)
        self.bind("<KeyPress-space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self._trace_id = None
        if hasattr(self.variable, "trace_add"):
            try:
                self._trace_id = self.variable.trace_add("write", lambda *_: self.redraw())
            except Exception:
                pass
        self.redraw()

    def _toggle(self, event=None):
        try:
            val = bool(self.variable.get())
        except Exception:
            val = False
        self.variable.set(not val)
        if self.command:
            self.command()

    def redraw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        try:
            checked = bool(self.variable.get())
        except Exception:
            checked = False
        s = self.size
        # Outer box
        box_outline = self.orange_color if checked else "#55556a"
        inner_bg = "#181825"
        self.create_rectangle(1, 1, s - 2, s - 2, outline=box_outline, fill=inner_bg, width=1.5)
        if checked:
            # Bright orange checkmark
            self.create_line(
                int(s * 0.22), int(s * 0.50),
                int(s * 0.40), int(s * 0.72),
                int(s * 0.74), int(s * 0.28),
                fill=self.orange_color,
                width=2.5,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
            )

    def destroy(self):
        try:
            if hasattr(self, "_trace_id") and self._trace_id and hasattr(self.variable, "trace_remove"):
                self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass
        super().destroy()


# ── RestartServices Floating HUD / Log Window ─────────────────
class RestartHUDWindow(tk.Toplevel):
    """Semi-transparent floating log monitor for RestartServices in bottom-right corner.
    Auto-closes after 20s unless pinned via the pin button (📌). Can also be closed by (✕).
    """
    AUTO_CLOSE_SEC: int = 20

    def __init__(self, parent):
        super().__init__(parent)
        self.app = parent
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.92)
        self.overrideredirect(True)
        self.configure(bg=C["border"])

        self.is_pinned: bool = False
        self.in_progress: bool = True
        self.countdown_sec: int = self.AUTO_CLOSE_SEC
        self._timer_id: Optional[str] = None
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0

        # Geometry & position in bottom-right corner of primary screen
        target_w = 420
        target_h = 220
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = max(10, sw - target_w - 20)
            y = max(10, sh - target_h - 60)
        except Exception:
            x, y = 800, 500
        self.geometry(f"{target_w}x{target_h}+{x}+{y}")

        # Container with 1px accent border
        border_frame = tk.Frame(self, bg=C["blue"], padx=1, pady=1)
        border_frame.pack(fill=tk.BOTH, expand=True)

        main_box = tk.Frame(border_frame, bg=C["card"])
        main_box.pack(fill=tk.BOTH, expand=True)

        # 1. Header (Draggable)
        self.header = tk.Frame(main_box, bg=C["surface"], height=32, padx=8, pady=4)
        self.header.pack(fill=tk.X)
        self.header.bind("<Button-1>", self._start_drag)
        self.header.bind("<B1-Motion>", self._do_drag)

        # Title
        self.title_lbl = tk.Label(
            self.header,
            text=f"🔄  {t('restart_hud_title')}",
            font=("Segoe UI", 9, "bold"),
            fg=C["text"],
            bg=C["surface"],
        )
        self.title_lbl.pack(side=tk.LEFT)
        self.title_lbl.bind("<Button-1>", self._start_drag)
        self.title_lbl.bind("<B1-Motion>", self._do_drag)

        # Right buttons: Close (✕), Pin (📌), Status Badge
        self.close_btn = tk.Button(
            self.header,
            text="✕",
            font=("Segoe UI", 9, "bold"),
            fg=C["subtext"],
            bg=C["surface"],
            activeforeground=C["red"],
            activebackground=C["overlay"],
            relief=tk.FLAT,
            bd=0,
            padx=6,
            cursor="hand2",
            command=self.close,
        )
        self.close_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.pin_btn = tk.Button(
            self.header,
            text="📌",
            font=("Segoe UI", 9),
            fg=C["subtext"],
            bg=C["surface"],
            activeforeground=C["orange"],
            activebackground=C["overlay"],
            relief=tk.FLAT,
            bd=0,
            padx=4,
            cursor="hand2",
            command=self.toggle_pin,
        )
        self.pin_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.status_badge = tk.Label(
            self.header,
            text="🔄 ...",
            font=("Segoe UI", 8),
            fg=C["yellow"],
            bg=C["surface"],
        )
        self.status_badge.pack(side=tk.RIGHT, padx=(0, 4))

        # 2. Log Text Area
        log_frame = tk.Frame(main_box, bg=C["log_bg"], padx=4, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            font=("Consolas", 8),
            bg=C["log_bg"],
            fg=C["log_fg"],
            relief=tk.FLAT,
            bd=0,
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.log_scrollbar = tk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scrollbar.set)
        self.log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("SUCCESS", foreground=C["green"])
        self.log_text.tag_config("INFO", foreground=C["blue"])
        self.log_text.tag_config("WARN", foreground=C["yellow"])
        self.log_text.tag_config("ERROR", foreground=C["red"])
        self.log_text.tag_config("TIME", foreground=C["muted"])

        # Start countdown ticker
        self._schedule_tick()

    def _start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _do_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_start_x)
        y = self.winfo_y() + (event.y - self._drag_start_y)
        self.geometry(f"+{x}+{y}")

    def toggle_pin(self):
        self.is_pinned = not self.is_pinned
        if self.is_pinned:
            self.pin_btn.config(fg=C["orange"], bg=C["overlay"])
            self.status_badge.config(text=f"📌 {t('restart_hud_pinned')}", fg=C["orange"])
        else:
            self.pin_btn.config(fg=C["subtext"], bg=C["surface"])
            self.countdown_sec = self.AUTO_CLOSE_SEC
            if not self.in_progress:
                self.status_badge.config(text=f"⏱ {self.AUTO_CLOSE_SEC}s", fg=C["muted"])

    def log(self, msg: str):
        if not self.winfo_exists():
            return
        ts = time.strftime("%H:%M:%S")

        tag = "INFO"
        if "✅" in msg or "✓" in msg or "Finished" in msg or "successfully" in msg:
            tag = "SUCCESS"
        elif "⚠️" in msg or "Warn" in msg:
            tag = "WARN"
        elif "❌" in msg or "Error" in msg or "failed" in msg:
            tag = "ERROR"

        def _append():
            try:
                if not self.winfo_exists():
                    return
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"[{ts}] ", "TIME")
                self.log_text.insert(tk.END, f"{msg}\n", tag)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except Exception:
                pass

        if self.winfo_exists():
            self.after(0, _append)

    def set_finished(self, summary: str = ""):
        self.in_progress = False
        self.countdown_sec = self.AUTO_CLOSE_SEC
        try:
            if self.winfo_exists() and not self.is_pinned:
                self.status_badge.config(text=f"⏱ {self.AUTO_CLOSE_SEC}s", fg=C["muted"])
        except Exception:
            pass

    def _schedule_tick(self):
        if not self.winfo_exists():
            return
        if not self.is_pinned and not self.in_progress:
            self.countdown_sec -= 1
            if self.countdown_sec <= 0:
                self.close()
                return
            try:
                self.status_badge.config(text=f"⏱ {self.countdown_sec}s", fg=C["muted"])
            except Exception:
                pass
        self._timer_id = self.after(1000, self._schedule_tick)

    def close(self):
        if self._timer_id:
            try:
                self.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None
        if hasattr(self.app, "restart_hud_win") and self.app.restart_hud_win == self:
            self.app.restart_hud_win = None
        try:
            if self.winfo_exists():
                self.destroy()
        except Exception:
            pass


# ── Main Application Window ───────────────────────────────────
class VlessApp(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title(t("app_title"))
        self.geometry("820x620")
        self.minsize(680, 480)
        self.configure(bg=C["bg"])

        # State
        self.instances: list[ProxyInstance] = []
        self.current_page: int = 0
        self._tray_icon: Optional[pystray.Icon] = None
        self._tray_thread: Optional[threading.Thread] = None
        self._hidden = False

        # RestartServices State & Checkbox Preferences
        self._restart_menu_open = False
        self.restart_menu_win: Optional[tk.Toplevel] = None
        self.restart_hud_win: Optional[RestartHUDWindow] = None
        self.btn_restart_services: Optional[tk.Button] = None
        self.btn_restart_arrow: Optional[tk.Button] = None
        self.restart_wsl_var = tk.BooleanVar(value=bool(settings_manager.get_setting("restart_wsl", True)))
        self.restart_work_proxy_var = tk.BooleanVar(value=bool(settings_manager.get_setting("restart_work_proxy", True)))
        self.restart_system_proxy_var = tk.BooleanVar(value=bool(settings_manager.get_setting("restart_system_proxy", True)))

        # Protocols & Window State Handlers
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.bind("<Unmap>", self._on_main_unmap, add="+")
        self.bind("<Deactivate>", self._on_main_deactivate, add="+")

        self._load_saved_data()
        self._build_main_ui()
        self._setup_tray()
        self._poll_loop()

        # Startup Options: Auto-start configured proxies
        if settings_manager.get_setting("autostart_proxies", True):
            self.after(500, self.autostart_configured_proxies)

        # Startup Options: Start minimized to tray
        if HAS_TRAY and settings_manager.get_setting("start_minimized_tray", True):
            self.after(100, self._hide_to_tray)

    def sort_instances(self):
        """Sort instances ascending by numeric order (>= 0), then by global_id."""
        self.instances.sort(key=lambda inst: (inst.get_order(), inst.global_id))

    def _load_saved_data(self):
        raw_instances = load_instances()
        self.instances = [ProxyInstance(self, cfg, idx) for idx, cfg in enumerate(raw_instances)]
        self.sort_instances()
        if not self.instances:
            base = load_base_config()
            self.instances = [ProxyInstance(self, base, 0)]

    def _build_main_ui(self):
        # 1. Top Header Bar (active and visible across all pages)
        self.top_bar = tk.Frame(self, bg=C["surface"], height=46)
        self.top_bar.pack(fill=tk.X)

        title_badge = tk.Label(
            self.top_bar, text=f" {t('app_badge')} ", font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", padx=6, pady=2,
        )
        title_badge.pack(side=tk.LEFT, padx=(10, 8), pady=8)

        self.count_label = tk.Label(
            self.top_bar, text="", font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["surface"],
        )
        self.count_label.pack(side=tk.LEFT, padx=4)

        # Quick Add Button in header (far right)
        add_btn = tk.Button(
            self.top_bar, text=t("btn_new_tab"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=12, pady=3, command=self.add_new_instance,
        )
        add_btn.pack(side=tk.RIGHT, padx=(4, 10), pady=8)

        # RestartServices Split-Button Container (directly adjacent to Add Button)
        self.restart_container = tk.Frame(self.top_bar, bg=C["surface"])
        self.restart_container.pack(side=tk.RIGHT, padx=(0, 6), pady=8)

        # 1. Main Action Button: [ 🔄 RestartServices ]
        self.btn_restart_services = tk.Button(
            self.restart_container,
            text=f"🔄 {t('btn_restart_services')}",
            font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2",
            command=self.execute_restart_services,
        )
        self.btn_restart_services.pack(side=tk.LEFT)

        # 2. Adjacent Arrow Button: [ ▼ ] with slide-down menu
        self.btn_restart_arrow = tk.Button(
            self.restart_container,
            text="▼",
            font=("Segoe UI", 8, "bold"),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], activeforeground=C["blue"],
            relief=tk.FLAT, padx=6, pady=4, cursor="hand2",
            command=self.toggle_restart_services_menu,
        )
        self.btn_restart_arrow.pack(side=tk.LEFT, padx=(1, 0))

        # 2. Main Navigation Notebook
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=C["surface"], foreground=C["text"],
            padding=[14, 7], font=("Segoe UI", 9, "bold"), borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", C["bg"])],
            foreground=[("selected", C["blue"])],
        )

        self.nav_notebook = ttk.Notebook(self)
        self.nav_notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Overview
        self.overview_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.overview_frame, text=t("nav_overview"))
        self._build_overview_tab()

        # Tab 2: Proxies (Paginated)
        self.proxies_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.proxies_frame, text=t("nav_proxies"))
        self._build_proxies_tab()

        # Tab 3: Options (NEW)
        self.options_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.options_frame, text=t("nav_options"))
        self._build_options_tab()

        # Tab 4: Backup & Security
        self.backup_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.backup_frame, text=t("nav_backup"))
        self._build_backup_tab()

        # Tab 5: WSL Isolation Guard
        self.wsl_iso_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.wsl_iso_frame, text=t("nav_wsl_isolation"))
        self._build_wsl_isolation_tab()

        # Tab 6: Localization
        self.loc_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.loc_frame, text=t("nav_localization"))
        self._build_localization_tab()

        self._update_header_stats()

    # ── Overview Tab ──────────────────────────────────────────
    def _build_overview_tab(self):
        bg = C["bg"]
        px = 12

        # Header bar with Batch Actions
        header = tk.Frame(self.overview_frame, bg=bg)
        header.pack(fill=tk.X, padx=px, pady=(10, 6))

        info_box = tk.Frame(header, bg=bg)
        info_box.pack(side=tk.LEFT)

        tk.Label(info_box, text=t("overview_title"), font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(anchor="w")
        tk.Label(info_box, text=t("overview_subtitle"), font=("Segoe UI", 8), fg=C["subtext"], bg=bg).pack(anchor="w")

        btn_box = tk.Frame(header, bg=bg)
        btn_box.pack(side=tk.RIGHT)

        tk.Button(
            btn_box, text=t("btn_start_all"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=3, command=self.start_all,
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            btn_box, text=t("btn_stop_all"), font=("Segoe UI", 9, "bold"),
            bg=C["red"], fg="#1e1e2e", activebackground="#eba0ac",
            relief=tk.FLAT, padx=10, pady=3, command=self.stop_all,
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            btn_box, text=t("btn_refresh_all_geo"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=10, pady=3, command=self.refresh_all_geo,
        ).pack(side=tk.LEFT, padx=3)

        # Scrollable container for proxy rows
        canvas_frame = tk.Frame(self.overview_frame, bg=bg)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=px, pady=(4, 10))

        self.overview_canvas = tk.Canvas(canvas_frame, bg=bg, highlightthickness=0)
        self.overview_scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.overview_canvas.yview)
        self.overview_content = tk.Frame(self.overview_canvas, bg=bg)

        self.overview_content.bind(
            "<Configure>", lambda e: self.overview_canvas.configure(scrollregion=self.overview_canvas.bbox("all"))
        )
        self.overview_canvas.create_window((0, 0), window=self.overview_content, anchor="nw", width=780)
        self.overview_canvas.configure(yscrollcommand=self.overview_scrollbar.set)

        self.overview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.overview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.refresh_overview()

    def refresh_overview(self):
        """Redraw all proxy cards in the overview content frame."""
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.refresh_overview)
            except Exception:
                pass
            return

        if not hasattr(self, "overview_content") or not self.overview_content:
            return
        try:
            if not self.overview_content.winfo_exists():
                return
        except Exception:
            return

        for widget in self.overview_content.winfo_children():
            try:
                if widget.winfo_exists():
                    widget.destroy()
            except Exception:
                pass

        if not self.instances:
            tk.Label(
                self.overview_content, text=t("empty_overview"),
                font=("Segoe UI", 11), fg=C["subtext"], bg=C["bg"], pady=40,
            ).pack()
            return

        for idx, inst in enumerate(self.instances):
            h, port = inst.get_listen()
            http_port = inst.get_http_port()
            display_name = inst.get_display_name()
            order_str = format_order(inst.get_order())

            flag = inst.geo_info.get("flag", "🌐")
            country = inst.geo_info.get("country", "Unknown")
            ip = inst.geo_info.get("ip", "")

            # Row Card
            card = tk.Frame(self.overview_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
            card.pack(fill=tk.X, pady=3, padx=2)

            # Left badge & Index
            left_badge = tk.Frame(card, bg=C["card"])
            left_badge.pack(side=tk.LEFT, padx=(10, 6), pady=8)

            dot_color = C["green"] if inst.running and inst.healthy else (C["yellow"] if inst.running else C["red"])
            tk.Label(left_badge, text="●", font=("Segoe UI", 14), fg=dot_color, bg=C["card"]).pack(side=tk.LEFT, padx=(0, 4))
            
            # Interactive order badge (click to change order)
            order_btn = tk.Button(
                left_badge, text=f"#{order_str}", font=("Consolas", 10, "bold"),
                fg=C["blue"], bg=C["card"], activebackground=C["card"], activeforeground=C["teal"],
                relief=tk.FLAT, bd=0, cursor="hand2",
                command=lambda i=inst: self.prompt_reorder_proxy(i),
            )
            order_btn.pack(side=tk.LEFT)

            # Center Info: Name, Ports, Geo
            info = tk.Frame(card, bg=C["card"])
            info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)

            title_row = tk.Frame(info, bg=C["card"])
            title_row.pack(anchor="w")

            tk.Label(title_row, text=display_name, font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"]).pack(side=tk.LEFT)

            # Pencil rename button
            rename_btn = tk.Button(
                title_row, text="✏️", font=("Segoe UI", 9),
                bg=C["card"], fg=C["subtext"], activebackground=C["card"], activeforeground=C["text"],
                relief=tk.FLAT, bd=0, padx=4, pady=0, cursor="hand2",
                command=lambda i=inst: self.prompt_rename_proxy(i),
            )
            rename_btn.pack(side=tk.LEFT, padx=(4, 0))
            if inst.cfg.get("killswitch", False):
                tk.Label(
                    title_row, text=" 🛡️ KS ", font=("Segoe UI", 7, "bold"),
                    bg=C["hover"], fg=C["green"], padx=4, pady=1
                ).pack(side=tk.LEFT, padx=(6, 0))

            if inst.is_system_proxy():
                tk.Label(
                    title_row, text=f" {t('badge_system_proxy')} ", font=("Segoe UI", 7, "bold"),
                    bg=C["hover"], fg=C["yellow"], padx=4, pady=1
                ).pack(side=tk.LEFT, padx=(6, 0))

            if inst.is_work_proxy():
                tk.Label(
                    title_row, text=f" {t('badge_work_proxy')} ", font=("Segoe UI", 7, "bold"),
                    bg=C["hover"], fg=C["teal"], padx=4, pady=1
                ).pack(side=tk.LEFT, padx=(6, 0))

            detail_row = tk.Frame(info, bg=C["card"])
            detail_row.pack(anchor="w", pady=(2, 0))

            tk.Label(
                detail_row,
                text=f"SOCKS5: {h}:{port}  |  HTTP: {h}:{http_port}",
                font=("Consolas", 9), fg=C["subtext"], bg=C["card"]
            ).pack(side=tk.LEFT, padx=(0, 12))

            geo_str = f"{flag} {country}" + (f" ({ip})" if ip else "")
            tk.Label(
                detail_row,
                text=geo_str,
                font=("Segoe UI", 9, "bold"), fg=C["yellow"] if country != "Unknown" else C["muted"], bg=C["card"]
            ).pack(side=tk.LEFT)

            # Right action buttons
            actions = tk.Frame(card, bg=C["card"])
            actions.pack(side=tk.RIGHT, padx=10, pady=6)

            # Start/Stop toggle
            toggle_btn_text = t("btn_off") if inst.running else t("btn_on")
            toggle_btn_bg = C["red"] if inst.running else C["green"]
            tk.Button(
                actions, text=toggle_btn_text, font=("Segoe UI", 9, "bold"),
                bg=toggle_btn_bg, fg="#1e1e2e", relief=tk.FLAT, padx=8, pady=2,
                command=lambda i=inst: (i.toggle(), self.refresh_overview()),
            ).pack(side=tk.LEFT, padx=3)

            # Check Geo button
            tk.Button(
                actions, text="🔍", font=("Segoe UI", 9),
                bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=6, pady=2,
                command=lambda i=inst: i.check_geo_now(),
            ).pack(side=tk.LEFT, padx=3)

            # Jump to Account tab button
            tk.Button(
                actions, text=t("btn_view_tab"), font=("Segoe UI", 9),
                bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=8, pady=2,
                command=lambda idx=idx: self._jump_to_instance(idx),
            ).pack(side=tk.LEFT, padx=3)

    def _jump_to_instance(self, global_idx: int):
        target_page = global_idx // PAGE_SIZE
        target_tab_idx = global_idx % PAGE_SIZE
        self.current_page = target_page
        self.nav_notebook.select(self.proxies_frame)
        self.refresh_current_page_tabs()
        if target_tab_idx < len(self.sub_notebook.tabs()):
            self.sub_notebook.select(target_tab_idx)

    def prompt_rename_proxy(self, inst: ProxyInstance):
        old_name = inst.get_display_name()
        _, port = inst.get_listen()
        res = simpledialog.askstring(
            t("rename_proxy_title"),
            t("rename_proxy_prompt", port=port),
            initialvalue=old_name,
            parent=self,
        )
        if res is not None:
            inst.set_name(res.strip())

    def prompt_reorder_proxy(self, inst: ProxyInstance):
        old_order = format_order(inst.get_order())
        res = simpledialog.askstring(
            t("reorder_proxy_title"),
            t("reorder_proxy_prompt", name=inst.get_display_name()),
            initialvalue=old_order,
            parent=self,
        )
        if res is not None:
            res_clean = res.strip().replace(",", ".")
            try:
                val = float(res_clean)
                if val < 0:
                    messagebox.showerror(t("app_title"), t("err_negative_order"), parent=self)
                    return
                inst.set_order(val)
            except ValueError:
                messagebox.showerror(t("app_title"), t("err_invalid_number"), parent=self)

    def _on_options_auto_reconnect_toggled(self):
        val = bool(self.auto_reconnect_var.get()) if hasattr(self, "auto_reconnect_var") else bool(settings_manager.get_setting("auto_reconnect", True))
        settings_manager.set_setting("auto_reconnect", val)
        settings_manager.set_setting("force_restart", val)
        if hasattr(self, "force_restart_var"):
            try:
                self.force_restart_var.set(val)
            except Exception:
                pass
        if not val:
            for inst in self.instances:
                inst._cancel_reconnect()
        self.save_all()

    def _on_force_restart_toggled(self):
        self._on_options_auto_reconnect_toggled()

    # ── Proxies Tab & Pagination ──────────────────────────────
    def _build_proxies_tab(self):
        bg = C["bg"]
        px = 10

        # Pagination Bar
        self.pagination_bar = tk.Frame(self.proxies_frame, bg=C["surface"], height=38)
        self.pagination_bar.pack(fill=tk.X)

        self.prev_page_btn = tk.Button(
            self.pagination_bar, text=t("pagination_prev"), font=("Segoe UI", 9, "bold"),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT,
            padx=10, pady=2, command=self._prev_page,
        )
        self.prev_page_btn.pack(side=tk.LEFT, padx=(10, 6), pady=6)

        self.page_info_label = tk.Label(
            self.pagination_bar, text="", font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["surface"],
        )
        self.page_info_label.pack(side=tk.LEFT, padx=8)

        self.next_page_btn = tk.Button(
            self.pagination_bar, text=t("pagination_next"), font=("Segoe UI", 9, "bold"),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT,
            padx=10, pady=2, command=self._next_page,
        )
        self.next_page_btn.pack(side=tk.LEFT, padx=6, pady=6)

        # Sub-notebook for the 10 instances of the current page
        self.sub_notebook = ttk.Notebook(self.proxies_frame)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True, padx=px, pady=(6, 10))

        self.refresh_current_page_tabs()

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_current_page_tabs()

    def _next_page(self):
        total_pages = max(1, (len(self.instances) + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self.refresh_current_page_tabs()

    def refresh_current_page_tabs(self):
        """Render the 10 instance tabs for the current page."""
        for tab_id in self.sub_notebook.tabs():
            self.sub_notebook.forget(tab_id)

        total_inst = len(self.instances)
        total_pages = max(1, (total_inst + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.current_page >= total_pages:
            self.current_page = total_pages - 1

        start_idx = self.current_page * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, total_inst)

        self.page_info_label.config(
            text=t("pagination_info", page=self.current_page + 1, pages=total_pages, start=start_idx + 1 if total_inst else 0, end=end_idx, total=total_inst)
        )
        self.prev_page_btn.config(state=tk.NORMAL if self.current_page > 0 else tk.DISABLED)
        self.next_page_btn.config(state=tk.NORMAL if self.current_page < total_pages - 1 else tk.DISABLED)

        for i in range(start_idx, end_idx):
            inst = self.instances[i]
            frame = inst.build_tab_ui(self.sub_notebook)
            _, port = inst.get_listen()
            name = inst.get_display_name()
            order_str = format_order(inst.get_order())
            flag = inst.geo_info.get("flag", "🌐")
            tab_label = f" {flag} #{order_str} {name[:12]}:{port} "
            self.sub_notebook.add(frame, text=tab_label)

    # ── Options Tab (NEW) ─────────────────────────────────────
    def _build_options_tab(self):
        bg = C["bg"]
        px = 16

        opt_canvas = tk.Canvas(self.options_frame, bg=bg, highlightthickness=0)
        opt_scrollbar = ttk.Scrollbar(self.options_frame, orient="vertical", command=opt_canvas.yview)
        opt_content = tk.Frame(opt_canvas, bg=bg)

        opt_content.bind("<Configure>", lambda e: opt_canvas.configure(scrollregion=opt_canvas.bbox("all")))
        opt_canvas.create_window((0, 0), window=opt_content, anchor="nw", width=760)
        opt_canvas.configure(yscrollcommand=opt_scrollbar.set)

        opt_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=px, pady=10)
        opt_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Label(opt_content, text=t("options_title"), font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 2))
        tk.Label(opt_content, text=t("options_subtitle"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).pack(anchor="w", pady=(0, 14))

        # 0. Proxy Launchers & Shortcuts Automation (FIRST ITEM)
        card_launchers = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card_launchers.pack(fill=tk.X, pady=(0, 8), padx=2)

        launchers_top = tk.Frame(card_launchers, bg=C["card"])
        launchers_top.pack(fill=tk.X, padx=12, pady=(10, 4))

        tk.Label(
            launchers_top, text=f"  {t('lbl_launchers_guide_title')}",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"]
        ).pack(side=tk.LEFT, anchor="w")

        btn_box = tk.Frame(launchers_top, bg=C["card"])
        btn_box.pack(side=tk.RIGHT)

        btn_guide = tk.Button(
            btn_box, text=t("btn_launchers_guide"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
            command=self.open_launchers_guide_modal,
        )
        btn_guide.pack(side=tk.LEFT, padx=(0, 6))

        btn_quick = tk.Button(
            btn_box, text=t("btn_quick_generate_launchers"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=10, pady=4, cursor="hand2",
            command=lambda: self.run_quick_launcher_generation(),
        )
        btn_quick.pack(side=tk.LEFT)

        tk.Label(
            card_launchers, text=t("lbl_launchers_guide_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 10))

        # 1. Force Port Takeover
        card1 = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card1.pack(fill=tk.X, pady=6, padx=2)

        self.force_port_var = tk.BooleanVar(value=settings_manager.get_setting("force_port_takeover", True))
        cb1 = tk.Checkbutton(
            card1, text=f"  {t('opt_force_port_takeover_title')}", variable=self.force_port_var,
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"],
            command=lambda: settings_manager.set_setting("force_port_takeover", self.force_port_var.get()),
        )
        cb1.pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            card1, text=t("opt_force_port_takeover_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 10))

        # 2. Auto-Start Proxies on Launch
        card2 = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card2.pack(fill=tk.X, pady=6, padx=2)

        self.autostart_var = tk.BooleanVar(value=settings_manager.get_setting("autostart_proxies", True))
        cb2 = tk.Checkbutton(
            card2, text=f"  {t('opt_autostart_proxies_title')}", variable=self.autostart_var,
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"],
            command=lambda: settings_manager.set_setting("autostart_proxies", self.autostart_var.get()),
        )
        cb2.pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            card2, text=t("opt_autostart_proxies_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 10))

        # 3. Start in Tray
        card3 = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card3.pack(fill=tk.X, pady=6, padx=2)

        self.start_tray_var = tk.BooleanVar(value=settings_manager.get_setting("start_minimized_tray", True))
        cb3 = tk.Checkbutton(
            card3, text=f"  {t('opt_start_minimized_tray_title')}", variable=self.start_tray_var,
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"],
            command=lambda: settings_manager.set_setting("start_minimized_tray", self.start_tray_var.get()),
        )
        cb3.pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            card3, text=t("opt_start_minimized_tray_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 10))

        # 4. Auto-Reconnect on Failure (Watchdog)
        card4 = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card4.pack(fill=tk.X, pady=6, padx=2)

        self.auto_reconnect_var = tk.BooleanVar(value=settings_manager.get_setting("auto_reconnect", True))
        cb4 = tk.Checkbutton(
            card4, text=f"  {t('opt_auto_reconnect_title')}", variable=self.auto_reconnect_var,
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"],
            command=self._on_options_auto_reconnect_toggled,
        )
        cb4.pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            card4, text=t("opt_auto_reconnect_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 6))

        # Reconnect intervals row
        int_row = tk.Frame(card4, bg=C["card"])
        int_row.pack(fill=tk.X, padx=36, pady=(2, 4))

        tk.Label(
            int_row, text=t("opt_reconnect_intervals_lbl"), font=("Segoe UI", 9, "bold"),
            fg=C["text"], bg=C["card"]
        ).pack(side=tk.LEFT, padx=(0, 8))

        curr_intervals = settings_manager.get_setting("reconnect_intervals", settings_manager.DEFAULT_RECONNECT_INTERVALS)
        self.intervals_entry = tk.Entry(
            int_row, font=("Consolas", 10), width=32,
            bg=C["overlay"], fg=C["text"], insertbackground=C["text"],
            relief=tk.FLAT, borderwidth=4,
        )
        self.intervals_entry.pack(side=tk.LEFT, padx=(0, 8))
        self.intervals_entry.insert(0, str(curr_intervals))
        attach_clipboard_and_context_menu(self.intervals_entry)

        def _on_intervals_change(event=None):
            val = self.intervals_entry.get().strip()
            settings_manager.set_setting("reconnect_intervals", val)

        self.intervals_entry.bind("<KeyRelease>", _on_intervals_change)

        def _reset_intervals():
            self.intervals_entry.delete(0, tk.END)
            self.intervals_entry.insert(0, settings_manager.DEFAULT_RECONNECT_INTERVALS)
            settings_manager.set_setting("reconnect_intervals", settings_manager.DEFAULT_RECONNECT_INTERVALS)

        reset_btn = tk.Button(
            int_row, text=t("btn_reset_intervals"), font=("Segoe UI", 8, "bold"),
            bg=C["overlay"], fg=C["yellow"], activebackground=C["hover"], relief=tk.FLAT,
            padx=8, pady=2, command=_reset_intervals,
        )
        reset_btn.pack(side=tk.LEFT)

        tk.Label(
            card4, text=t("opt_reconnect_intervals_desc"), font=("Segoe UI", 8),
            fg=C["muted"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 10))

        # Footer note
        tk.Label(
            opt_content, text=t("opt_saved_hint"), font=("Segoe UI", 9, "bold"),
            fg=C["green"], bg=bg,
        ).pack(anchor="w", pady=(14, 4), padx=4)

    def run_quick_launcher_generation(self, status_lbl=None):
        """Execute launcher generation in background thread."""
        def _task():
            try:
                from vless2socks.ink import generate_proxy_workspace
                target_proxy_dir = r"C:\MyFiles\Proxy"
                res = generate_proxy_workspace(
                    target_dir=target_proxy_dir,
                    instances_path=INSTANCES_FILE if INSTANCES_FILE.exists() else None,
                    include_xshell=True,
                    xshell_port=1030,
                    update_desktop=False,
                )
                msg = t("launchers_generated_success", path=res.get("proxy_dir", target_proxy_dir))
                def _update_ok():
                    try:
                        if status_lbl and status_lbl.winfo_exists():
                            status_lbl.config(text=f"✅ {msg}", fg=C["green"])
                    except Exception:
                        pass
                def _safe_after(callback):
                    try:
                        self.after(0, callback)
                    except Exception:
                        pass

                _safe_after(_update_ok)
                _safe_after(lambda: messagebox.showinfo("vless2socks", msg, parent=self))
            except Exception as e:
                err_msg = f"Ошибка генерации: {e}"
                def _update_err():
                    try:
                        if status_lbl and status_lbl.winfo_exists():
                            status_lbl.config(text=f"❌ {err_msg}", fg=C["red"])
                    except Exception:
                        pass
                def _safe_after_err(callback):
                    try:
                        self.after(0, callback)
                    except Exception:
                        pass

                _safe_after_err(_update_err)
                _safe_after_err(lambda: messagebox.showerror("vless2socks", err_msg, parent=self))

        if status_lbl and status_lbl.winfo_exists():
            status_lbl.config(text=f"⏳ {t('launchers_generating')}", fg=C["yellow"])
        threading.Thread(target=_task, daemon=True).start()

    def open_launchers_guide_modal(self):
        """Open modal window with instructions, Antigravity prompts, and automation."""
        dlg = tk.Toplevel(self)
        dlg.title(t("dlg_launchers_title"))
        dlg.geometry("780x740")
        dlg.minsize(680, 560)
        dlg.configure(bg=C["bg"])
        dlg.transient(self)
        dlg.grab_set()

        try:
            x = self.winfo_x() + (self.winfo_width() - 780) // 2
            y = self.winfo_y() + (self.winfo_height() - 740) // 2
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        # Top Action Bar inside dialog
        top_bar = tk.Frame(dlg, bg=C["surface"], padx=16, pady=12)
        top_bar.pack(fill=tk.X)

        title_lbl = tk.Label(
            top_bar, text="🛠️ Как создать батники и ярлыки (.bat / .lnk / .ico)",
            font=("Segoe UI", 12, "bold"), fg=C["text"], bg=C["surface"]
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            top_bar,
            text="Иерархия Proxy (bat, ico, ink), модуль vless2socks.ink и скилл proxy-launcher-generator для Antigravity",
            font=("Segoe UI", 8), fg=C["subtext"], bg=C["surface"]
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Bottom control bar
        bottom_bar = tk.Frame(dlg, bg=C["surface"], padx=16, pady=10)
        bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)

        status_lbl = tk.Label(bottom_bar, text="", font=("Segoe UI", 8), fg=C["subtext"], bg=C["surface"])
        status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _open_folder():
            folder = r"C:\MyFiles\Proxy"
            Path(folder).mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(["explorer", folder])
            except Exception as e:
                messagebox.showerror("Error", f"Не удалось открыть папку: {e}", parent=dlg)

        open_folder_btn = tk.Button(
            bottom_bar, text=t("btn_open_proxy_dir"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=10, pady=4, cursor="hand2", command=_open_folder
        )
        open_folder_btn.pack(side=tk.RIGHT, padx=(8, 0))

        gen_btn = tk.Button(
            bottom_bar, text=t("btn_quick_generate_launchers"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
            command=lambda: self.run_quick_launcher_generation(status_lbl=status_lbl)
        )
        gen_btn.pack(side=tk.RIGHT, padx=(8, 0))

        close_btn = tk.Button(
            bottom_bar, text="Закрыть", font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["subtext"], activebackground=C["hover"], activeforeground=C["text"],
            relief=tk.FLAT, padx=10, pady=4, cursor="hand2", command=dlg.destroy
        )
        close_btn.pack(side=tk.RIGHT)

        # Scrollable container for instructions and prompts
        canvas = tk.Canvas(dlg, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        scroll_content = tk.Frame(canvas, bg=C["bg"])

        scroll_content.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_window = canvas.create_window((0, 0), window=scroll_content, anchor="nw", width=744)

        def _on_canvas_configure(e):
            canvas.itemconfig(canvas_window, width=e.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        dlg.bind_all("<MouseWheel>", _on_mousewheel)
        dlg.protocol("WM_DELETE_WINDOW", lambda: (dlg.unbind_all("<MouseWheel>"), dlg.destroy()))

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=8)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Section 1: Архитектура и правила
        sec1 = tk.Frame(scroll_content, bg=C["card"], padx=14, pady=12, relief=tk.FLAT)
        sec1.pack(fill=tk.X, pady=(4, 10))

        tk.Label(
            sec1, text="📁 Архитектура папки C:\\MyFiles\\Proxy",
            font=("Segoe UI", 10, "bold"), fg=C["blue"], bg=C["card"]
        ).pack(anchor="w")

        arch_text = (
            "• Корневая папка (Proxy): содержит исполняемые .bat файлы (Chrome_Socket_<PORT>.bat, Xshell_Socket_<PORT>.bat).\n"
            "• Подпапка ico/: содержит цветные многослойные иконки (256, 128, 64, 48, 32, 24, 16 px) без смазывания и искажений масштаба Windows.\n"
            "• Подпапка ink/: содержит Windows-ярлыки (.lnk) с привязкой к батникам и цветным иконкам.\n"
            "• Алиасы link и lnk: символические связи (NTFS Junctions) на ink, чтобы исключить опечатки.\n"
            "• Изоляция профилей Chrome: ключ --user-data-dir гарантирует, что вкладка откроется в отдельной сессии под своим прокси."
        )
        tk.Label(
            sec1, text=arch_text, font=("Segoe UI", 9), fg=C["text"], bg=C["card"],
            justify=tk.LEFT, wraplength=700
        ).pack(anchor="w", pady=(6, 0))

        # Section 2: Последовательность запросов к Antigravity
        sec2_header = tk.Frame(scroll_content, bg=C["bg"])
        sec2_header.pack(fill=tk.X, pady=(6, 4))
        tk.Label(
            sec2_header, text="🤖 Последовательность запросов (промптов) к Antigravity",
            font=("Segoe UI", 11, "bold"), fg=C["text"], bg=C["bg"]
        ).pack(anchor="w")
        tk.Label(
            sec2_header,
            text="Скопируйте нужный промпт одной кнопкой. Промпты учитывают наличие модуля vless2socks.ink и скилла proxy-launcher-generator.",
            font=("Segoe UI", 8), fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        prompts = [
            (
                "Шаг 1: Полная генерация батников и ярлыков для всех прокси",
                "Генерирует батники и цветные multi-res иконки для всех профилей из vless2socks (Chrome + Xshell) и размещает ярлыки в ink/ с алиасами link/lnk.",
                "Используй скилл proxy-launcher-generator и модуль vless2socks.ink: сгенерируй батники для всех сокетов из C:\\MyFiles\\vless2socks\\instances.json в папку C:\\MyFiles\\Proxy, извлеки и перекрась оригинальные multi-res иконки в ico/, создай ярлыки в ink/ с алиасами link и lnk, и добавь лаунчер Xshell на порт 1030."
            ),
            (
                "Шаг 2: Добавление нового сокета или лаунчера под отдельный порт",
                "Если вы создали новый прокси-профиль в vless2socks и хотите сгенерировать под него отдельный батник и ярлык нужного цвета.",
                "Используй модуль vless2socks.ink: создай батник и ярлык для сокета 1084 с бирюзовой иконкой в папке C:\\MyFiles\\Proxy (ярлык в ink/) с помощью функций build_chrome_bat и generate_app_icon."
            ),
            (
                "Шаг 3: Настройка запуска Xshell через сокет 1030 с бирюзовой иконкой",
                "Создает батник запуска Xshell 8 через сокет 1030, перекрашивает оранжевую иконку в бирюзовую и обновляет SOCKS5 конфигурацию в документах NetSarang.",
                "Настрой Xshell через сокет 1030 с помощью модуля vless2socks.ink: создай Xshell_Socket_1030.bat в C:\\MyFiles\\Proxy, бирюзовую иконку xshell_cyan.ico в ico/, ярлык в ink/ и обнови Socket1030.ini в документах NetSarang."
            ),
            (
                "Шаг 4: Вынос ярлыков на рабочий стол Windows (Desktop)",
                "Отправляет готовые ярлыки из подпапки ink на рабочий стол с обновлением системного кэша иконок проводника Windows.",
                "Скопируй ярлыки Chrome Socket 1015 и Xshell Socket 1030 из C:\\MyFiles\\Proxy\\ink на рабочий стол Windows (Desktop) и выполни сброс кэша иконок через ie4uinit.exe -show."
            ),
        ]

        for step_title, step_desc, prompt_text in prompts:
            p_card = tk.Frame(scroll_content, bg=C["card"], padx=12, pady=10, relief=tk.FLAT)
            p_card.pack(fill=tk.X, pady=4)

            p_top = tk.Frame(p_card, bg=C["card"])
            p_top.pack(fill=tk.X)

            tk.Label(
                p_top, text=step_title, font=("Segoe UI", 9, "bold"),
                fg=C["blue"], bg=C["card"]
            ).pack(side=tk.LEFT, anchor="w")

            def _make_copy_cmd(txt, btn_ref):
                def _copy():
                    try:
                        self.clipboard_clear()
                        self.clipboard_append(txt)
                        orig_text = btn_ref.cget("text")
                        btn_ref.config(text=t("btn_prompt_copied"), bg=C["green"], fg="#1e1e2e")
                        self.after(1800, lambda: btn_ref.config(text=orig_text, bg=C["overlay"], fg=C["text"]))
                    except Exception as ex:
                        messagebox.showerror("Clipboard", f"Ошибка копирования: {ex}", parent=dlg)
                return _copy

            copy_btn = tk.Button(
                p_top, text=t("btn_copy_prompt"), font=("Segoe UI", 8, "bold"),
                bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
                relief=tk.FLAT, padx=10, pady=2, cursor="hand2"
            )
            copy_btn.pack(side=tk.RIGHT)
            copy_btn.config(command=_make_copy_cmd(prompt_text, copy_btn))

            tk.Label(
                p_card, text=step_desc, font=("Segoe UI", 8),
                fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT
            ).pack(anchor="w", pady=(2, 4))

            # Prompt text box
            box = tk.Frame(p_card, bg=C["surface"], padx=8, pady=6)
            box.pack(fill=tk.X)
            prompt_lbl = tk.Label(
                box, text=prompt_text, font=("Consolas", 8),
                fg=C["teal"], bg=C["surface"], justify=tk.LEFT, wraplength=680
            )
            prompt_lbl.pack(anchor="w")

    # ── Backup & Security Tab ─────────────────────────────────
    def _build_backup_tab(self):
        bg = C["bg"]
        px = 16

        b_canvas = tk.Canvas(self.backup_frame, bg=bg, highlightthickness=0)
        b_scrollbar = ttk.Scrollbar(self.backup_frame, orient="vertical", command=b_canvas.yview)
        b_content = tk.Frame(b_canvas, bg=bg)

        b_content.bind("<Configure>", lambda e: b_canvas.configure(scrollregion=b_canvas.bbox("all")))
        b_canvas.create_window((0, 0), window=b_content, anchor="nw", width=760)
        b_canvas.configure(yscrollcommand=b_scrollbar.set)

        b_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=px, pady=10)
        b_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Label(b_content, text=t("backup_title"), font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 2))
        tk.Label(b_content, text=t("backup_subtitle"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).pack(anchor="w", pady=(0, 10))

        # 1. Master Password Section
        sec1 = tk.LabelFrame(b_content, text=f"  {t('master_pwd_title')}  ", font=("Segoe UI", 9, "bold"), fg=C["blue"], bg=C["card"], relief=tk.GROOVE)
        sec1.pack(fill=tk.X, pady=6, padx=2)

        tk.Label(sec1, text=t("master_pwd_desc"), font=("Segoe UI", 8), fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT).pack(anchor="w", padx=10, pady=4)

        pwd_row = tk.Frame(sec1, bg=C["card"])
        pwd_row.pack(fill=tk.X, padx=10, pady=6)

        tk.Label(pwd_row, text=t("lbl_master_pwd"), font=("Segoe UI", 9), fg=C["text"], bg=C["card"]).pack(side=tk.LEFT, padx=(0, 6))

        self.backup_pwd_entry = tk.Entry(
            pwd_row, font=("Consolas", 10), width=28, bg=C["overlay"], fg=C["text"],
            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4, show="•"
        )
        self.backup_pwd_entry.pack(side=tk.LEFT, padx=(0, 6))
        saved_pwd = backup_manager.load_backup_password()
        if saved_pwd:
            self.backup_pwd_entry.insert(0, saved_pwd)

        self._backup_pwd_visible = False
        self.eye_pwd_btn = tk.Button(
            pwd_row, text="👁️", font=("Segoe UI", 9), bg=C["overlay"], fg=C["text"],
            relief=tk.FLAT, padx=6, pady=2, command=self._toggle_backup_pwd_visibility,
        )
        self.eye_pwd_btn.pack(side=tk.LEFT, padx=(0, 8))

        save_pwd_btn = tk.Button(
            pwd_row, text=t("btn_save_pwd"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"], relief=tk.FLAT, padx=10, pady=2,
            command=self._save_master_pwd,
        )
        save_pwd_btn.pack(side=tk.LEFT)

        fp_row = tk.Frame(sec1, bg=C["card"])
        fp_row.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Label(fp_row, text=t("lbl_pwd_fingerprint"), font=("Segoe UI", 8), fg=C["muted"], bg=C["card"]).pack(side=tk.LEFT, padx=(0, 6))
        self.fp_label = tk.Label(
            fp_row, text=backup_manager.compute_password_fingerprint(saved_pwd) or "—",
            font=("Consolas", 9, "bold"), fg=C["green"] if saved_pwd else C["muted"], bg=C["card"]
        )
        self.fp_label.pack(side=tk.LEFT)

        self.backup_pwd_entry.bind("<KeyRelease>", lambda e: self._on_pwd_key())

        # 2. Backup Folder & Auto-Backup Settings
        sec2 = tk.LabelFrame(b_content, text=f"  {t('lbl_backup_folder')} & {t('lbl_auto_backup')}  ", font=("Segoe UI", 9, "bold"), fg=C["blue"], bg=C["card"], relief=tk.GROOVE)
        sec2.pack(fill=tk.X, pady=6, padx=2)

        # Directory selection row
        dir_row = tk.Frame(sec2, bg=C["card"])
        dir_row.pack(fill=tk.X, padx=10, pady=(8, 4))

        tk.Label(dir_row, text=t("lbl_backup_folder"), font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["card"]).pack(side=tk.LEFT, padx=(0, 6))

        curr_b_dir = str(backup_manager.get_backup_dir())
        self.backup_dir_entry = tk.Entry(
            dir_row, font=("Consolas", 9), bg=C["overlay"], fg=C["text"],
            insertbackground=C["text"], relief=tk.FLAT, borderwidth=4,
        )
        self.backup_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.backup_dir_entry.insert(0, curr_b_dir)
        attach_clipboard_and_context_menu(self.backup_dir_entry)

        browse_btn = tk.Button(
            dir_row, text=t("btn_browse_folder"), font=("Segoe UI", 8, "bold"),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=10, pady=2,
            command=self._choose_backup_dir,
        )
        browse_btn.pack(side=tk.RIGHT)

        # Auto-backup toggle & interval
        auto_b_row = tk.Frame(sec2, bg=C["card"])
        auto_b_row.pack(fill=tk.X, padx=10, pady=(4, 2))

        self.auto_backup_var = tk.BooleanVar(value=settings_manager.get_setting("auto_backup_enabled", False))
        cb_ab = tk.Checkbutton(
            auto_b_row, text=f"  {t('lbl_auto_backup')}", variable=self.auto_backup_var,
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"],
            command=lambda: settings_manager.set_setting("auto_backup_enabled", self.auto_backup_var.get()),
        )
        cb_ab.pack(side=tk.LEFT)

        # Hours interval input
        int_frame = tk.Frame(auto_b_row, bg=C["card"])
        int_frame.pack(side=tk.LEFT, padx=(16, 0))

        tk.Label(int_frame, text=t("lbl_backup_interval"), font=("Segoe UI", 8, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT, padx=(0, 4))
        self.backup_interval_entry = tk.Entry(
            int_frame, font=("Consolas", 9), width=5, bg=C["overlay"], fg=C["text"],
            insertbackground=C["text"], relief=tk.FLAT, borderwidth=3,
        )
        self.backup_interval_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.backup_interval_entry.insert(0, str(settings_manager.get_setting("backup_interval_hours", 24)))
        self.backup_interval_entry.bind(
            "<KeyRelease>",
            lambda e: settings_manager.set_setting("backup_interval_hours", self.backup_interval_entry.get().strip() or "24")
        )

        last_b_time = settings_manager.get_setting("last_backup_time", "-")
        self.last_backup_time_lbl = tk.Label(
            auto_b_row, text=t("lbl_last_backup_time", time=last_b_time),
            font=("Segoe UI", 8), fg=C["green"] if last_b_time != "-" else C["muted"], bg=C["card"]
        )
        self.last_backup_time_lbl.pack(side=tk.LEFT, padx=6)

        tk.Label(
            sec2, text=t("lbl_auto_backup_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=32, pady=(0, 6))

        # Action buttons row
        btn_action_row = tk.Frame(sec2, bg=C["card"])
        btn_action_row.pack(anchor="w", padx=10, pady=(4, 10))

        tk.Button(
            btn_action_row, text=t("btn_export_hbak"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"], relief=tk.FLAT, padx=14, pady=4,
            command=self._export_backup,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_action_row, text=t("btn_restore_hbak"), font=("Segoe UI", 9, "bold"),
            bg=C["yellow"], fg="#1e1e2e", activebackground="#f9e2af", relief=tk.FLAT, padx=14, pady=4,
            command=self._restore_backup,
        ).pack(side=tk.LEFT)

        # 3. Available Snapshots in Folder Section
        sec3 = tk.LabelFrame(b_content, text=f"  {t('lbl_available_backups')}  ", font=("Segoe UI", 9, "bold"), fg=C["green"], bg=C["card"], relief=tk.GROOVE)
        sec3.pack(fill=tk.X, pady=6, padx=2)

        sec3_top = tk.Frame(sec3, bg=C["card"])
        sec3_top.pack(fill=tk.X, padx=10, pady=(6, 4))

        tk.Label(sec3_top, text=t("lbl_available_backups"), font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["card"]).pack(side=tk.LEFT)
        tk.Button(
            sec3_top, text=t("btn_refresh_backups"), font=("Segoe UI", 8),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=8, pady=2,
            command=self._render_available_backups,
        ).pack(side=tk.RIGHT)

        self.backup_list_container = tk.Frame(sec3, bg=C["card"])
        self.backup_list_container.pack(fill=tk.X, padx=10, pady=(2, 8))

        # 4. Danger Zone Section
        sec4 = tk.LabelFrame(b_content, text=f"  {t('danger_title')}  ", font=("Segoe UI", 9, "bold"), fg=C["red"], bg=C["card"], relief=tk.GROOVE)
        sec4.pack(fill=tk.X, pady=8, padx=2)

        tk.Label(sec4, text=t("danger_desc"), font=("Segoe UI", 8), fg=C["red"], bg=C["card"], wraplength=700, justify=tk.LEFT).pack(anchor="w", padx=10, pady=4)

        tk.Button(
            sec4, text=t("btn_wipe_all"), font=("Segoe UI", 9, "bold"),
            bg=C["red"], fg="#1e1e2e", activebackground="#eba0ac", relief=tk.FLAT, padx=14, pady=4,
            command=self._danger_wipe_all,
        ).pack(anchor="w", padx=10, pady=(4, 10))

        # Initial render of available backups
        self._render_available_backups()

    def _choose_backup_dir(self):
        curr = self.backup_dir_entry.get().strip() or str(backup_manager.DEFAULT_BACKUP_DIR)
        d = filedialog.askdirectory(initialdir=curr, title=t("lbl_backup_folder"))
        if d:
            backup_manager.set_backup_dir(d)
            self.backup_dir_entry.delete(0, tk.END)
            self.backup_dir_entry.insert(0, d)
            self._render_available_backups()

    def _render_available_backups(self):
        for w in self.backup_list_container.winfo_children():
            w.destroy()

        b_dir = self.backup_dir_entry.get().strip() if hasattr(self, "backup_dir_entry") else str(backup_manager.get_backup_dir())
        backups = backup_manager.list_backups_in_dir(b_dir)

        if not backups:
            e_row = tk.Frame(self.backup_list_container, bg=C["card"])
            e_row.pack(fill=tk.X, pady=12)
            tk.Label(
                e_row, text=t("empty_backups_list"),
                font=("Segoe UI", 9), fg=C["subtext"], bg=C["card"]
            ).pack()
            return

        for idx, b in enumerate(backups[:12]):
            row_bg = C["card"] if idx % 2 == 0 else C["overlay"]
            row = tk.Frame(self.backup_list_container, bg=row_bg, height=32)
            row.pack(fill=tk.X, pady=1)

            tk.Label(
                row, text=f"📦 {b['filename']}", font=("Consolas", 9, "bold"),
                fg=C["text"], bg=row_bg, anchor="w"
            ).pack(side=tk.LEFT, padx=8)

            tk.Label(
                row, text=f"{b['size_kb']} KB", font=("Segoe UI", 8),
                fg=C["subtext"], bg=row_bg, width=10, anchor="center"
            ).pack(side=tk.LEFT, padx=4)

            tk.Label(
                row, text=b["modified"], font=("Segoe UI", 8),
                fg=C["subtext"], bg=row_bg, width=18, anchor="w"
            ).pack(side=tk.LEFT, padx=4)

            btn_rest = tk.Button(
                row, text=t("btn_restore_this"), font=("Segoe UI", 8, "bold"),
                bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
                relief=tk.FLAT, padx=8, pady=1,
                command=lambda fp=b["filepath"]: self._restore_specific_backup(fp),
            )
            btn_rest.pack(side=tk.RIGHT, padx=8)

    def _restore_specific_backup(self, filepath: str):
        pwd = self.backup_pwd_entry.get().strip()
        if not pwd:
            pwd = simpledialog.askstring("vless2socks", t("lbl_master_pwd"), show="•")
            if not pwd:
                return

        try:
            count = backup_manager.restore_encrypted_backup(filepath, pwd)
            self.stop_all()
            self._load_saved_data()
            self.current_page = 0
            if hasattr(self, "force_restart_var"):
                self.force_restart_var.set(settings_manager.get_setting("force_restart", True))
            if hasattr(self, "auto_reconnect_var"):
                self.auto_reconnect_var.set(settings_manager.get_setting("auto_reconnect", True))
            self.refresh_current_page_tabs()
            self.refresh_overview()
            self._update_header_stats()
            messagebox.showinfo("vless2socks", t("restore_success", count=count))
        except Exception as e:
            messagebox.showerror("vless2socks", f"{t('restore_bad_pwd')}\n({e})")

    def _toggle_backup_pwd_visibility(self):
        self._backup_pwd_visible = not self._backup_pwd_visible
        if self._backup_pwd_visible:
            self.backup_pwd_entry.config(show="")
            self.eye_pwd_btn.config(text="🙈")
        else:
            self.backup_pwd_entry.config(show="•")
            self.eye_pwd_btn.config(text="👁️")

    def _on_pwd_key(self):
        pwd = self.backup_pwd_entry.get().strip()
        fp = backup_manager.compute_password_fingerprint(pwd)
        self.fp_label.config(text=fp or "—", fg=C["green"] if fp else C["muted"])

    def _save_master_pwd(self):
        pwd = self.backup_pwd_entry.get().strip()
        if not pwd:
            messagebox.showwarning("vless2socks", t("pwd_empty_warning"))
            return
        backup_manager.save_backup_password(pwd)
        self._on_pwd_key()
        messagebox.showinfo("vless2socks", t("pwd_saved_msg"))

    def _export_backup(self):
        pwd = self.backup_pwd_entry.get().strip()
        if not pwd:
            pwd = simpledialog.askstring("vless2socks", t("lbl_master_pwd"), show="•")
            if not pwd:
                return

        self.save_all()
        target_dir = Path(self.backup_dir_entry.get().strip()) if hasattr(self, "backup_dir_entry") else backup_manager.get_backup_dir()
        try:
            out_file = backup_manager.export_encrypted_backup(pwd, dest_dir=target_dir)
            self._render_available_backups()
            if hasattr(self, "last_backup_time_lbl"):
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                self.last_backup_time_lbl.config(text=t("lbl_last_backup_time", time=now_str), fg=C["green"])
            messagebox.showinfo("vless2socks", t("export_success", path=str(out_file)))
        except Exception as e:
            messagebox.showerror("vless2socks", f"Export failed: {e}")

    def _restore_backup(self):
        curr_dir = self.backup_dir_entry.get().strip() if hasattr(self, "backup_dir_entry") else str(backup_manager.get_backup_dir())
        path = filedialog.askopenfilename(
            initialdir=curr_dir,
            title=t("btn_restore_hbak"),
            filetypes=[("Herdr / Vless Backup", "*.hbak"), ("All Files", "*.*")],
        )
        if not path:
            return

        self._restore_specific_backup(path)

    def _danger_wipe_all(self):
        if not messagebox.askyesno(t("danger_title"), t("danger_confirm_1")):
            return

        backup_manager.wipe_all_sensitive_data(stop_all_callback=self.stop_all)
        self._load_saved_data()
        self.current_page = 0
        self.refresh_current_page_tabs()
        self.refresh_overview()
        self._update_header_stats()
        self.backup_pwd_entry.delete(0, tk.END)
        self.fp_label.config(text="—", fg=C["muted"])
        self._render_available_backups()
        messagebox.showinfo("vless2socks", t("danger_wiped_msg"))

    # ── WSL Isolation Guard Tab ──────────────────────────────
    def _build_wsl_isolation_tab(self):
        bg = C["bg"]
        px = 16

        w_canvas = tk.Canvas(self.wsl_iso_frame, bg=bg, highlightthickness=0)
        w_scrollbar = ttk.Scrollbar(self.wsl_iso_frame, orient="vertical", command=w_canvas.yview)
        w_content = tk.Frame(w_canvas, bg=bg)

        w_content.bind("<Configure>", lambda e: w_canvas.configure(scrollregion=w_canvas.bbox("all")))
        w_canvas.create_window((0, 0), window=w_content, anchor="nw", width=760)
        w_canvas.configure(yscrollcommand=w_scrollbar.set)

        w_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=px, pady=10)
        w_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Label(w_content, text=t("wsl_title"), font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 2))
        tk.Label(w_content, text=t("wsl_subtitle"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg, wraplength=740, justify=tk.LEFT).pack(anchor="w", pady=(0, 10))

        # Status and Controls Card
        card = tk.LabelFrame(w_content, text="  Состояние и параметры сетевого контура  ", font=("Segoe UI", 9, "bold"), fg=C["blue"], bg=C["card"], relief=tk.GROOVE)
        card.pack(fill=tk.X, pady=6, padx=2)

        # Row 1: WSL status & Kernel status
        r1 = tk.Frame(card, bg=C["card"])
        r1.pack(fill=tk.X, padx=12, pady=6)

        tk.Label(r1, text=t("wsl_lbl_status"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_status_lbl = tk.Label(r1, text="Проверка...", font=("Segoe UI", 9), fg=C["yellow"], bg=C["card"])
        self.wsl_status_lbl.pack(side=tk.LEFT, padx=(6, 24))

        tk.Label(r1, text=t("wsl_lbl_kernel"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_kernel_lbl = tk.Label(r1, text="Проверка...", font=("Segoe UI", 9), fg=C["yellow"], bg=C["card"])
        self.wsl_kernel_lbl.pack(side=tk.LEFT, padx=(6, 0))

        # Row 2: Port selector & reachability
        r2 = tk.Frame(card, bg=C["card"])
        r2.pack(fill=tk.X, padx=12, pady=6)

        tk.Label(r2, text=t("wsl_lbl_port"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_port_var = tk.StringVar(value="1015")
        self.wsl_port_ent = tk.Entry(r2, textvariable=self.wsl_port_var, font=("Consolas", 10), width=8, bg=C["overlay"], fg=C["text"], insertbackground=C["text"], relief=tk.FLAT)
        self.wsl_port_ent.pack(side=tk.LEFT, padx=(6, 12))

        tk.Label(r2, text=t("wsl_lbl_port_status"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_port_status_lbl = tk.Label(r2, text="—", font=("Segoe UI", 9), fg=C["muted"], bg=C["card"])
        self.wsl_port_status_lbl.pack(side=tk.LEFT, padx=(6, 20))

        tk.Button(
            r2, text="Взять активный порт прокси", font=("Segoe UI", 8),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=6, pady=1,
            command=self._wsl_pick_active_port,
        ).pack(side=tk.LEFT)

        # Row 3: DNS delegation & IP leak status
        r3 = tk.Frame(card, bg=C["card"])
        r3.pack(fill=tk.X, padx=12, pady=6)

        tk.Label(r3, text=t("wsl_lbl_dns"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_dns_lbl = tk.Label(r3, text="—", font=("Segoe UI", 9), fg=C["muted"], bg=C["card"])
        self.wsl_dns_lbl.pack(side=tk.LEFT, padx=(6, 24))

        tk.Label(r3, text=t("wsl_lbl_leak"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=C["card"]).pack(side=tk.LEFT)
        self.wsl_leak_lbl = tk.Label(r3, text="—", font=("Segoe UI", 9), fg=C["muted"], bg=C["card"])
        self.wsl_leak_lbl.pack(side=tk.LEFT, padx=(6, 0))

        # Action Buttons Bar
        btn_bar = tk.Frame(w_content, bg=bg)
        btn_bar.pack(fill=tk.X, pady=(10, 8))

        self.wsl_btn_apply = tk.Button(
            btn_bar, text=t("btn_wsl_apply"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"], relief=tk.FLAT, padx=12, pady=5,
            command=self._wsl_apply_isolation,
        )
        self.wsl_btn_apply.pack(side=tk.LEFT, padx=(0, 6))

        self.wsl_btn_test = tk.Button(
            btn_bar, text=t("btn_wsl_test"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"], relief=tk.FLAT, padx=12, pady=5,
            command=self._wsl_run_diagnostic,
        )
        self.wsl_btn_test.pack(side=tk.LEFT, padx=6)

        self.wsl_btn_remove = tk.Button(
            btn_bar, text=t("btn_wsl_remove"), font=("Segoe UI", 9),
            bg=C["red"], fg="#1e1e2e", activebackground="#eba0ac", relief=tk.FLAT, padx=12, pady=5,
            command=self._wsl_remove_isolation,
        )
        self.wsl_btn_remove.pack(side=tk.LEFT, padx=6)

        self.wsl_btn_refresh = tk.Button(
            btn_bar, text=t("btn_wsl_refresh"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], relief=tk.FLAT, padx=10, pady=5,
            command=self._wsl_refresh_status,
        )
        self.wsl_btn_refresh.pack(side=tk.LEFT, padx=6)

        # Audit Log Box
        log_hdr = tk.Frame(w_content, bg=bg)
        log_hdr.pack(fill=tk.X, pady=(8, 2))
        tk.Label(log_hdr, text="Журнал аудита изоляции и сетевых проверок:", font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg).pack(side=tk.LEFT)
        tk.Button(
            log_hdr, text=t("btn_wsl_clear_log"), font=("Segoe UI", 8),
            bg=C["overlay"], fg=C["subtext"], activebackground=C["hover"], relief=tk.FLAT, padx=6, pady=1,
            command=self._wsl_clear_log,
        ).pack(side=tk.RIGHT)

        self.wsl_log_text = scrolledtext.ScrolledText(
            w_content, height=13, font=("Consolas", 9),
            bg="#11111b", fg=C["text"], insertbackground=C["text"],
            relief=tk.FLAT, borderwidth=1,
        )
        self.wsl_log_text.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        self.wsl_log_text.tag_config("SUCCESS", foreground=C["green"])
        self.wsl_log_text.tag_config("ERROR", foreground=C["red"])
        self.wsl_log_text.tag_config("WARN", foreground=C["yellow"])
        self.wsl_log_text.tag_config("STEP", foreground=C["blue"])
        self.wsl_log_text.tag_config("INFO", foreground=C["text"])
        self.wsl_log_text.tag_config("TIMESTAMP", foreground=C["muted"])

        # Initial refresh
        self._wsl_refresh_status()

    def _wsl_log(self, level: str, msg: str):
        if not hasattr(self, "wsl_log_text") or not self.wsl_log_text:
            return
        def _append():
            try:
                self.wsl_log_text.insert(tk.END, f"[{level:<7}] {msg}\n", level)
                self.wsl_log_text.see(tk.END)
            except Exception:
                pass
        self.after(0, _append)

    def _wsl_clear_log(self):
        if hasattr(self, "wsl_log_text") and self.wsl_log_text:
            self.wsl_log_text.delete("1.0", tk.END)

    def _wsl_pick_active_port(self):
        running_ports = []
        for inst in getattr(self, "instances", []):
            if inst.process and inst.process.poll() is None:
                running_ports.append(inst.port)
        if running_ports:
            chosen = running_ports[0]
            self.wsl_port_var.set(str(chosen))
            self._wsl_log("INFO", f"Выбран активный порт SOCKS5 {chosen} из работающего профиля.")
            self._wsl_refresh_status()
        else:
            messagebox.showinfo("vless2socks", "Нет активных запущенных прокси. Используется порт по умолчанию: 1015.")

    def _wsl_refresh_status(self):
        def _worker():
            try:
                if not HAS_WSL_ISO:
                    self.after(0, lambda: self.wsl_status_lbl.config(text="Модуль не найден", fg=C["red"]))
                    return

                installed = wsl_detector.is_wsl_installed()
                if not installed:
                    self.after(0, lambda: self.wsl_status_lbl.config(text="Не установлен", fg=C["red"]))
                    self.after(0, lambda: self.wsl_kernel_lbl.config(text="Н/Д", fg=C["muted"]))
                    return

                active_distro = wsl_detector.get_active_distro()
                self.after(0, lambda: self.wsl_status_lbl.config(text=f"OK ({active_distro})", fg=C["green"]))

                kernel_iso = firewall_isolate.check_wsl_isolation_active(distro=active_distro)
                if kernel_iso:
                    self.after(0, lambda: self.wsl_kernel_lbl.config(text="🟢 Активна (nftables)", fg=C["green"]))
                else:
                    self.after(0, lambda: self.wsl_kernel_lbl.config(text="⚪ Отключена (Direct IP)", fg=C["subtext"]))

                try:
                    port = int(self.wsl_port_var.get().strip())
                except Exception:
                    port = 1015

                p_ok, lat = isolation_tester.check_port_accessible("127.0.0.1", port, timeout=0.3)
                if not self.winfo_exists():
                    return
                if p_ok:
                    self.after(0, lambda: self.wsl_port_status_lbl.config(text=f"🟢 Доступен ({lat} ms)", fg=C["green"]))
                else:
                    self.after(0, lambda: self.wsl_port_status_lbl.config(text="🔴 Закрыт (Offline)", fg=C["red"]))
            except (RuntimeError, tk.TclError):
                pass
            except Exception as e:
                self._wsl_log("ERROR", f"Ошибка проверки статуса: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _wsl_apply_isolation(self):
        try:
            port = int(self.wsl_port_var.get().strip())
        except ValueError:
            messagebox.showerror("vless2socks", "Некорректный номер порта.")
            return

        def _worker():
            self._wsl_log("STEP", f"=== Применение изоляции WSL2 на порт {port} ===")
            if not HAS_WSL_ISO:
                self._wsl_log("ERROR", "Модуль wsl-proxy-isolation недоступен.")
                return
            ok = firewall_isolate.apply_wsl_isolation(port=port)
            if ok:
                self._wsl_log("SUCCESS", f"✓ Сетевая тюрьма успешно активирована на порт {port}!")
                self._wsl_log("SUCCESS", f"✓ Переменные окружения socks5h://127.0.0.1:{port} настроены.")
                self.after(0, lambda: messagebox.showinfo("vless2socks", t("wsl_msg_applied")))
            else:
                self._wsl_log("ERROR", "✕ Ошибка применения правил изоляции ядра Linux.")
                self.after(0, lambda: messagebox.showerror("vless2socks", "Ошибка применения правил изоляции."))
            self._wsl_refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def _wsl_run_diagnostic(self):
        try:
            port = int(self.wsl_port_var.get().strip())
        except ValueError:
            port = 1015

        def _worker():
            if not HAS_WSL_ISO:
                self._wsl_log("ERROR", "Модуль wsl-proxy-isolation недоступен.")
                return
            self._wsl_log("TIMESTAMP", f"--- Запуск комплексного аудита изоляции (порт {port}) ---")
            res = isolation_tester.run_isolation_audit(
                host="127.0.0.1",
                port=port,
                log_callback=self._wsl_log,
            )

            def _update_ui():
                if res["dns_leak_protected"]:
                    self.wsl_dns_lbl.config(text="🟢 Защищен (socks5h)", fg=C["green"])
                else:
                    self.wsl_dns_lbl.config(text="⚠ Не защищен", fg=C["yellow"])

                if res["direct_leak_detected"]:
                    self.wsl_leak_lbl.config(text="✕ ОБНАРУЖЕНА УТЕЧКА!", fg=C["red"])
                else:
                    self.wsl_leak_lbl.config(text="🛡️ Заблокировано (Zero Leaks)", fg=C["green"])

            self.after(0, _update_ui)
            self._wsl_refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def _wsl_remove_isolation(self):
        if not messagebox.askyesno("vless2socks", t("wsl_msg_confirm_remove")):
            return

        def _worker():
            self._wsl_log("STEP", "=== Снятие изоляции WSL2 (восстановление прямого доступа) ===")
            if not HAS_WSL_ISO:
                return
            ok = firewall_isolate.remove_wsl_isolation()
            if ok:
                self._wsl_log("SUCCESS", "✓ Сетевая изоляция снята. Прямой доступ к интернету в WSL2 восстановлен.")
                self.after(0, lambda: messagebox.showinfo("vless2socks", t("wsl_msg_removed")))
            else:
                self._wsl_log("ERROR", "✕ Ошибка при удалении правил.")
            self._wsl_refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    # ── Localization Tab ──────────────────────────────────────
    def _build_localization_tab(self):
        bg = C["bg"]
        px = 16

        tk.Label(self.loc_frame, text=t("loc_title"), font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(anchor="w", padx=px, pady=(12, 2))
        tk.Label(self.loc_frame, text=t("loc_desc"), font=("Segoe UI", 9), fg=C["subtext"], bg=bg).pack(anchor="w", padx=px, pady=(0, 14))

        opt_frame = tk.Frame(self.loc_frame, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        opt_frame.pack(fill=tk.X, padx=px, pady=6)

        self.lang_var = tk.StringVar(value=get_current_language())

        r_en = tk.Radiobutton(
            opt_frame, text=t("lang_en"), variable=self.lang_var, value="en",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"], padx=14, pady=10,
        )
        r_en.pack(anchor="w")

        r_ru = tk.Radiobutton(
            opt_frame, text=t("lang_ru"), variable=self.lang_var, value="ru",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"], padx=14, pady=10,
        )
        r_ru.pack(anchor="w")

        apply_lang_btn = tk.Button(
            self.loc_frame, text=t("btn_apply_lang"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"], relief=tk.FLAT, padx=16, pady=4,
            command=self._apply_language,
        )
        apply_lang_btn.pack(anchor="w", padx=px, pady=12)

    def _apply_language(self):
        selected = self.lang_var.get()
        save_language_preference(selected)

        self.title(t("app_title"))
        self.nav_notebook.tab(self.overview_frame, text=t("nav_overview"))
        self.nav_notebook.tab(self.proxies_frame, text=t("nav_proxies"))
        self.nav_notebook.tab(self.options_frame, text=t("nav_options"))
        self.nav_notebook.tab(self.backup_frame, text=t("nav_backup"))
        self.nav_notebook.tab(self.wsl_iso_frame, text=t("nav_wsl_isolation"))
        self.nav_notebook.tab(self.loc_frame, text=t("nav_localization"))

        for widget in self.overview_frame.winfo_children():
            widget.destroy()
        self._build_overview_tab()

        for widget in self.proxies_frame.winfo_children():
            widget.destroy()
        self._build_proxies_tab()

        for widget in self.options_frame.winfo_children():
            widget.destroy()
        self._build_options_tab()

        for widget in self.backup_frame.winfo_children():
            widget.destroy()
        self._build_backup_tab()

        for widget in self.wsl_iso_frame.winfo_children():
            widget.destroy()
        self._build_wsl_isolation_tab()

        for widget in self.loc_frame.winfo_children():
            widget.destroy()
        self._build_localization_tab()

        self._update_header_stats()
        if hasattr(self, "btn_restart_services") and self.btn_restart_services and self.btn_restart_services.winfo_exists():
            self.btn_restart_services.config(text=f"🔄 {t('btn_restart_services')}")
        if HAS_TRAY and hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.menu = self._build_tray_menu()
        self.update_tray_icon()

    # ── Global Actions & Startup Routines ─────────────────────
    def autostart_configured_proxies(self):
        """Auto-start all proxies with valid URLs if autostart option is enabled."""
        started_count = 0
        for inst in self.instances:
            url = inst.cfg.get("url", "").strip()
            if (url.startswith("vless://") or url.startswith("socks5://") or url.startswith("socks://") or url.startswith("wireguard://") or url.startswith("wg://")) and not inst.running:
                inst.start()
                started_count += 1
        if started_count > 0:
            self.refresh_overview()
            self.update_tray_icon()
            self._update_header_stats()

    def add_new_instance(self):
        """Add a new proxy instance."""
        used_ports = set()
        for inst in self.instances:
            _, p_str = inst.get_listen()
            try:
                used_ports.add(int(p_str))
            except ValueError:
                pass
        free_port = find_free_port(BASE_PORT, exclude=used_ports)

        base = load_base_config()
        base["listen"] = f"127.0.0.1:{free_port}"
        base["url"] = ""

        max_order = max([inst.get_order() for inst in self.instances], default=0.0)
        next_order = float(int(max_order + 1.0)) if float(max_order + 1.0).is_integer() else (max_order + 1.0)
        base["order"] = int(next_order) if next_order.is_integer() else next_order
        base["name"] = f"Proxy {int(next_order)}" if next_order.is_integer() else f"Proxy {next_order}"

        if free_port == 1015:
            base["killswitch"] = True
            base["name"] = "System Proxy"
            base["order"] = 0

        new_id = len(self.instances)
        new_inst = ProxyInstance(self, base, new_id)
        self.instances.append(new_inst)
        self.sort_instances()

        self.current_page = (len(self.instances) - 1) // PAGE_SIZE
        self.save_all()
        self.refresh_current_page_tabs()
        self.refresh_overview()
        self._update_header_stats()

        tab_idx_on_page = (len(self.instances) - 1) % PAGE_SIZE
        self.nav_notebook.select(self.proxies_frame)
        if tab_idx_on_page < len(self.sub_notebook.tabs()):
            self.sub_notebook.select(tab_idx_on_page)

    def remove_instance(self, inst: ProxyInstance):
        if inst in self.instances:
            self.instances.remove(inst)
        self.save_all()
        self.refresh_current_page_tabs()
        self.refresh_overview()
        self._update_header_stats()
        self.update_tray_icon()

    def start_all(self):
        for inst in self.instances:
            if not inst.running:
                inst.start()
        self.refresh_overview()
        self.update_tray_icon()
        self._update_header_stats()

    def stop_all(self):
        for inst in self.instances:
            inst.stop()
        self.refresh_overview()
        self.update_tray_icon()
        self._update_header_stats()

    def refresh_all_geo(self):
        for inst in self.instances:
            inst.check_geo_now()

    def save_all(self):
        data = [inst.get_config() for inst in self.instances]
        save_instances(data)

    def _update_header_stats(self):
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self._update_header_stats)
            except Exception:
                pass
            return
        running = sum(1 for inst in self.instances if inst.running)
        total = len(self.instances)
        try:
            if hasattr(self, "count_label") and self.count_label and self.count_label.winfo_exists():
                self.count_label.config(text=t("count_summary", total=total, running=running))
        except Exception:
            pass

    # ── RestartServices Feature & Dropdown Menu ───────────────
    def toggle_restart_services_menu(self):
        """Toggle slide-down context menu with RestartServices settings."""
        if self._restart_menu_open:
            self.close_restart_services_menu()
        else:
            self.open_restart_services_menu()

    def open_restart_services_menu(self):
        """Open sleek slide-down dropdown menu containing checkboxes for RestartServices."""
        if self._restart_menu_open:
            return

        if self.restart_menu_win and self.restart_menu_win.winfo_exists():
            try:
                self.restart_menu_win.destroy()
            except Exception:
                pass

        self._restart_menu_open = True
        if hasattr(self, "btn_restart_arrow") and self.btn_restart_arrow and self.btn_restart_arrow.winfo_exists():
            self.btn_restart_arrow.config(text="▲")

        # Create borderless toplevel popup
        self.restart_menu_win = tk.Toplevel(self)
        self.restart_menu_win.overrideredirect(True)
        self.restart_menu_win.attributes("-topmost", True)
        self.restart_menu_win.configure(bg=C["border"])

        # Border frame (accent outline)
        border_frame = tk.Frame(self.restart_menu_win, bg=C["blue"], padx=1, pady=1)
        border_frame.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(border_frame, bg=C["card"], padx=12, pady=10)
        inner.pack(fill=tk.BOTH, expand=True)

        # 1. Title / Header
        header = tk.Frame(inner, bg=C["card"])
        header.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            header, text=f"⚙️  {t('restart_menu_title')}",
            font=("Segoe UI", 9, "bold"), fg=C["text"], bg=C["card"]
        ).pack(side=tk.LEFT)

        # Separator line
        sep = tk.Frame(inner, bg=C["overlay"], height=1)
        sep.pack(fill=tk.X, pady=(0, 8))

        # Checkboxes: WSL, WorkProxy, SystemProxy
        chk_opts = [
            (f"🐧  {t('lbl_restart_wsl')}", self.restart_wsl_var, C["yellow"]),
            (f"💼  {t('lbl_restart_work_proxy')}", self.restart_work_proxy_var, C["teal"]),
            (f"⚙️  {t('lbl_restart_system_proxy')}", self.restart_system_proxy_var, C["blue"]),
        ]

        for label_text, var, accent_color in chk_opts:
            row = tk.Frame(inner, bg=C["card"], cursor="hand2")
            row.pack(fill=tk.X, pady=3)

            lbl = tk.Label(
                row, text=label_text, font=("Segoe UI", 9, "bold"),
                fg=accent_color, bg=C["card"], cursor="hand2",
            )
            lbl.pack(side=tk.LEFT)

            cb = OrangeCheckbox(
                row, variable=var,
                bg=C["card"],
                orange_color=C["orange"],
                size=18,
                command=self._on_restart_flags_changed,
            )
            cb.pack(side=tk.RIGHT)

            # Clicking the row or label also toggles the checkbox
            lbl.bind("<Button-1>", lambda e, c=cb: c._toggle())
            row.bind("<Button-1>", lambda e, c=cb: c._toggle())

        # Footer Hint
        hint_lbl = tk.Label(
            inner, text=t("restart_menu_hint"),
            font=("Segoe UI", 7), fg=C["subtext"], bg=C["card"]
        )
        hint_lbl.pack(anchor="w", pady=(8, 0))

        # Geometry & Coordinates positioning
        self.update_idletasks()
        try:
            arrow_rx = self.btn_restart_arrow.winfo_rootx()
            arrow_rw = self.btn_restart_arrow.winfo_width()
            main_ry = self.btn_restart_services.winfo_rooty()
            main_h = self.btn_restart_services.winfo_height()

            target_w = 245
            target_h = 168
            popup_x = (arrow_rx + arrow_rw) - target_w
            popup_y = main_ry + main_h + 3
        except Exception:
            popup_x = self.winfo_rootx() + 300
            popup_y = self.winfo_rooty() + 50
            target_w = 245
            target_h = 168

        # Smooth slide-down unfolding animation
        self.restart_menu_win.geometry(f"{target_w}x4+{popup_x}+{popup_y}")
        self.restart_menu_win.deiconify()

        def _slide_step(step=1, total_steps=7):
            if not self._restart_menu_open or not self.restart_menu_win or not self.restart_menu_win.winfo_exists():
                return
            h = int(target_h * (step / total_steps))
            self.restart_menu_win.geometry(f"{target_w}x{h}+{popup_x}+{popup_y}")
            if step < total_steps:
                self.after(12, lambda: _slide_step(step + 1, total_steps))

        _slide_step()

        # Auto-dismiss watcher if parent window state is not normal (e.g. iconic / minimized / hidden)
        def _check_parent_state():
            if not getattr(self, "_restart_menu_open", False):
                return
            if not self.restart_menu_win or not self.restart_menu_win.winfo_exists():
                return
            try:
                if self.state() != "normal":
                    self.close_restart_services_menu()
                    return
            except Exception:
                self.close_restart_services_menu()
                return
            self.after(150, _check_parent_state)

        _check_parent_state()

        # Dismiss when clicked outside
        def _on_global_click(event):
            if not self._restart_menu_open or not self.restart_menu_win or not self.restart_menu_win.winfo_exists():
                return
            try:
                wx, wy = event.x_root, event.y_root
                px, py = self.restart_menu_win.winfo_rootx(), self.restart_menu_win.winfo_rooty()
                pw, ph = self.restart_menu_win.winfo_width(), self.restart_menu_win.winfo_height()
                ax, ay = self.btn_restart_arrow.winfo_rootx(), self.btn_restart_arrow.winfo_rooty()
                aw, ah = self.btn_restart_arrow.winfo_width(), self.btn_restart_arrow.winfo_height()

                if (px <= wx <= px + pw and py <= wy <= py + ph) or (ax <= wx <= ax + aw and ay <= wy <= ay + ah):
                    return
                self.close_restart_services_menu()
            except Exception:
                self.close_restart_services_menu()

        self.bind_all("<Button-1>", _on_global_click, add="+")

    def close_restart_services_menu(self):
        """Close slide-down context menu."""
        self._restart_menu_open = False
        if hasattr(self, "btn_restart_arrow") and self.btn_restart_arrow and self.btn_restart_arrow.winfo_exists():
            try:
                self.btn_restart_arrow.config(text="▼")
            except Exception:
                pass
        if hasattr(self, "restart_menu_win") and self.restart_menu_win:
            try:
                if self.restart_menu_win.winfo_exists():
                    self.restart_menu_win.destroy()
            except Exception:
                pass
        self.restart_menu_win = None

    def _on_main_unmap(self, event=None):
        """Dismiss dropdown context menu if main window is unmapped / minimized."""
        if event is None or event.widget == self:
            if getattr(self, "_restart_menu_open", False):
                self.close_restart_services_menu()

    def _on_main_deactivate(self, event=None):
        """Dismiss dropdown context menu if application loses focus to another window."""
        if getattr(self, "_restart_menu_open", False):
            self.close_restart_services_menu()

    def _on_restart_flags_changed(self):
        """Persist checkbox choices when modified by user."""
        wsl = bool(self.restart_wsl_var.get())
        work = bool(self.restart_work_proxy_var.get())
        sys_p = bool(self.restart_system_proxy_var.get())
        settings_manager.set_restart_services_flags(wsl, work, sys_p)

    def execute_restart_services(self):
        """Restart all services whose checkboxes are currently checked (WSL, WorkProxy, SystemProxy)."""
        wsl = bool(self.restart_wsl_var.get())
        work = bool(self.restart_work_proxy_var.get())
        sys_p = bool(self.restart_system_proxy_var.get())

        if not (wsl or work or sys_p):
            messagebox.showinfo(
                "RestartServices",
                t("msg_restart_no_selection"),
                parent=self,
            )
            return

        # Open / bring up the transparent HUD monitor in bottom-right corner
        def _ensure_hud():
            if not getattr(self, "restart_hud_win", None) or not self.restart_hud_win.winfo_exists():
                self.restart_hud_win = RestartHUDWindow(self)
            else:
                try:
                    self.restart_hud_win.deiconify()
                    self.restart_hud_win.lift()
                    self.restart_hud_win.in_progress = True
                    self.restart_hud_win.countdown_sec = RestartHUDWindow.AUTO_CLOSE_SEC
                    self.restart_hud_win.status_badge.config(text="🔄 ...", fg=C["yellow"])
                except Exception:
                    pass

        self.after(0, _ensure_hud)

        def _worker():
            self._set_restart_button_busy(True)
            restarted_items = []
            try:
                # 1. WSL
                if wsl:
                    self._log("🔄 [RestartServices] Restarting WSL...")
                    ok, msg = restart_wsl()
                    if ok:
                        restarted_items.append("WSL")
                        self._log("✅ [RestartServices] WSL successfully restarted.")
                    else:
                        self._log(f"⚠️ [RestartServices] {msg}")

                # 2. WorkProxy instances
                restarted_proxies = set()
                if work:
                    self._log("🔄 [RestartServices] Restarting WorkProxy instances...")
                    cnt = 0
                    for inst in self.instances:
                        if inst.is_work_proxy():
                            try:
                                inst.restart()
                            except Exception as err:
                                self._log(f"⚠️ [RestartServices] WorkProxy restart warning: {err}")
                            restarted_proxies.add(inst)
                            cnt += 1
                    restarted_items.append(f"WorkProxy ({cnt})")
                    self._log(f"✅ [RestartServices] Restarted {cnt} WorkProxy instance(s).")

                # 3. SystemProxy instances
                if sys_p:
                    self._log("🔄 [RestartServices] Restarting SystemProxy instances...")
                    cnt = 0
                    for inst in self.instances:
                        if inst.is_system_proxy():
                            if inst not in restarted_proxies:
                                try:
                                    inst.restart()
                                except Exception as err:
                                    self._log(f"⚠️ [RestartServices] SystemProxy restart warning: {err}")
                                restarted_proxies.add(inst)
                            cnt += 1
                    restarted_items.append(f"SystemProxy ({cnt})")
                    self._log(f"✅ [RestartServices] Restarted {cnt} SystemProxy instance(s).")

                self.after(500, self.refresh_overview)
                self.after(500, self._update_header_stats)
                summary_str = ", ".join(restarted_items) if restarted_items else "Done"
                self._log(f"🎉 [RestartServices] Finished restarting: {summary_str}.")
                self.after(0, lambda: self._set_restart_button_success(summary_str))
                if getattr(self, "restart_hud_win", None) and self.restart_hud_win.winfo_exists():
                    self.restart_hud_win.set_finished(summary_str)
            except Exception as e:
                self._log(f"❌ [RestartServices] Error: {e}")
                self.after(0, lambda: self._set_restart_button_busy(False))
                if getattr(self, "restart_hud_win", None) and self.restart_hud_win.winfo_exists():
                    self.restart_hud_win.set_finished("Error")

        threading.Thread(target=_worker, daemon=True).start()

    def _log(self, msg: str):
        """Application-level logger for background service operations."""
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)
        try:
            if hasattr(self, "_wsl_log"):
                self._wsl_log("INFO", msg)
        except Exception:
            pass
        try:
            if getattr(self, "restart_hud_win", None) and self.restart_hud_win.winfo_exists():
                self.restart_hud_win.log(msg)
        except Exception:
            pass

    def _set_restart_button_busy(self, busy: bool):
        """Update button state during background restart operations."""
        if not hasattr(self, "btn_restart_services") or not self.btn_restart_services or not self.btn_restart_services.winfo_exists():
            return
        if busy:
            self.btn_restart_services.config(
                text=f"⏳ {t('msg_restart_in_progress')}",
                state=tk.DISABLED,
                bg=C["overlay"],
                fg=C["text"],
            )
        else:
            self.btn_restart_services.config(
                text=f"🔄 {t('btn_restart_services')}",
                state=tk.NORMAL,
                bg=C["blue"],
                fg="#1e1e2e",
            )

    def _set_restart_button_success(self, text: str):
        """Briefly show success badge on button before reverting to normal."""
        if not hasattr(self, "btn_restart_services") or not self.btn_restart_services or not self.btn_restart_services.winfo_exists():
            return
        self.btn_restart_services.config(
            text=f"✅ {text}",
            state=tk.NORMAL,
            bg=C["green"],
            fg="#1e1e2e",
        )
        self.after(2500, lambda: self._set_restart_button_busy(False))

    # ── System Tray ───────────────────────────────────────────
    def _build_tray_menu(self):
        """Build tray context menu with RestartServices and its settings submenu."""
        return pystray.Menu(
            pystray.MenuItem(t("tray_show"), self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"🔄 {t('btn_restart_services')}", self._tray_restart_services),
            pystray.MenuItem(
                f"⚙️ {t('restart_menu_title')}",
                pystray.Menu(
                    pystray.MenuItem(
                        f"🐧 {t('lbl_restart_wsl')}",
                        self._tray_toggle_wsl,
                        checked=lambda _: bool(settings_manager.get_setting("restart_wsl", True)),
                    ),
                    pystray.MenuItem(
                        f"💼 {t('lbl_restart_work_proxy')}",
                        self._tray_toggle_work_proxy,
                        checked=lambda _: bool(settings_manager.get_setting("restart_work_proxy", True)),
                    ),
                    pystray.MenuItem(
                        f"⚙️ {t('lbl_restart_system_proxy')}",
                        self._tray_toggle_system_proxy,
                        checked=lambda _: bool(settings_manager.get_setting("restart_system_proxy", True)),
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_exit"), self._exit_from_tray),
        )

    def _setup_tray(self):
        if not HAS_TRAY:
            return

        menu = self._build_tray_menu()
        self._tray_icon = pystray.Icon(
            "vless2socks",
            icon=make_tray_icon("gray"),
            title="vless2socks",
            menu=menu,
        )
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _tray_restart_services(self, icon=None, item=None):
        """Trigger RestartServices from system tray context menu."""
        self.after(0, self.execute_restart_services)

    def _tray_toggle_wsl(self, icon=None, item=None):
        cur = bool(settings_manager.get_setting("restart_wsl", True))
        new_val = not cur
        settings_manager.set_setting("restart_wsl", new_val)
        self.restart_wsl_var.set(new_val)
        if self._tray_icon:
            self._tray_icon.update_menu()

    def _tray_toggle_work_proxy(self, icon=None, item=None):
        cur = bool(settings_manager.get_setting("restart_work_proxy", True))
        new_val = not cur
        settings_manager.set_setting("restart_work_proxy", new_val)
        self.restart_work_proxy_var.set(new_val)
        if self._tray_icon:
            self._tray_icon.update_menu()

    def _tray_toggle_system_proxy(self, icon=None, item=None):
        cur = bool(settings_manager.get_setting("restart_system_proxy", True))
        new_val = not cur
        settings_manager.set_setting("restart_system_proxy", new_val)
        self.restart_system_proxy_var.set(new_val)
        if self._tray_icon:
            self._tray_icon.update_menu()

    def update_tray_icon(self):
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.update_tray_icon)
            except Exception:
                pass
            return
        if not self._tray_icon:
            return
        running = sum(1 for inst in self.instances if inst.running and inst.healthy)
        total_running = sum(1 for inst in self.instances if inst.running)
        if running > 0:
            self._tray_icon.icon = make_tray_icon("green")
            self._tray_icon.title = t("tray_running", count=running)
        elif total_running > 0:
            self._tray_icon.icon = make_tray_icon("yellow")
            self._tray_icon.title = t("tray_starting")
        else:
            self._tray_icon.icon = make_tray_icon("red")
            self._tray_icon.title = t("tray_stopped")
        self._update_header_stats()

    def _hide_to_tray(self):
        self.close_restart_services_menu()
        if HAS_TRAY and self._tray_icon:
            self.withdraw()
            self._hidden = True
        else:
            self._exit()

    def _show_from_tray(self, icon=None, item=None):
        self.after(0, self._do_show)

    def _do_show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self._hidden = False

    def _exit_from_tray(self, icon=None, item=None):
        self.after(0, self._exit)

    def _exit(self):
        if getattr(self, "restart_hud_win", None):
            try:
                self.restart_hud_win.close()
            except Exception:
                pass
        for inst in self.instances:
            inst.destroy()
        self.save_all()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # ── Background Poll Loop ──────────────────────────────────
    def _poll_loop(self):
        for inst in self.instances:
            inst.poll_health()
        self.update_tray_icon()

        # Check periodic auto-backup
        try:
            ok, msg, path = backup_manager.check_and_run_auto_backup()
            if ok and hasattr(self, "backup_list_container"):
                self._render_available_backups()
                if hasattr(self, "last_backup_time_lbl"):
                    now_str = settings_manager.get_setting("last_backup_time", "-")
                    self.last_backup_time_lbl.config(text=t("lbl_last_backup_time", time=now_str), fg=C["green"])
        except Exception:
            pass

        self.after(4000, self._poll_loop)


#: На случай, если шаблона не оказалось и в сборке.
_FALLBACK_CONFIG = {
    "url": "",
    "listen": "127.0.0.1:1081",
    "username": "",
    "password": "",
    "udp": True,
    "connectTimeout": 10,
    "udpIdleTimeout": 60,
    "logLevel": "info",
    "backend": "auto",
    "xrayPath": "",
    "xrayLegacyConfig": False,
}


def _bootstrap_files() -> None:
    """Первый запуск: создать отсутствующие файлы из зашитых шаблонов и распаковать bin/."""
    try:
        unpack_bundled_bin()
    except Exception:
        pass

    plan = (
        (CONFIG_FILE, "config.example.json", _FALLBACK_CONFIG),
        (INSTANCES_FILE, "instances.example.json", [_FALLBACK_CONFIG]),
        (SETTINGS_FILE, "settings.example.json", settings_manager.DEFAULT_SETTINGS),
    )
    for target, template, fallback in plan:
        if target.exists():
            continue
        source = bundled(template)
        if source.is_file() and source != target:
            shutil.copyfile(source, target)
        else:
            target.write_text(
                json.dumps(fallback, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )


def _fatal(details: str) -> None:
    """Показать ошибку запуска окном: в сборке без консоли её иначе не видно."""
    print(details, file=sys.stderr)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("vless2socks — ошибка запуска", details)
        root.destroy()
    except Exception:
        pass


def _show_already_running_notice(duration_ms: int = 1200) -> None:
    """Отображает окно уведомления о том, что программа уже запущена, на duration_ms миллисекунд и закрывается."""
    try:
        root = tk.Tk()
        root.title("vless2socks")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#1e1e2e")

        frame = tk.Frame(root, bg="#1e1e2e", highlightbackground="#89b4fa", highlightthickness=2, padx=24, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="⚠️  " + t("already_running"),
            font=("Segoe UI", 11, "bold"),
            fg="#cdd6f4",
            bg="#1e1e2e"
        ).pack()

        root.update_idletasks()
        w = root.winfo_reqwidth()
        h = root.winfo_reqheight()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")

        root.after(duration_ms, root.destroy)
        root.mainloop()
    except Exception:
        pass


_LOCK_FILE_HANDLE = None


def _acquire_instance_lock() -> bool:
    """Пытается захватить блокировку единственного экземпляра приложения.
    Возвращает True, если экземпляр первый, False — если уже запущен.
    """
    global _LOCK_FILE_HANDLE
    lock_path = ROOT_DIR / ".vless2socks.lock"
    try:
        if sys.platform == "win32":
            import msvcrt
            f = open(lock_path, "a+b")
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                _LOCK_FILE_HANDLE = f
                return True
            except (BlockingIOError, PermissionError, OSError):
                f.close()
                return False
        else:
            import fcntl
            f = open(lock_path, "a+b")
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                _LOCK_FILE_HANDLE = f
                return True
            except (BlockingIOError, PermissionError, OSError):
                f.close()
                return False
    except Exception:
        return True


def _release_instance_lock() -> None:
    """Освобождает блокировку приложения."""
    global _LOCK_FILE_HANDLE
    if _LOCK_FILE_HANDLE is not None:
        try:
            if sys.platform == "win32":
                import msvcrt
                _LOCK_FILE_HANDLE.seek(0)
                msvcrt.locking(_LOCK_FILE_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(_LOCK_FILE_HANDLE.fileno(), fcntl.LOCK_UN)
            _LOCK_FILE_HANDLE.close()
        except Exception:
            pass
        _LOCK_FILE_HANDLE = None


def main():
    # Если запущен с аргументами командной строки (фоновый прокси от GUI или прямой запуск)
    argv = sys.argv[1:]
    if argv:
        try:
            unpack_bundled_bin()
        except Exception:
            pass
        if "--cli" in argv:
            argv = [a for a in argv if a != "--cli"]
            import main as cli_module
            return cli_module.main(argv)
        if any(arg in argv for arg in ("-c", "--config", "-u", "--url", "--test", "--doctor", "--ip", "--help", "-h")):
            import main as cli_module
            return cli_module.main(argv)

    # Проверка на множественный запуск GUI
    if not _acquire_instance_lock():
        _show_already_running_notice(1200)
        return 0

    try:
        _bootstrap_files()
        app = VlessApp()
    except Exception:
        _fatal(
            f"Не удалось запустить программу.\n\nРабочая папка: {ROOT_DIR}\n\n"
            f"{traceback.format_exc()}"
        )
        _release_instance_lock()
        return 1

    try:
        app.mainloop()
    finally:
        _release_instance_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
