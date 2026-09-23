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
    from vless2socks.url import SocksServer
    if isinstance(cfg.server, SocksServer):
        pass  # SOCKS5 upstream always requires xray
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
    """Ensure that the default unfilled System Proxy on port 1015 (#0) exists with killswitch=True."""
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
            break

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

        # Protocol & SOCKS5 UI
        self.proto_var: Optional[tk.StringVar] = None
        self.vless_container: Optional[tk.Frame] = None
        self.socks_container: Optional[tk.Frame] = None
        self.socks_summary_lbl: Optional[tk.Label] = None

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

        # Ensure port 1015 defaults
        _, port = self.get_listen()
        if str(port) == "1015":
            if "killswitch" not in self.cfg:
                self.cfg["killswitch"] = True
            if not self.cfg.get("name"):
                self.cfg["name"] = "System Proxy"
            if "order" not in self.cfg:
                self.cfg["order"] = 0

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
        self.proto_var = tk.StringVar(value="socks5" if is_socks else "vless")

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
        rb_socks.pack(side=tk.LEFT)

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
        self.url_entry.insert(0, current_url if not is_socks else "")
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

        # Show active container
        if is_socks:
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
                if is_leak:
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

    def _on_proto_changed(self):
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "socks5":
            if self.vless_container:
                self.vless_container.pack_forget()
            if self.socks_container:
                self.socks_container.pack(fill=tk.X)
                self._update_socks_summary()
            curr_url = self.cfg.get("url", "").strip()
            if not curr_url.startswith(("socks5://", "socks://")):
                self.open_socks_dialog()
        else:
            if self.socks_container:
                self.socks_container.pack_forget()
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

        val = self.proto_var.get() if self.proto_var else ("socks5" if self.cfg.get("url", "").startswith(("socks5://", "socks://")) else "vless")
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
        if not (url.startswith("vless://") or url.startswith("socks5://") or url.startswith("socks://")):
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

    def apply(self):
        val = self.proto_var.get() if self.proto_var else "vless"
        if val == "vless" and self.url_entry:
            self.cfg["url"] = self.url_entry.get().strip()
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
        h, port = self.get_listen()
        http_port = self.get_http_port()
        states = {
            "stopped":  (C["red"],    t("status_stopped"), t("btn_on"),  C["green"]),
            "starting": (C["yellow"], t("status_starting"), t("btn_off"), C["red"]),
            "running":  (C["green"],  t("status_running", port=port, http_port=http_port), t("btn_off"), C["red"]),
            "error":    (C["red"],    t("status_error"), t("btn_on"),  C["green"]),
        }
        dot_color, label_text, btn_text, btn_color = states.get(state, states["stopped"])
        if self.status_dot:
            self.status_dot.config(fg=dot_color)
        if self.status_label:
            self.status_label.config(text=label_text)
        if self.toggle_btn:
            self.toggle_btn.config(text=btn_text, bg=btn_color)

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
        if "name" in self.cfg:
            cfg["name"] = self.cfg["name"]
        if "order" in self.cfg:
            cfg["order"] = self.cfg["order"]
        if "sendThrough" in self.cfg:
            cfg["sendThrough"] = self.cfg["sendThrough"]
        return cfg


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

        # Protocol
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

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
        # 1. Top Header Bar
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

        # Quick Add Button in header
        add_btn = tk.Button(
            self.top_bar, text=t("btn_new_tab"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=12, pady=3, command=self.add_new_instance,
        )
        add_btn.pack(side=tk.RIGHT, padx=10, pady=8)

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

        # Tab 5: Localization
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
        for widget in self.overview_content.winfo_children():
            widget.destroy()

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

        for widget in self.loc_frame.winfo_children():
            widget.destroy()
        self._build_localization_tab()

        self._update_header_stats()
        self.update_tray_icon()

    # ── Global Actions & Startup Routines ─────────────────────
    def autostart_configured_proxies(self):
        """Auto-start all proxies with valid URLs if autostart option is enabled."""
        started_count = 0
        for inst in self.instances:
            url = inst.cfg.get("url", "").strip()
            if (url.startswith("vless://") or url.startswith("socks5://") or url.startswith("socks://")) and not inst.running:
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
        running = sum(1 for inst in self.instances if inst.running)
        total = len(self.instances)
        self.count_label.config(text=t("count_summary", total=total, running=running))

    # ── System Tray ───────────────────────────────────────────
    def _setup_tray(self):
        if not HAS_TRAY:
            return

        menu = pystray.Menu(
            pystray.MenuItem(t("tray_show"), self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_exit"), self._exit_from_tray),
        )
        self._tray_icon = pystray.Icon(
            "vless2socks",
            icon=make_tray_icon("gray"),
            title="vless2socks",
            menu=menu,
        )
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def update_tray_icon(self):
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
