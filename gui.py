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
from vless2socks.paths import APP_DIR, FROZEN, bundled

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

#: Чем поднимать отдельный прокси в собранном виде: внутри .exe нет ни
#: python.exe, ни main.py на диске, поэтому рядом кладётся консольный CLI.
CLI_EXE = ROOT_DIR / ("vless2socks-cli.exe" if sys.platform == "win32" else "vless2socks-cli")


def proxy_command(config_path: Path | str) -> list[str]:
    """Команда запуска одного прокси — для скрипта и для сборки по-разному."""
    if FROZEN:
        return [str(CLI_EXE), "-c", str(config_path)]
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
    if backend == "auto" and not cfg.server.unsupported:
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
BASE_PORT = 1080

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
        return {"url": "", "listen": "127.0.0.1:1080"}


def load_instances() -> list[dict]:
    """Load list of instances from instances.json."""
    try:
        with open(INSTANCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    except Exception:
        pass
    base = load_base_config()
    return [base]


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

        # Reconnect state
        self._manual_stop = False
        self._reconnect_attempt = 0
        self._reconnect_timer_id: Optional[str] = None

    def _cancel_reconnect(self):
        if self._reconnect_timer_id:
            try:
                self.app.after_cancel(self._reconnect_timer_id)
            except Exception:
                pass
            self._reconnect_timer_id = None

    def _schedule_reconnect(self):
        if self._manual_stop:
            return
        if not settings_manager.get_setting("auto_reconnect", True):
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
        listen = self.cfg.get("listen", "127.0.0.1:1080")
        h, _, p = listen.rpartition(":")
        return h or "127.0.0.1", p or "1080"

    def get_http_port(self) -> int:
        _, p = self.get_listen()
        try:
            return int(p) + 10000
        except ValueError:
            return 11080

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

        # 4. VLESS URL with Hidden Mask & Eye Toggle
        url_frame = tk.Frame(self.frame, bg=bg)
        url_frame.pack(fill=tk.X, padx=px, pady=4)

        url_header = tk.Frame(url_frame, bg=bg)
        url_header.pack(fill=tk.X)
        tk.Label(url_header, text=t("lbl_vless_url"), font=("Segoe UI", 9, "bold"), fg=C["subtext"], bg=bg).pack(side=tk.LEFT)

        input_row = tk.Frame(url_frame, bg=bg)
        input_row.pack(fill=tk.X, pady=(2, 4))

        self.url_entry = tk.Entry(
            input_row, font=("Consolas", 9),
            bg=C["overlay"], fg=fg, insertbackground=fg,
            relief=tk.FLAT, borderwidth=5, show="•",
        )
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.url_entry.insert(0, self.cfg.get("url", ""))
        attach_clipboard_and_context_menu(self.url_entry)

        # Eye toggle button
        self.eye_btn = tk.Button(
            input_row, text="👁️", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.toggle_url_visibility,
        )
        self.eye_btn.pack(side=tk.LEFT, padx=(0, 4))

        # Copy button
        copy_btn = tk.Button(
            input_row, text="📋", font=("Segoe UI", 10),
            bg=C["overlay"], fg=fg, activebackground=C["hover"],
            relief=tk.FLAT, padx=8, pady=2, command=self.copy_url,
        )
        copy_btn.pack(side=tk.LEFT)

        # Save & Apply button
        apply_btn = tk.Button(
            url_frame, text=t("btn_save_apply"), font=("Segoe UI", 9, "bold"),
            bg=C["hover"], fg=fg, activebackground="#585b70",
            relief=tk.FLAT, padx=12, pady=3, command=self.apply,
        )
        apply_btn.pack(anchor="w")

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
        url = self.url_entry.get().strip() if self.url_entry else self.cfg.get("url", "")
        if url:
            self.app.clipboard_clear()
            self.app.clipboard_append(url)
            self._log(f"VLESS URL {t('copied_toast')}")

    def check_geo_now(self):
        """Perform live IP and Geo probe."""
        if self.geo_card_label:
            self.geo_card_label.config(text=t("geo_checking"))

        host, port_str = self.get_listen()
        try:
            port = int(port_str)
        except ValueError:
            port = 1080

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
            port = int(port_str or "1080")
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

        url = self.url_entry.get().strip() if self.url_entry else self.cfg.get("url", "").strip()
        host = self.host_entry.get().strip() if self.host_entry else self.get_listen()[0]
        port = self.port_entry.get().strip() if self.port_entry else self.get_listen()[1]
        host = host or "127.0.0.1"
        port = port or "1080"

        if not url:
            messagebox.showwarning("vless2socks", t("msg_url_required"))
            return
        if not url.startswith("vless://"):
            messagebox.showwarning("vless2socks", t("msg_url_prefix"))
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
        if FROZEN and not CLI_EXE.exists():
            self._log(
                f"Не найден {CLI_EXE.name} рядом с программой — нечем поднять прокси."
            )
            self._set_state("error")
            return
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

        proc = self.process
        if proc is None:
            self.running = False
            self._set_state("stopped")
            self.app.update_tray_icon()
            self.app.refresh_overview()
            return

        self._log("Stopping...")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception as e:
            self._log(f"Stop error: {e}")

        self.process = None
        self.running = False
        self.healthy = False
        self._set_state("stopped")
        self._log("Stopped")
        self.app.update_tray_icon()
        self.app.refresh_overview()

    def apply(self):
        if self.url_entry:
            self.cfg["url"] = self.url_entry.get().strip()
        if self.host_entry and self.port_entry:
            h = self.host_entry.get().strip() or "127.0.0.1"
            p = self.port_entry.get().strip() or "1080"
            self.cfg["listen"] = f"{h}:{p}"
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
        if self.running:
            self._log(f"Process terminated (exit code {code})")
            self.running = False
            self.process = None
            self.healthy = False
            if self.frame:
                self.frame.after(0, lambda: self._set_state("error"))
            self.app.after(0, self.app.update_tray_icon)
            self.app.after(0, self.app.refresh_overview)

            if not self._manual_stop:
                self.app.after(0, self._schedule_reconnect)

    def poll_health(self):
        if not self.running:
            return
        h, p_str = self.get_listen()
        try:
            p = int(p_str)
        except ValueError:
            return
        alive = is_port_alive(h, p)
        if alive and not self.healthy:
            self.healthy = True
            self._reconnect_attempt = 0
            self._set_state("running")
        elif not alive and self.healthy:
            self.healthy = False
            if self.process and self.process.poll() is None:
                self._set_state("starting")
            else:
                self._set_state("error")
                if not self._manual_stop:
                    self._schedule_reconnect()

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
        if self.url_entry:
            cfg["url"] = self.url_entry.get().strip()
        if self.host_entry and self.port_entry:
            h = self.host_entry.get().strip() or "127.0.0.1"
            p = self.port_entry.get().strip() or "1080"
            cfg["listen"] = f"{h}:{p}"
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

    def _load_saved_data(self):
        raw_instances = load_instances()
        self.instances = [ProxyInstance(self, cfg, idx) for idx, cfg in enumerate(raw_instances)]
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
            server_name = extract_server_name(inst.cfg.get("url", ""))

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
            tk.Label(left_badge, text=f"#{idx + 1}", font=("Consolas", 10, "bold"), fg=C["blue"], bg=C["card"]).pack(side=tk.LEFT)

            # Center Info: Name, Ports, Geo
            info = tk.Frame(card, bg=C["card"])
            info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=6)

            title_row = tk.Frame(info, bg=C["card"])
            title_row.pack(anchor="w")

            tk.Label(title_row, text=server_name, font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"]).pack(side=tk.LEFT)

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
            name = extract_server_name(inst.cfg.get("url", ""))
            flag = inst.geo_info.get("flag", "🌐")
            tab_label = f" {flag} #{i + 1} {name[:12]}:{port} "
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
            command=lambda: settings_manager.set_setting("auto_reconnect", self.auto_reconnect_var.get()),
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
            if url.startswith("vless://") and not inst.running:
                inst.start()
                started_count += 1
        if started_count > 0:
            self.refresh_overview()
            self.update_tray_icon()

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

        new_id = len(self.instances)
        new_inst = ProxyInstance(self, base, new_id)
        self.instances.append(new_inst)

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

    def stop_all(self):
        for inst in self.instances:
            if inst.running:
                inst.stop()
        self.refresh_overview()
        self.update_tray_icon()

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
    "listen": "127.0.0.1:1080",
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
    """Первый запуск: создать отсутствующие файлы из зашитых шаблонов.

    Раньше отсутствие config.json означало ``sys.exit(1)`` с сообщением в
    stderr. У собранного .exe консоли нет (``console=False``), поэтому со
    стороны это выглядело как «программа просто не запускается».
    """
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


def main():
    try:
        _bootstrap_files()
        app = VlessApp()
    except Exception:
        _fatal(
            f"Не удалось запустить программу.\n\nРабочая папка: {ROOT_DIR}\n\n"
            f"{traceback.format_exc()}"
        )
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
