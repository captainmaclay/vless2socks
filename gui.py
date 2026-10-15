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
import functools
import io
import json
import queue
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
from typing import Any, Iterable, Optional

import backup_manager
import geo_ip
import proxy_groups
import settings_manager
import subscriptions
from ZapretRecovery import Zapret2Watcher, ZapretRecoveryWidget
from macui import MAC, MacButton, MacDivider, MacSegmented, MacTabChip, ui_font
from overview_view import Button as OverviewButton, CardModel, HeaderModel, HintModel, OverviewView
from i18n import get_current_language, load_language_preference, save_language_preference, t
from vless2socks.paths import APP_DIR, FROZEN, bundled, unpack_bundled_bin
from vless2socks.power_watcher import PowerBroadcastWatcher
from vless2socks.wsl_watchdog import WSL_OPERATION_LOCK, WslWatchdog
from vless2socks.ipc_server import SingleInstanceIpcServer, request_show_running_instance
from vless2socks.tunnel_probe import probe_socks5_tunnel

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
RESTART_PROXIES_TIMEOUT_SEC = 45
UI_QUEUE_POLL_MS = 20
LOG_FLUSH_MS = 200          # proxy log lines reach the widget in batches, not one redraw per line
LOG_WIDGET_MAX_LINES = 500  # the log widget keeps only the tail; an unbounded Text makes every append slow
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
    "orange":   "#fab387",
    "peach":    "#fab387",
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


def is_port_alive(host: str, port: int, timeout: float = 0.8) -> bool:
    """Check if port is responding via TCP/SOCKS5 connect without generating EOF errors."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            try:
                s.settimeout(min(timeout, 0.3))
                s.sendall(b"\x05\x01\x00")
                _ = s.recv(2)
            except Exception:
                pass
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


#: Our own proxy launchers: `python main.py` in a script build, vless2socks-cli.exe in a frozen one.
_SUPERVISOR_EXE_NAMES = {"python.exe", "vless2socks-cli.exe"}


def _process_table() -> dict[int, tuple[int, str]]:
    """Snapshot of running processes on Windows: pid -> (parent pid, exe name in lower case)."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    table: dict[int, tuple[int, str]] = {}
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if snapshot in (None, wintypes.HANDLE(-1).value):
            return {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                table[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile.lower())
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return {}
    return table


def _expand_to_proxy_supervisors(
    pids: set[int],
    current_pid: int,
    table: dict[int, tuple[int, str]] | None = None,
    start_time=None,
) -> set[int]:
    """Replace an xray pid by the top of its `main.py` launcher chain.

    xray is kept alive by its launcher, so killing xray alone frees the port for a second at most: a
    launcher orphaned by a previous GUI session restarts it and fights the new instance for the port.
    Never climbs into the current process or its ancestors.
    """
    if table is None:
        table = _process_table()
    if not table:
        return set(pids)
    if start_time is None:
        start_time = _process_start_time

    protected = set()
    cursor = current_pid
    while cursor in table and cursor not in protected:
        protected.add(cursor)
        cursor = table[cursor][0]

    expanded = set()
    for pid in pids:
        target = pid
        if table.get(pid, (0, ""))[1] == "xray.exe":
            for _ in range(3):
                parent = table.get(target, (0, ""))[0]
                if parent in protected or table.get(parent, (0, ""))[1] not in _SUPERVISOR_EXE_NAMES:
                    break
                # A recorded parent pid may have been reused by an unrelated process after the real
                # parent died; a genuine parent is never younger than its child.
                born_parent, born_child = start_time(parent), start_time(target)
                if born_parent is None or born_child is None or born_parent > born_child:
                    break
                target = parent
        expanded.add(target)
    return expanded


def _process_start_time(pid: int) -> int | None:
    """Creation time of a process as a Windows FILETIME integer, or None if it cannot be read."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


#: Console tools (netstat) started by the GUI at the same time. Dozens of proxies stopping or
#: taking over ports together used to start them by the hundred; the desktop heap ran out and
#: Windows failed new console programs with 0xc0000142.
_CONSOLE_TOOL_SLOTS = threading.BoundedSemaphore(4)
#: Killswitch leak checks running at the same time (each one makes outgoing connections)
_LEAK_CHECK_SLOTS = threading.BoundedSemaphore(3)
#: Proxies starting at the same time on "Start all" / autostart
START_CONCURRENCY = 6
START_SPACING_MS = 400
#: Short background jobs (geo lookups, killswitch checks) share one pool: starting a new
#: thread from the GUI thread for each of dozens of proxies made Thread.start() wait on
#: thread start-up again and again while everything else was busy
_BACKGROUND_POOL = None
_BACKGROUND_POOL_LOCK = threading.Lock()


def submit_background(fn, *args):
    """Run fn in the shared background pool; returns its Future."""
    global _BACKGROUND_POOL
    with _BACKGROUND_POOL_LOCK:
        if _BACKGROUND_POOL is None:
            from concurrent.futures import ThreadPoolExecutor
            _BACKGROUND_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bg")
    return _BACKGROUND_POOL.submit(fn, *args)


_LIFECYCLE_POOL = None


def submit_lifecycle(fn, *args):
    """Proxy start/stop workers (they block on Popen / process exit). A pool: threads are
    created once and reused, so the GUI thread does not wait for a new thread to start
    on every start, stop or restart of every proxy."""
    global _LIFECYCLE_POOL
    with _BACKGROUND_POOL_LOCK:
        if _LIFECYCLE_POOL is None:
            from concurrent.futures import ThreadPoolExecutor
            _LIFECYCLE_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="lifecycle")
    return _LIFECYCLE_POOL.submit(fn, *args)


#: After the last proxy of a start wave was launched, background tab building waits
#: this long: geo lookups and killswitch checks of the new proxies are still arriving
TAB_BUILD_QUIET_MS = 8000
#: Telegram port field: applied this long after typing stops (or on Enter / leaving the field)
TG_PORT_APPLY_DELAY_MS = 800
TG_PORT_MIN = 1024
#: Upper bound for the exit; the window is already hidden while proxies are killed
EXIT_TIMEOUT_S = 8.0
#: Proxy health checks run in parallel during one status sweep
HEALTH_CHECK_WORKERS = 16
#: Grace period during which a newly started proxy is warming up, initializing its core,
#: and binding its listening port. Health checks during this window will not trigger
#: error state or self-recovery restarts.
STARTUP_GRACE_PERIOD_S = 20.0
#: Minimum cooldown between automatic self-recovery restarts for the same proxy instance.
SELF_RECOVERY_COOLDOWN_S = 45.0
#: Consecutive failed health sweeps required on a running proxy past warmup before self-recovery.
SELF_RECOVERY_FAIL_THRESHOLD = 3
#: Maximum consecutive self-recovery attempts before pausing to prevent infinite loops.
MAX_CONSECUTIVE_RECOVERIES = 3


def _terminate_pid(pid: int) -> bool:
    """TerminateProcess through the Win32 API: no taskkill.exe process per kill."""
    if sys.platform != "win32":
        try:
            import os
            import signal
            os.kill(pid, signal.SIGKILL)
            return True
        except Exception:
            return False
    import ctypes
    from ctypes import wintypes
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def kill_process_trees(
    root_pids: "Iterable[int]",
    current_pid: int | None = None,
    table: dict[int, tuple[int, str]] | None = None,
) -> set[int]:
    """Kill every root pid with all its descendants in one pass (like taskkill /F /T, but
    without starting a process). Never touches this process or its ancestors.
    Roots go first so a launcher cannot restart its xray while the tree is torn down."""
    import os
    if current_pid is None:
        current_pid = os.getpid()
    roots = {int(p) for p in root_pids if p}
    if not roots:
        return set()
    if table is None:
        table = _process_table()

    protected = set()
    cursor = current_pid
    while cursor in table and cursor not in protected:
        protected.add(cursor)
        cursor = table[cursor][0]
    protected.add(current_pid)

    children: dict[int, list[int]] = {}
    for pid, (ppid, _name) in table.items():
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)

    order: list[int] = []
    seen: set[int] = set()
    queue_ = [p for p in roots if p not in protected]
    while queue_:
        pid = queue_.pop(0)
        if pid in seen or pid in protected:
            continue
        seen.add(pid)
        order.append(pid)
        queue_.extend(children.get(pid, []))

    return {pid for pid in order if _terminate_pid(pid)}


def listening_pids_by_port(ports: "Iterable[int]") -> dict[int, set[int]]:
    """Pids bound to each of `ports` (TCP and UDP), from a single netstat run."""
    wanted = {int(p) for p in ports}
    found: dict[int, set[int]] = {}
    if not wanted:
        return found
    with _CONSOLE_TOOL_SLOTS:
        res = subprocess.run(
            ["netstat", "-ano"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in ("TCP", "UDP"):
            continue
        port_str = parts[1].rpartition(":")[2]
        try:
            port, pid = int(port_str), int(parts[-1])
        except ValueError:
            continue
        if port in wanted and pid:
            found.setdefault(port, set()).add(pid)
    return found


def free_ports(ports: "Iterable[int]", current_pid: int | None = None) -> set[int]:
    """Kill whatever still holds any of `ports` (and its proxy launcher). Ports that are
    already free cost nothing; the busy ones are found with one netstat run in total."""
    import os
    if current_pid is None:
        current_pid = os.getpid()
    busy = [p for p in ports if not is_port_free(int(p))]
    if not busy:
        return set()
    holders = set()
    for pids in listening_pids_by_port(busy).values():
        holders |= pids
    holders.discard(current_pid)
    if not holders:
        return set()
    table = _process_table()
    return kill_process_trees(_expand_to_proxy_supervisors(holders, current_pid, table), current_pid, table)


def kill_processes_on_port(port: int, current_pid: int | None = None) -> tuple[bool, str]:
    """Force kill processes holding the given port on Windows."""
    import os
    if current_pid is None:
        current_pid = os.getpid()

    try:
        pids_to_kill = {pid for pid in listening_pids_by_port([port]).get(port, set()) if pid != current_pid}
    except Exception as e:
        return False, f"Port scan error: {e}"

    if not pids_to_kill:
        if is_port_free(port):
            return True, t("port_already_free", port=port)
        return False, t("port_busy_wait", port=port)

    table = _process_table()
    pids_to_kill = _expand_to_proxy_supervisors(pids_to_kill, current_pid, table)
    killed = kill_process_trees(pids_to_kill, current_pid, table)

    time.sleep(0.4)
    if is_port_free(port) or killed:
        return True, t("port_freed", port=port, count=len(killed))
    missed = ", ".join(str(p) for p in sorted(pids_to_kill - killed))
    return False, t("port_fail_free", port=port, error=f"PID {missed}")


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
            if "telegram_proxy" not in inst and "TelegramProxy" not in inst:
                inst["telegram_proxy"] = False
                inst["TelegramProxy"] = False
        elif port == 1030 or "worproxy" in str(inst.get("name", "")).lower():
            if "work_proxy" not in inst and "Work_Proxy" not in inst:
                inst["work_proxy"] = True
                inst["Work_Proxy"] = True
            if "system_proxy" not in inst and "System_Proxy" not in inst:
                inst["system_proxy"] = False
                inst["System_Proxy"] = False
            if "telegram_proxy" not in inst and "TelegramProxy" not in inst:
                inst["telegram_proxy"] = False
                inst["TelegramProxy"] = False

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
            "telegram_proxy": False,
            "TelegramProxy": False,
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
    """Restart WSL on purpose; the lock tells the WSL watchdog this is not a hang."""
    with WSL_OPERATION_LOCK:
        return _restart_wsl_unlocked()


def _restart_wsl_unlocked() -> tuple[bool, str]:
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
def make_telegram_icon(size: int) -> Optional["Image.Image"]:
    """Telegram logo (white paper plane on a blue circle), drawn at 4x and scaled down
    for smooth edges. None when Pillow is missing."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, big - 1, big - 1), fill=(42, 171, 238, 255))
    s = big / 100.0
    plane = [(22, 49), (76, 27), (67, 73), (51, 61), (43, 70), (41, 58), (64, 36), (37, 55)]
    draw.polygon([(x * s, y * s) for x, y in plane], fill=(255, 255, 255, 255))
    fold = [(41, 58), (43, 70), (47, 60)]
    draw.polygon([(x * s, y * s) for x, y in fold], fill=(200, 218, 234, 255))
    return img.resize((size, size), Image.LANCZOS)


def telegram_photo(widget: tk.Misc, size: int):
    """Tk image of the Telegram logo (kept alive by the caller), or None."""
    img = make_telegram_icon(size)
    if img is None:
        return None
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(img, master=widget)
    except Exception:
        return None


@functools.lru_cache(maxsize=8)
def make_tray_icon(color: str = "green") -> "Image.Image":
    """Create system tray icon image (cached: it is asked for on every status change)."""
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
    process: Optional[subprocess.Popen] = None
    running: bool = False
    healthy: bool = False
    is_transitioning: bool = False
    _should_run: bool = False

    def __init__(self, app: "VlessApp", cfg: dict, global_id: int):
        self.app = app
        self.cfg = cfg
        self.global_id = global_id
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.healthy = False
        self.is_transitioning = False
        self.log_lines: deque[str] = deque(maxlen=LOG_WIDGET_MAX_LINES)
        self._pending_log: list[str] = []
        self._log_lock = threading.Lock()
        self._log_flush_scheduled = False
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

        # Group selector (Proxies tab)
        self.group_menu_btn: Optional[tk.Menubutton] = None
        self.group_menu: Optional[tk.Menu] = None
        self._group_menu_vars: dict[str, tk.BooleanVar] = {}

        # Reconnect state
        #: True once the proxy was started (or adopted) and until the user stops it
        self._should_run = False
        self._manual_stop = False
        self._reconnect_attempt = 0
        self._reconnect_timer_id: Optional[str] = None
        self._upstream_failures = 0
        self._last_tunnel_probe = 0.0
        self._start_time = 0.0
        self._was_healthy = False
        self._last_recovery_restart = 0.0
        self._recovery_restart_count = 0
        self._consecutive_unhealthy = 0

        # Flags: System_Proxy & Work_Proxy & TelegramProxy
        self.system_proxy_var: Optional[tk.BooleanVar] = None
        self.work_proxy_var: Optional[tk.BooleanVar] = None
        self.telegram_proxy_var: Optional[tk.BooleanVar] = None


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
        self.app.update_overview_card(self)

    def set_work_proxy(self, val: bool) -> None:
        """Dynamically set Work_Proxy flag and persist."""
        b = bool(val)
        self.cfg["work_proxy"] = b
        self.cfg["Work_Proxy"] = b
        if self.work_proxy_var is not None:
            self.work_proxy_var.set(b)
        self.app.save_all()
        self.app.update_overview_card(self)

    def _on_system_proxy_toggled(self) -> None:
        val = bool(self.system_proxy_var.get() if self.system_proxy_var else False)
        self.cfg["system_proxy"] = val
        self.cfg["System_Proxy"] = val
        self.app.save_all()
        self.app.update_overview_card(self)

    def _on_work_proxy_toggled(self) -> None:
        val = bool(self.work_proxy_var.get() if self.work_proxy_var else False)
        self.cfg["work_proxy"] = val
        self.cfg["Work_Proxy"] = val
        self.app.save_all()
        self.app.update_overview_card(self)

    def is_telegram_proxy(self) -> bool:
        """Check if instance is flagged as TelegramProxy."""
        if self.telegram_proxy_var is not None:
            return bool(self.telegram_proxy_var.get())
        if "telegram_proxy" in self.cfg:
            return bool(self.cfg["telegram_proxy"])
        if "TelegramProxy" in self.cfg:
            return bool(self.cfg["TelegramProxy"])
        return False

    def _ensure_fallback_proxy(self) -> None:
        """WireGuard split tunnel: send traffic outside AllowedIPs through the System Proxy socket.

        Only fills the default; an explicit "fallbackProxy" in the instance config (even "") is kept.
        """
        if "fallbackProxy" in self.cfg:
            return
        if not str(self.cfg.get("url", "")).startswith(("wireguard://", "wg://")):
            return
        if self.is_system_proxy():
            return
        system_socket = str(settings_manager.get_setting("system_proxy", "127.0.0.1:1015") or "").strip()
        if system_socket:
            self.cfg["fallbackProxy"] = system_socket

    def set_telegram_proxy(self, val: bool) -> None:
        """Dynamically set TelegramProxy flag and persist."""
        b = bool(val)
        self.cfg["telegram_proxy"] = b
        self.cfg["TelegramProxy"] = b
        if self.telegram_proxy_var is not None:
            self.telegram_proxy_var.set(b)
        self.app.save_all()
        if hasattr(self.app, "_update_telegram_dispatcher_pool"):
            self.app._update_telegram_dispatcher_pool()
        if hasattr(self.app, "zapret_watcher") and self.app.zapret_watcher:
            self.app.zapret_watcher.trigger_immediate_check()
        self.app.update_overview_card(self)

    def _on_telegram_proxy_toggled(self) -> None:
        val = bool(self.telegram_proxy_var.get() if self.telegram_proxy_var else False)
        self.cfg["telegram_proxy"] = val
        self.cfg["TelegramProxy"] = val
        self.app.save_all()
        if hasattr(self.app, "_update_telegram_dispatcher_pool"):
            self.app._update_telegram_dispatcher_pool()
        if hasattr(self.app, "zapret_watcher") and self.app.zapret_watcher:
            self.app.zapret_watcher.trigger_immediate_check()
        self.app.update_overview_card(self)



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
        self.app.update_overview_card(self)
        self.app.refresh_current_page_tabs()
        if self.name_label and self.name_label.winfo_exists():
            self.name_label.config(text=self.get_display_name())

    def prompt_rename(self) -> None:
        self.app.prompt_rename_proxy(self)

    def prompt_reorder(self) -> None:
        self.app.prompt_reorder_proxy(self)

    def get_group_ids(self, known_ids: Optional[set[str]] = None) -> list[str]:
        """Ids of the groups this proxy is in (empty list = Unsorted)."""
        if known_ids is None:
            known_ids = {g["id"] for g in proxy_groups.load_groups()}
        return proxy_groups.group_ids_of(self.cfg, known_ids)

    def set_group_membership(self, group_id: str, member: bool) -> None:
        """Add this proxy to a group or take it out. Only the reference changes:
        the proxy, its port and its other groups stay as they are."""
        if member:
            changed = proxy_groups.add_group_ref(self.cfg, group_id)
        else:
            changed = proxy_groups.remove_group_ref(self.cfg, group_id)
        if changed:
            self.app.save_all()
            self.app.refresh_overview()
        self._sync_group_selector()

    def clear_groups(self) -> None:
        """Take the proxy out of every group (it becomes Unsorted)."""
        if proxy_groups.group_ids_of(self.cfg):
            proxy_groups.set_group_ids(self.cfg, [])
            self.app.save_all()
            self.app.refresh_overview()
        self._sync_group_selector()

    def _sync_group_selector(self) -> None:
        """Refresh the Proxies-tab group menu: its title and one checkbox per group."""
        btn = getattr(self, "group_menu_btn", None)
        if btn is None:
            return
        try:
            if not btn.winfo_exists():
                return
        except Exception:
            return
        groups = proxy_groups.load_groups()
        member_of = set(self.get_group_ids({g["id"] for g in groups}))
        names = [g["name"] for g in groups if g["id"] in member_of]
        btn.config(text=(f"📁 {', '.join(names)}" if names else f"📂 {t('group_unsorted')}") + "  ▾")

        menu = self.group_menu
        menu.delete(0, tk.END)
        self._group_menu_vars = {}
        for g in groups:
            var = tk.BooleanVar(value=g["id"] in member_of)
            self._group_menu_vars[g["id"]] = var
            menu.add_checkbutton(
                label=g["name"], variable=var,
                command=lambda gid=g["id"], v=var: self.set_group_membership(gid, bool(v.get())),
            )
        if groups:
            menu.add_separator()
        menu.add_command(label=f"📂 {t('group_menu_clear')}", command=self.clear_groups)
        menu.add_command(label=t("group_menu_new"), command=self._create_group_from_tab)

    def _create_group_from_tab(self) -> None:
        group = self.app.prompt_new_group(self.app, suggested=self.app.suggest_group_name())
        if group is not None:
            self.app.on_groups_changed()
            self.set_group_membership(group["id"], True)

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
            relief=tk.FLAT, padx=14, pady=3, command=self.toggle_async,
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

        self.telegram_proxy_var = tk.BooleanVar(value=self.is_telegram_proxy())
        tp_chk = tk.Checkbutton(
            flags_frame, text=f" {t('lbl_telegram_proxy')}", variable=self.telegram_proxy_var,
            font=("Segoe UI", 9, "bold"), fg=C["peach"], bg=bg,
            selectcolor=C["card"], activebackground=bg, activeforeground=C["peach"],
            command=self._on_telegram_proxy_toggled,
        )
        tp_chk.pack(side=tk.LEFT, padx=(0, 12))

        # 3.7. Group membership
        group_frame = tk.Frame(self.frame, bg=bg)
        group_frame.pack(fill=tk.X, padx=px, pady=(2, 4))

        tk.Label(
            group_frame, text=t("lbl_group"), font=("Segoe UI", 9, "bold"),
            fg=C["subtext"], bg=bg
        ).pack(side=tk.LEFT, padx=(0, 8))

        # A proxy can be in several groups: a menu of checkboxes, not a single choice
        self.group_menu_btn = tk.Menubutton(
            group_frame, text="", font=("Segoe UI", 9, "bold"), relief=tk.FLAT,
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"], activeforeground=C["blue"],
            padx=10, pady=3, cursor="hand2", anchor="w",
        )
        self.group_menu = tk.Menu(
            self.group_menu_btn, tearoff=0, bg=C["surface"], fg=C["text"],
            activebackground=C["hover"], activeforeground=C["blue"], font=("Segoe UI", 9),
            selectcolor=C["blue"],
        )
        self.group_menu_btn.config(menu=self.group_menu)
        self.group_menu_btn.pack(side=tk.LEFT)
        self._sync_group_selector()

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

    def set_killswitch(self, val: bool) -> None:
        """Turn the killswitch on/off from anywhere (Overview menu, Proxies tab checkbox)."""
        if self.killswitch_var is not None:
            self.killswitch_var.set(bool(val))
        self._apply_killswitch(bool(val))

    def _on_killswitch_toggled(self):
        self._apply_killswitch(bool(self.killswitch_var.get() if self.killswitch_var else False))

    def _apply_killswitch(self, val: bool) -> None:
        self.cfg["killswitch"] = val
        if self.ks_badge:
            self.ks_badge.config(
                text=f"[{t('ks_active')}]" if val else f"[{t('ks_off')}]",
                fg=C["green"] if val else C["muted"],
            )
        self.app.save_all()
        self.app.update_overview_card(self)
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
            from vless2socks.ipcheck import check_ip_leak, is_system_tun_active
            # A handful at a time: with dozens of killswitch proxies starting together, every
            # check at once meant a burst of connections and route lookups
            with _LEAK_CHECK_SLOTS:
                self._log("🛡️ [KILLSWITCH] Проверка защиты от утечек...")
                is_leak, direct_ip, tunnel_ip, msg = check_ip_leak(host, port)
                # Looked up here, in the worker: on_done runs on the GUI thread
                system_tun = is_system_tun_active() if is_leak else False

            def on_done():
                if is_leak:
                    if system_tun:
                        self._log(f"ℹ️ [KILLSWITCH] Обнаружен системный туннель (Throne). Выходной IP ({tunnel_ip}) совпадает с системным. Прокси продолжает безопасную работу.")
                        return
                    self._log(f"⚠️ [KILLSWITCH ALERT] УТЕЧКА ОБНАРУЖЕНА! Реальный IP ({direct_ip}) == IP прокси ({tunnel_ip})")
                    self._log("🛡️ [KILLSWITCH] Немедленная остановка прокси для предотвращения утечки!")
                    self.stop_async(callback=lambda *_: self._set_state("error"))
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

        return submit_background(worker)

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

        def _mark_stopped():
            self.geo_info["verified"] = False
            self.geo_info["ip"] = ""
            if self.frame and self.geo_card_label:
                try:
                    self.geo_card_label.config(text=f"{self._format_geo_text()} ({t('status_stopped')})")
                except Exception:
                    pass
            self._log(f"Geo IP: {t('status_stopped')} (порт {port} не отвечает)")

        if not self.running:
            _mark_stopped()
            return

        if self.geo_card_label:
            try:
                self.geo_card_label.config(text=t("geo_checking"))
            except Exception:
                pass

        url = self.url_entry.get().strip() if self.url_entry else self.cfg.get("url", "")

        def _on_result(data: dict[str, Any]):
            self.geo_info.update(data)
            self._log(f"Geo IP: {self.geo_info.get('flag')} {self.geo_info.get('country')} ({self.geo_info.get('ip') or 'no IP'})")
            if self.frame and self.geo_card_label:
                try:
                    self.app.after(0,lambda: self.geo_card_label.config(text=self._format_geo_text()))
                except Exception:
                    pass
            self.app.update_overview_card(self)

        def _probe():
            # The port probe can take up to its full timeout, so it must not run on the GUI thread
            if not is_port_alive(host, port):
                self._dispatch_ui(_mark_stopped)
                return
            geo_ip.fetch_geo_async(host, port, _on_result, fallback_url=url)

        submit_background(_probe)

    def _dispatch_ui(self, fn):
        """Safely schedule a function on the main thread, or invoke directly if not in Tk mainloop or test environment."""
        try:
            self.app.after(0, fn)
        except Exception:
            try:
                fn()
            except Exception:
                pass

    def _safe_log(self, msg: str):
        self._dispatch_ui(lambda: self._log(msg))

    def toggle(self, blocking: bool = True):
        if blocking:
            if self.running or (self.process and self.process.poll() is None):
                self.stop(blocking=True)
            else:
                self.start(blocking=True)
        else:
            self.toggle_async()

    def toggle_async(self):
        if self.is_transitioning:
            return
        if self.running or (self.process and self.process.poll() is None):
            self.stop_async()
        else:
            self.start_async()

    def free_port(self):
        port_str = self.port_entry.get().strip() if self.port_entry else self.get_listen()[1]
        try:
            port = int(port_str or "1081")
        except ValueError:
            messagebox.showwarning("vless2socks", t("port_invalid"))
            return

        if self.running:
            self.stop(blocking=True)

        self._log(t("port_freeing", port=port, http_port=port + 10000))
        ok, msg = kill_processes_on_port(port)
        kill_processes_on_port(port + 10000)
        self._log(msg)
        if ok:
            messagebox.showinfo("vless2socks", msg)
            self._set_state("stopped")
        else:
            messagebox.showwarning("vless2socks", msg)

    def start(self, blocking: bool = True):
        """Start proxy instance. If blocking=True, executes synchronously (test compatible)."""
        if not blocking:
            self.start_async()
            return
        self._start_internal()

    def _start_internal(self):
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
                    free_ports([p_int, http_p_int])
                    time.sleep(0.3)
            except Exception as e:
                self._log(f"Takeover error: {e}")

        self.cfg["url"] = url
        self.cfg["listen"] = f"{host}:{port}"
        if self.killswitch_var is not None:
            self.cfg["killswitch"] = bool(self.killswitch_var.get())
        self._ensure_fallback_proxy()
        # Only now, with a valid URL: from here on the proxy is expected to stay up until stopped
        self._should_run = True
        self._init_geo_from_url()

        # Write instance config
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)

        self._log(f"Starting proxy on {host}:{port}...")
        self._set_state("starting")

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
        self.healthy = False
        self.is_transitioning = False
        now = time.time()
        self._start_time = now
        self._last_tunnel_probe = now + 15.0
        self._upstream_failures = 0
        self._consecutive_unhealthy = 0
        self._was_healthy = False
        self._log(f"Started (PID {self.process.pid})")
        self.app.request_save()
        self.app.update_tray_icon()
        self.app.update_overview_card(self)

        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._watcher, daemon=True).start()

        self.app.after(3000, self.check_geo_now)

        if self.cfg.get("killswitch", False):
            self._log("🛡️ [KILLSWITCH] Активен: весь трафик привязан к прокси, проверка утечек запущена...")
            self.app.after(3500, lambda: self.verify_killswitch(manual=False))

    def start_async(self, callback: callable = None):
        """Asynchronously start proxy in a worker thread without blocking the GUI event loop."""
        if self.is_transitioning:
            return
        if self.process and self.process.poll() is None:
            return

        self._manual_stop = False
        self._cancel_reconnect()

        # Gather inputs safely on the main thread
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

        self.cfg["url"] = url
        self.cfg["listen"] = f"{host}:{port}"
        if self.killswitch_var is not None:
            self.cfg["killswitch"] = bool(self.killswitch_var.get())
        self._ensure_fallback_proxy()
        # Only now, with a valid URL: from here on the proxy is expected to stay up until stopped
        self._should_run = True
        self._init_geo_from_url()

        self.is_transitioning = True
        self._set_state("starting")
        self.app.update_overview_card(self)
        self._log(f"Starting proxy on {host}:{port}...")

        def _bg_worker():
            try:
                # Force Port Takeover if enabled in Options
                if settings_manager.get_setting("force_port_takeover", True):
                    try:
                        p_int = int(port)
                        http_p_int = self.get_http_port()
                        if not is_port_free(p_int, host) or not is_port_free(http_p_int, host):
                            self._safe_log(f"Force takeover: reclaiming busy ports {p_int} & {http_p_int}...")
                            free_ports([p_int, http_p_int])
                            time.sleep(0.3)
                    except Exception as e:
                        self._safe_log(f"Takeover error: {e}")

                # Write instance config
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(self.cfg, f, indent=2, ensure_ascii=False)

                global _xray_fetch_failed
                if not _xray_fetch_failed and xray_required_but_missing(self.config_path):
                    self._safe_log("xray-core required, fetching in background...")
                    emit = lambda msg: self._safe_log(msg)
                    if not download_xray(emit):
                        raise RuntimeError("Failed to download xray-core")

                cmd = proxy_command(self.config_path)
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=str(ROOT_DIR),
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                self._dispatch_ui(lambda: self._on_start_success(proc, callback))
                # queued after the success above, so the GUI sees "started" before any exit
                threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
                threading.Thread(target=self._watcher, args=(proc,), daemon=True).start()
            except Exception as e:
                self._dispatch_ui(lambda: self._on_start_failed(e, callback))

        submit_lifecycle(_bg_worker)

    def _on_start_success(self, proc, callback=None):
        self.process = proc
        self.running = True
        self.healthy = False
        self.is_transitioning = False
        now = time.time()
        self._start_time = now
        self._last_tunnel_probe = now + 15.0
        self._upstream_failures = 0
        self._consecutive_unhealthy = 0
        self._was_healthy = False
        self._log(f"Started (PID {proc.pid})")
        self.app.request_save()
        self.app.update_tray_icon()
        self.app.update_overview_card(self)
        self.app._update_header_stats()
        # The output reader and the process watcher were started by the start worker
        # (_bg_worker): Thread.start() waits for the new thread to run, and doing that twice
        # per proxy on the GUI thread added up during a start wave.

        self.app.after(3000, self.check_geo_now)
        if self.cfg.get("killswitch", False):
            self._log("🛡️ [KILLSWITCH] Активен: весь трафик привязан к прокси, проверка утечек запущена...")
            self.app.after(3500, lambda: self.verify_killswitch(manual=False))

        if callback:
            try:
                callback(True)
            except Exception:
                pass

    def _on_start_failed(self, err, callback=None):
        self.is_transitioning = False
        self._log(f"Launch error: {err}")
        self._set_state("error")
        self.app.update_overview_card(self)
        if callback:
            try:
                callback(False)
            except Exception:
                pass

    def _fetch_xray_then_start(self):
        """Скачать xray-core в фоне и повторить запуск, когда он появится."""
        if not _xray_fetch_lock.acquire(blocking=False):
            self._log("xray-core уже скачивается — повторю через 5 с.")
            self.app.after(5000, self.start_async)
            return

        self._set_state("starting")
        self._log("Профиль требует xray-core (reality / xtls / ws).")

        def worker():
            emit = lambda msg: self.app.after(0, self._log, msg)
            try:
                ok = download_xray(emit)
            finally:
                _xray_fetch_lock.release()
            self.app.after(0, self.start_async if ok else lambda: self._set_state("error"))

        threading.Thread(target=worker, daemon=True).start()

    def stop(self, blocking: bool = True):
        """Stop proxy instance. If blocking=True, executes synchronously (test compatible)."""
        if not blocking:
            self.stop_async()
            return
        self._stop_internal()

    def _stop_internal(self):
        self._manual_stop = True
        self._should_run = False
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
                if sys.platform == "win32":
                    kill_process_trees([proc.pid])  # launcher + its xray, without a taskkill process
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception as e:
                self._log(f"Stop error: {e}")

        try:
            free_ports([port, http_port])
        except Exception as e:
            self._log(f"Port cleanup error: {e}")

        for cfg_name in (f"xray-config-{port}.json", "xray-config.json"):
            cfg_p = ROOT_DIR / "runtime" / cfg_name
            if cfg_p.exists():
                try:
                    cfg_p.unlink()
                except Exception:
                    pass

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
        self.is_transitioning = False
        self._start_time = 0.0
        self._consecutive_unhealthy = 0
        self._upstream_failures = 0
        self._recovery_restart_count = 0
        self._set_state("stopped")
        self._log("Stopped")
        self.app.update_tray_icon()
        self.app.update_overview_card(self)
        self.app._update_header_stats()

    def stop_async(self, callback: callable = None):
        """Asynchronously stop proxy in a worker thread without blocking the GUI event loop."""
        if self.is_transitioning:
            return
        if not self.running and not self.process:
            if callback:
                try:
                    callback()
                except Exception:
                    pass
            return

        self._manual_stop = True
        self._should_run = False
        self._cancel_reconnect()
        self._reconnect_attempt = 0
        self.is_transitioning = True
        self._set_state("stopping")
        self._log("Stopping...")
        self.app.update_overview_card(self)

        proc = self.process
        self.process = None
        h, port_str = self.get_listen()
        try:
            port = int(port_str)
            http_port = self.get_http_port()
        except ValueError:
            port = 1081
            http_port = 11081

        def _bg_worker():
            if proc is not None:
                try:
                    if sys.platform == "win32":
                        kill_process_trees([proc.pid])  # launcher + its xray, without a taskkill process
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                except Exception as e:
                    self._safe_log(f"Stop error: {e}")

            try:
                free_ports([port, http_port])
            except Exception as e:
                self._safe_log(f"Port cleanup error: {e}")

            for cfg_name in (f"xray-config-{port}.json", "xray-config.json"):
                cfg_p = ROOT_DIR / "runtime" / cfg_name
                if cfg_p.exists():
                    try:
                        cfg_p.unlink()
                    except Exception:
                        pass

            geo_ip.invalidate_cache(h, port)
            self._dispatch_ui(lambda: self._on_stop_success(h, port, callback))

        submit_lifecycle(_bg_worker)

    def _on_stop_success(self, host, port, callback=None):
        self.running = False
        self.healthy = False
        self.is_transitioning = False
        self._start_time = 0.0
        self._consecutive_unhealthy = 0
        self._upstream_failures = 0
        self._recovery_restart_count = 0
        self.geo_info["verified"] = False
        self.geo_info["ip"] = ""
        if self.frame and self.geo_card_label:
            try:
                self.geo_card_label.config(text=self._format_geo_text())
            except Exception:
                pass
        self._set_state("stopped")
        self._log("Stopped")
        self.app.update_tray_icon()
        self.app.update_overview_card(self)
        self.app._update_header_stats()
        if callback:
            try:
                callback()
            except Exception:
                pass

    def restart(self, blocking: bool = True):
        """Cleanly restart this proxy instance."""
        if blocking:
            url = self.cfg.get("url", "").strip()
            if not url and self.url_entry:
                try:
                    url = self.url_entry.get().strip()
                except Exception:
                    pass
            if not url:
                return
            if self.running or (self.process and self.process.poll() is None):
                self.stop(blocking=True)
                time.sleep(0.3)
            self.start(blocking=True)
        else:
            self.restart_async()

    def restart_async(self, callback: callable = None):
        """Asynchronously and cleanly restart this proxy instance."""
        url = self.cfg.get("url", "").strip()
        if not url and self.url_entry:
            try:
                url = self.url_entry.get().strip()
            except Exception:
                pass
        if not url:
            if callback:
                try:
                    callback(False)
                except Exception:
                    pass
            return
        if self.running or (self.process and self.process.poll() is None):
            self.stop_async(callback=lambda: self.start_async(callback=callback))
        else:
            self.start_async(callback=callback)

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
            self.restart_async()
        else:
            self.start_async()

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
        self._should_run = False
        self._cancel_reconnect()
        self.stop()
        if self.config_path.exists():
            try:
                self.config_path.unlink()
            except Exception:
                pass

    def _reader(self, proc=None):
        proc = proc or self.process
        if not proc or not proc.stderr:
            return
        try:
            while True:
                raw = proc.stderr.readline()
                # Anything but a non-empty bytes line means the pipe is closed
                if not isinstance(raw, bytes) or not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._log(line)
                    if "готов:" in line or "ready:" in line.lower():
                        self.healthy = True
                        self._reconnect_attempt = 0
                        if self.frame:
                            self.app.after(0,lambda: self._set_state("running"))
        except Exception:
            pass

    def _watcher(self, proc=None):
        proc = proc or self.process
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
            free_ports([p, self.get_http_port()])
            geo_ip.invalidate_cache(h, p)
        except Exception:
            pass
        try:
            if self.frame:
                self.app.after(0,lambda: self._set_state("error"))
            self.app.after(0, self.app.update_tray_icon)
            self.app.update_overview_card(self)

            if self.is_auto_reconnect_enabled():
                self.app.after(0, self._schedule_reconnect)
        except Exception:
            pass

    def check_health_sync(self, timeout: float = 0.6) -> bool:
        """Non-UI health check method safe to run in a background worker thread."""
        h, p_str = self.get_listen()
        try:
            p = int(p_str)
        except ValueError:
            return False

        # 1. Quick local loopback port check
        if not is_port_alive(h, p, timeout=timeout):
            return False

        # 2. If running, periodically verify actual outbound tunnel connectivity
        if self.running and not self.is_transitioning:
            now = time.time()
            start_time = getattr(self, "_start_time", 0.0)
            # Never run active tunnel probe during startup grace period
            if (now - start_time) < STARTUP_GRACE_PERIOD_S:
                return True

            if now - getattr(self, "_last_tunnel_probe", 0.0) >= 20.0:
                self._last_tunnel_probe = now
                try:
                    ok, _ = probe_socks5_tunnel(h, p, timeout=2.5)
                    if not ok:
                        self._upstream_failures = getattr(self, "_upstream_failures", 0) + 1
                        if self._upstream_failures >= SELF_RECOVERY_FAIL_THRESHOLD:
                            return False
                    else:
                        self._upstream_failures = 0
                except Exception:
                    pass

        return True

    def apply_health_status(self, alive: bool) -> bool:
        """Apply health status on main thread. Returns True if status changed."""
        if self.is_transitioning:
            return False

        changed = False
        now = time.time()

        if self.running:
            if alive:
                self._upstream_failures = 0
                self._consecutive_unhealthy = 0
                self._recovery_restart_count = 0
                self._was_healthy = True
                if not self.healthy:
                    self.healthy = True
                    self._reconnect_attempt = 0
                    self._set_state("running")
                    self.app.update_overview_card(self)
                    changed = True
            elif not alive:
                has_live_process = bool(self.process and self.process.poll() is None)
                if has_live_process:
                    start_time = getattr(self, "_start_time", 0.0)
                    is_warming_up = (now - start_time) < STARTUP_GRACE_PERIOD_S
                    if is_warming_up:
                        # Process is still warming up. Keep 'starting' state, never restart during warmup.
                        if self.healthy:
                            self.healthy = False
                            self._set_state("starting")
                            self.app.update_overview_card(self)
                            changed = True
                        return changed

                    # Process has passed warmup window and is unhealthy
                    self._consecutive_unhealthy = getattr(self, "_consecutive_unhealthy", 0) + 1
                    if self.healthy:
                        self.healthy = False
                        self._set_state("error")
                        self.app.update_overview_card(self)
                        changed = True

                    # Only consider self-recovery after confirmed consecutive failures
                    if (
                        not self._manual_stop
                        and self.is_auto_reconnect_enabled()
                        and self._consecutive_unhealthy >= SELF_RECOVERY_FAIL_THRESHOLD
                    ):
                        last_rec = getattr(self, "_last_recovery_restart", 0.0)
                        rec_count = getattr(self, "_recovery_restart_count", 0)
                        time_since_rec = now - last_rec

                        if time_since_rec < SELF_RECOVERY_COOLDOWN_S:
                            # In cooldown: do not restart in a rapid loop
                            return changed

                        if rec_count >= MAX_CONSECUTIVE_RECOVERIES:
                            # Max recovery attempts reached; back off to prevent endless thrashing
                            self._log(
                                f"⚠️ [SelfRecovery] Max restart attempts ({MAX_CONSECUTIVE_RECOVERIES}) reached for live process. Pausing auto-recovery."
                            )
                            self._last_recovery_restart = now + 60.0  # extended backoff
                            return changed

                        self._last_recovery_restart = now
                        self._recovery_restart_count = rec_count + 1
                        self._consecutive_unhealthy = 0
                        self._log(
                            f"🔄 [SelfRecovery] Dead upstream tunnel detected with live process (recovery attempt #{self._recovery_restart_count}). Restarting instance..."
                        )
                        self.restart(blocking=False)
                elif self._reconnect_timer_id is None:
                    # Nothing of ours is behind this instance any more - e.g. a process adopted from a
                    # previous GUI session has died. Drop the stale "running" flag, otherwise
                    # _schedule_reconnect() refuses to act and the proxy stays down forever.
                    self.healthy = False
                    self.running = False
                    self.process = None
                    self._set_state("error")
                    if not self._manual_stop and self.is_auto_reconnect_enabled():
                        self._schedule_reconnect()
                    self.app.update_overview_card(self)
                    changed = True
        else:
            if alive:
                if self._manual_stop:
                    # User stopped proxy, but an orphan process is still holding the port
                    h, port_str = self.get_listen()
                    try:
                        p = int(port_str)
                        threading.Thread(
                            target=lambda: (free_ports([p, self.get_http_port()]), geo_ip.invalidate_cache(h, p)),
                            daemon=True
                        ).start()
                    except Exception:
                        pass
                elif self._reconnect_timer_id is not None:
                    pass
                else:
                    # Preexisting process detected on port
                    self.running = True
                    self.healthy = True
                    self._should_run = True
                    self._was_healthy = True
                    self._start_time = now - STARTUP_GRACE_PERIOD_S
                    self._set_state("running")
                    self.app.update_overview_card(self)
                    changed = True
            else:
                if self.healthy:
                    self.healthy = False
                    self._set_state("stopped")
                    self.app.update_overview_card(self)
                    changed = True
                # Safety net: the proxy is supposed to be up, nothing is running and no retry is
                # pending (a failed launch, a watcher thread that never got to schedule one, ...)
                if (
                    self._should_run
                    and not self._manual_stop
                    and self._reconnect_timer_id is None
                    and self.is_auto_reconnect_enabled()
                ):
                    self._schedule_reconnect()
                    changed = True
        return changed

    def poll_health(self):
        """Synchronous poll method for backwards compatibility."""
        alive = self.check_health_sync()
        if self.apply_health_status(alive):
            self.app.update_tray_icon()
            self.app.update_overview_card(self)
            self.app._update_header_stats()

    def _set_state(self, state: str):
        if threading.current_thread() is not threading.main_thread():
            self._dispatch_ui(lambda s=state: self._set_state(s))
            return
        h, port = self.get_listen()
        http_port = self.get_http_port()
        states = {
            "stopped":  (C["red"],    t("status_stopped"), t("btn_on"),  C["green"]),
            "starting": (C["yellow"], t("status_starting"), "⏳ ...",    C["yellow"]),
            "running":  (C["green"],  t("status_running", port=port, http_port=http_port), t("btn_off"), C["red"]),
            "stopping": (C["yellow"], "Остановка...",      "⏳ ...",    C["yellow"]),
            "error":    (C["red"],    t("status_error"),   t("btn_on"),  C["green"]),
        }
        dot_color, label_text, btn_text, btn_color = states.get(state, states["stopped"])
        btn_state = tk.DISABLED if state in ("starting", "stopping") else tk.NORMAL
        try:
            if self.status_dot and self.status_dot.winfo_exists():
                self.status_dot.config(fg=dot_color)
            if self.status_label and self.status_label.winfo_exists():
                self.status_label.config(text=label_text)
            if self.toggle_btn and self.toggle_btn.winfo_exists():
                self.toggle_btn.config(text=btn_text, bg=btn_color, state=btn_state)
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
            with self._log_lock:
                self._pending_log.append(line)
                schedule = not self._log_flush_scheduled
                self._log_flush_scheduled = True
            if schedule:
                try:
                    self.app.after(LOG_FLUSH_MS, self._flush_log)
                except Exception:
                    with self._log_lock:
                        self._log_flush_scheduled = False

    def _flush_log(self):
        """Write the lines collected since the last flush in one go and drop everything but the tail."""
        with self._log_lock:
            lines = self._pending_log[-LOG_WIDGET_MAX_LINES:]
            self._pending_log = []
            self._log_flush_scheduled = False
        if not self.log_text or not lines:
            return
        try:
            follow = self.log_text.yview()[1] >= 0.999  # do not yank the view while the user reads older lines
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, "\n".join(lines) + "\n")
            extra = int(self.log_text.index("end-1c").split(".")[0]) - 1 - LOG_WIDGET_MAX_LINES
            if extra > 0:
                self.log_text.delete("1.0", f"{extra + 1}.0")
            if follow:
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

        if self.telegram_proxy_var is not None:
            tp_val = bool(self.telegram_proxy_var.get())
            cfg["telegram_proxy"] = tp_val
            cfg["TelegramProxy"] = tp_val
        elif "telegram_proxy" in self.cfg or "TelegramProxy" in self.cfg:
            tp_val = bool(self.cfg.get("telegram_proxy", self.cfg.get("TelegramProxy", False)))
            cfg["telegram_proxy"] = tp_val
            cfg["TelegramProxy"] = tp_val
        else:
            tp_val = self.is_telegram_proxy()
            cfg["telegram_proxy"] = tp_val
            cfg["TelegramProxy"] = tp_val


        if "name" in self.cfg:
            cfg["name"] = self.cfg["name"]
        if "order" in self.cfg:
            cfg["order"] = self.cfg["order"]
        if "sendThrough" in self.cfg:
            cfg["sendThrough"] = self.cfg["sendThrough"]
        return cfg


def _center_over(win: tk.Toplevel, parent: tk.Misc) -> None:
    """Place a dialog at the center of its parent window, using the dialog's natural size."""
    try:
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


# ── Proxy Groups Manager Dialog ───────────────────────────────
class GroupsManagerDialog(tk.Toplevel):
    """Modal list of proxy groups (10 per page): create, rename with ✏️, delete with ✕.

    The window has no fixed size: it takes exactly the room its rows need, and the
    page switcher is shown only when there are at least two pages.
    """

    def __init__(self, app: "VlessApp"):
        super().__init__(app)
        self.app = app
        self.page = 0
        bg = C["bg"]
        self.title(t("dlg_groups_title"))
        self.configure(bg=bg)
        self.resizable(False, False)
        self.transient(app)

        header = tk.Frame(self, bg=bg)
        header.pack(fill=tk.X, padx=16, pady=(14, 8))
        tk.Label(
            header, text=f"📁 {t('dlg_groups_title')}", font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg,
        ).pack(side=tk.LEFT)
        tk.Button(
            header, text=t("btn_new_group"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2", command=self._create,
        ).pack(side=tk.RIGHT, padx=(24, 0))

        self.rows_frame = tk.Frame(self, bg=bg)
        self.rows_frame.pack(fill=tk.X, padx=16)

        self.pager = tk.Frame(self, bg=bg)
        self.prev_btn = tk.Button(
            self.pager, text="◀", font=("Segoe UI", 9, "bold"), bg=C["overlay"], fg=C["text"],
            activebackground=C["hover"], relief=tk.FLAT, padx=10, pady=1, cursor="hand2",
            command=lambda: self._go(-1),
        )
        self.prev_btn.pack(side=tk.LEFT)
        self.page_lbl = tk.Label(self.pager, text="", font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg)
        self.page_lbl.pack(side=tk.LEFT, padx=10)
        self.next_btn = tk.Button(
            self.pager, text="▶", font=("Segoe UI", 9, "bold"), bg=C["overlay"], fg=C["text"],
            activebackground=C["hover"], relief=tk.FLAT, padx=10, pady=1, cursor="hand2",
            command=lambda: self._go(1),
        )
        self.next_btn.pack(side=tk.LEFT)

        self.footer = tk.Frame(self, bg=bg)
        self.footer.pack(fill=tk.X, padx=16, pady=(10, 14))
        tk.Button(
            self.footer, text=t("btn_close_dialog"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=12, pady=3, cursor="hand2", command=self.destroy,
        ).pack(side=tk.RIGHT)

        self.bind("<Escape>", lambda e: self.destroy())
        self.render()
        _center_over(self, app)
        self._regrab()

    def _regrab(self):
        """Nested dialogs take the grab away; take it back so this window stays modal."""
        try:
            if self.winfo_exists():
                self.grab_set()
                self.focus_force()
        except Exception:
            pass

    def render(self):
        bg = C["bg"]
        for w in self.rows_frame.winfo_children():
            w.destroy()

        groups = proxy_groups.load_groups()
        counts = proxy_groups.member_counts(inst.cfg for inst in self.app.instances)

        rows, self.page = proxy_groups.page_slice(groups, self.page)
        if not groups:
            tk.Label(
                self.rows_frame, text=t("groups_empty"), font=("Segoe UI", 9, "italic"),
                fg=C["muted"], bg=bg, pady=10,
            ).pack(anchor="w")

        for g in rows:
            row = tk.Frame(self.rows_frame, bg=C["card"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row, text=f"📁 {g['name']}", font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"],
                width=28, anchor="w",
            ).pack(side=tk.LEFT, padx=(10, 6), pady=6)
            tk.Label(
                row, text=t("group_proxy_count", count=counts.get(g["id"], 0)), font=("Segoe UI", 9),
                fg=C["subtext"], bg=C["card"], width=11, anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 6))
            tk.Button(
                row, text="✕", font=("Segoe UI", 10, "bold"), bg=C["card"], fg=C["red"],
                activebackground=C["hover"], activeforeground=C["red"], relief=tk.FLAT, bd=0,
                padx=8, cursor="hand2", command=lambda grp=g: self._delete(grp),
            ).pack(side=tk.RIGHT, padx=(0, 6))
            tk.Button(
                row, text="✏️", font=("Segoe UI", 9), bg=C["card"], fg=C["subtext"],
                activebackground=C["hover"], activeforeground=C["text"], relief=tk.FLAT, bd=0,
                padx=6, cursor="hand2", command=lambda grp=g: self._rename(grp),
            ).pack(side=tk.RIGHT)

        pages = proxy_groups.page_count(len(groups))
        if pages >= 2:
            self.page_lbl.config(text=t("groups_page_info", page=self.page + 1, pages=pages))
            self.prev_btn.config(state=tk.NORMAL if self.page > 0 else tk.DISABLED)
            self.next_btn.config(state=tk.NORMAL if self.page < pages - 1 else tk.DISABLED)
            if not self.pager.winfo_manager():
                self.pager.pack(padx=16, pady=(8, 0), before=self.footer)
        else:
            self.pager.pack_forget()

        # Drop any previous size so the window shrinks or grows to its content
        self.geometry("")

    def _go(self, delta: int):
        self.page += delta
        self.render()

    def _create(self):
        group = self.app.prompt_new_group(self)
        if group is not None:
            self.page = proxy_groups.page_count(len(proxy_groups.load_groups())) - 1
            self.app.on_groups_changed()
            self.render()
        self._regrab()

    def _rename(self, group: dict):
        if self.app.prompt_rename_group(group, self):
            self.app.on_groups_changed()
            self.render()
        self._regrab()

    def _delete(self, group: dict):
        if self.app.delete_group_flow(group, self):
            self.render()
        self._regrab()


# ── Group Tab Picker Dialog ───────────────────────────────────
class GroupTabPickerDialog(tk.Toplevel):
    """Modal checklist of groups (10 per page); every checked group opens as an Overview tab.

    Groups that already have a tab are shown checked and disabled. Like the groups
    manager, the window takes only the room its rows need and shows the page switcher
    only from two pages on.
    """

    def __init__(self, app: "VlessApp"):
        super().__init__(app)
        self.app = app
        self.page = 0
        self.chosen: list[str] = []
        self._vars: dict[str, tk.BooleanVar] = {}
        bg = C["bg"]
        self.title(t("dlg_pick_groups_title"))
        self.configure(bg=bg)
        self.resizable(False, False)
        self.transient(app)

        header = tk.Frame(self, bg=bg)
        header.pack(fill=tk.X, padx=16, pady=(14, 2))
        tk.Label(
            header, text=f"📁 {t('dlg_pick_groups_title')}", font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg,
        ).pack(side=tk.LEFT)
        tk.Button(
            header, text=t("btn_new_group"), font=("Segoe UI", 9, "bold"),
            bg=C["green"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2", command=self._create,
        ).pack(side=tk.RIGHT, padx=(24, 0))
        tk.Label(self, text=t("pick_groups_hint"), font=("Segoe UI", 8), fg=C["subtext"], bg=bg).pack(anchor="w", padx=16, pady=(0, 8))

        self.rows_frame = tk.Frame(self, bg=bg)
        self.rows_frame.pack(fill=tk.X, padx=16)

        self.pager = tk.Frame(self, bg=bg)
        nav = dict(font=("Segoe UI", 9, "bold"), bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
                   relief=tk.FLAT, padx=10, pady=1, cursor="hand2")
        self.prev_btn = tk.Button(self.pager, text="◀", command=lambda: self._go(-1), **nav)
        self.prev_btn.pack(side=tk.LEFT)
        self.page_lbl = tk.Label(self.pager, text="", font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg)
        self.page_lbl.pack(side=tk.LEFT, padx=10)
        self.next_btn = tk.Button(self.pager, text="▶", command=lambda: self._go(1), **nav)
        self.next_btn.pack(side=tk.LEFT)

        self.footer = tk.Frame(self, bg=bg)
        self.footer.pack(fill=tk.X, padx=16, pady=(10, 14))
        self.open_btn = tk.Button(
            self.footer, text=t("btn_open_group_tabs"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"], disabledforeground=C["muted"],
            relief=tk.FLAT, padx=12, pady=3, cursor="hand2", command=self._confirm,
        )
        self.open_btn.pack(side=tk.RIGHT)
        tk.Button(
            self.footer, text=t("btn_cancel"), font=("Segoe UI", 9),
            bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            relief=tk.FLAT, padx=12, pady=3, cursor="hand2", command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        self.bind("<Escape>", lambda e: self.destroy())
        self.render()
        _center_over(self, app)
        self._regrab()

    def _regrab(self):
        try:
            if self.winfo_exists():
                self.grab_set()
                self.focus_force()
        except Exception:
            pass

    def render(self):
        bg = C["bg"]
        for w in self.rows_frame.winfo_children():
            w.destroy()
        groups = proxy_groups.load_groups()
        open_tabs = set(proxy_groups.get_group_tabs())
        counts = proxy_groups.member_counts(inst.cfg for inst in self.app.instances)

        rows, self.page = proxy_groups.page_slice(groups, self.page)
        if not groups:
            tk.Label(
                self.rows_frame, text=t("groups_empty"), font=("Segoe UI", 9, "italic"),
                fg=C["muted"], bg=bg, pady=10,
            ).pack(anchor="w")

        for g in rows:
            is_open = g["id"] in open_tabs
            var = self._vars.setdefault(g["id"], tk.BooleanVar(value=False))
            if is_open:
                var.set(True)
            row = tk.Frame(self.rows_frame, bg=C["card"])
            row.pack(fill=tk.X, pady=2)
            label = f"📁 {g['name']}" + (f"   {t('group_tab_already_open')}" if is_open else "")
            tk.Checkbutton(
                row, text=f" {label}", variable=var, font=("Segoe UI", 10, "bold"),
                fg=C["muted"] if is_open else C["text"], bg=C["card"], selectcolor=C["surface"],
                activebackground=C["card"], activeforeground=C["blue"], disabledforeground=C["muted"],
                anchor="w", width=30, state=tk.DISABLED if is_open else tk.NORMAL,
                command=lambda gid=g["id"]: self._toggle(gid),
            ).pack(side=tk.LEFT, padx=(8, 6), pady=4)
            tk.Label(
                row, text=t("group_proxy_count", count=counts.get(g["id"], 0)), font=("Segoe UI", 9),
                fg=C["subtext"], bg=C["card"], width=11, anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 10))

        pages = proxy_groups.page_count(len(groups))
        if pages >= 2:
            self.page_lbl.config(text=t("groups_page_info", page=self.page + 1, pages=pages))
            self.prev_btn.config(state=tk.NORMAL if self.page > 0 else tk.DISABLED)
            self.next_btn.config(state=tk.NORMAL if self.page < pages - 1 else tk.DISABLED)
            if not self.pager.winfo_manager():
                self.pager.pack(padx=16, pady=(8, 0), before=self.footer)
        else:
            self.pager.pack_forget()
        self._sync_open_btn()
        self.geometry("")

    def _toggle(self, gid: str):
        if self._vars[gid].get():
            if gid not in self.chosen:
                self.chosen.append(gid)
        elif gid in self.chosen:
            self.chosen.remove(gid)
        self._sync_open_btn()

    def _sync_open_btn(self):
        self.open_btn.config(state=tk.NORMAL if self.chosen else tk.DISABLED)

    def _go(self, delta: int):
        self.page += delta
        self.render()

    def _create(self):
        group = self.app.prompt_new_group(self, suggested=self.app.suggest_group_name())
        if group is not None:
            self.app.on_groups_changed()
            self._vars[group["id"]] = tk.BooleanVar(value=True)
            self.chosen.append(group["id"])
            self.page = proxy_groups.page_count(len(proxy_groups.load_groups())) - 1
            self.render()
        self._regrab()

    def _confirm(self):
        chosen = list(self.chosen)
        self.destroy()
        self.app.open_group_tabs(chosen)


# ── Add Subscription Wizard ───────────────────────────────────
class SubscriptionDialog(tk.Toplevel):
    """Modal wizard: subscription type → URL → target group → async import → summary."""

    def __init__(self, app: "VlessApp"):
        super().__init__(app)
        self.app = app
        bg = C["bg"]
        self.title(t("dlg_sub_title"))
        self.configure(bg=bg)
        self.resizable(False, False)
        self.transient(app)

        self.type_var = tk.StringVar(value=subscriptions.TYPE_VLESS)
        self.url_var = tk.StringVar()
        self.group_mode_var = tk.StringVar()
        self.new_group_var = tk.StringVar()
        self.start_after_var = tk.BooleanVar(value=False)
        self._busy = False

        head = tk.Frame(self, bg=bg)
        head.pack(fill=tk.X, padx=18, pady=(14, 4))
        tk.Label(head, text=f"🔗 {t('dlg_sub_title')}", font=("Segoe UI", 12, "bold"), fg=C["text"], bg=bg).pack(side=tk.LEFT)
        self.step_lbl = tk.Label(head, text="", font=("Segoe UI", 9), fg=C["subtext"], bg=bg)
        self.step_lbl.pack(side=tk.RIGHT)

        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill=tk.BOTH, padx=18, pady=(4, 6))

        self.status_lbl = tk.Label(self, text="", font=("Segoe UI", 9), fg=C["red"], bg=bg, wraplength=480, justify=tk.LEFT, anchor="w")
        self.status_lbl.pack(fill=tk.X, padx=18)

        footer = tk.Frame(self, bg=bg)
        footer.pack(fill=tk.X, padx=18, pady=(8, 14))
        btn = dict(font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=12, pady=3, cursor="hand2")
        self.next_btn = tk.Button(footer, bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"], **btn)
        self.next_btn.pack(side=tk.RIGHT)
        self.back_btn = tk.Button(footer, text=t("btn_sub_back"), bg=C["overlay"], fg=C["text"], activebackground=C["hover"], **btn)
        self.back_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self.cancel_btn = tk.Button(
            footer, text=t("btn_cancel"), bg=C["overlay"], fg=C["text"], activebackground=C["hover"],
            command=self._close, **btn,
        )
        self.cancel_btn.pack(side=tk.LEFT)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.show_type_step()
        _center_over(self, app)
        self.grab_set()
        self.focus_force()

    # Steps
    def _clear(self, step: int):
        for w in self.body.winfo_children():
            w.destroy()
        self.status_lbl.config(text="", fg=C["red"])
        self.step_lbl.config(text=t("sub_step", step=step, steps=3) if step else "")

    def _fit(self):
        self.geometry("")

    def show_type_step(self):
        self._clear(1)
        bg = C["bg"]
        tk.Label(self.body, text=t("lbl_sub_type"), font=("Segoe UI", 10, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 6))
        tk.Radiobutton(
            self.body, text=f" {t('sub_type_vless')}", variable=self.type_var, value=subscriptions.TYPE_VLESS,
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=bg, selectcolor=C["card"],
            activebackground=bg, activeforeground=C["blue"],
        ).pack(anchor="w")
        tk.Label(
            self.body, text=t("sub_type_vless_desc"), font=("Segoe UI", 8), fg=C["subtext"], bg=bg,
            wraplength=460, justify=tk.LEFT,
        ).pack(anchor="w", padx=(26, 0), pady=(0, 4))
        self.back_btn.pack_forget()
        self.next_btn.config(text=t("btn_sub_next"), command=self.show_url_step, state=tk.NORMAL)
        self._fit()

    def show_url_step(self):
        self._clear(2)
        bg = C["bg"]
        tk.Label(self.body, text=t("lbl_sub_url"), font=("Segoe UI", 10, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 6))
        entry = tk.Entry(
            self.body, textvariable=self.url_var, font=("Consolas", 10), width=56,
            bg=C["card"], fg=C["text"], insertbackground=C["text"], relief=tk.FLAT,
        )
        entry.pack(fill=tk.X, ipady=4)
        attach_clipboard_and_context_menu(entry)
        entry.bind("<Return>", lambda e: self._url_next())
        entry.focus_set()
        tk.Label(self.body, text=t("sub_url_hint"), font=("Segoe UI", 8), fg=C["subtext"], bg=bg).pack(anchor="w", pady=(4, 0))
        # side=RIGHT: packing after "Next" keeps "Back" to its left
        self.back_btn.pack(side=tk.RIGHT, padx=(0, 6), after=self.next_btn)
        self.back_btn.config(command=self.show_type_step)
        self.next_btn.config(text=t("btn_sub_next"), command=self._url_next, state=tk.NORMAL)
        self._fit()

    def _url_next(self):
        try:
            self.url_var.set(subscriptions.validate_subscription_url(self.url_var.get()))
        except subscriptions.SubscriptionError:
            self.status_lbl.config(text=t("err_sub_bad_url"))
            return
        self.show_group_step()

    def show_group_step(self):
        self._clear(3)
        bg = C["bg"]
        groups = proxy_groups.load_groups()
        self._groups = groups
        if not self.group_mode_var.get():
            self.group_mode_var.set("existing" if groups else "new")
        if not groups and self.group_mode_var.get() == "existing":
            self.group_mode_var.set("new")
        if not self.new_group_var.get():
            self.new_group_var.set(self.app.suggest_group_name())

        tk.Label(self.body, text=t("lbl_sub_group"), font=("Segoe UI", 10, "bold"), fg=C["text"], bg=bg).pack(anchor="w", pady=(0, 6))
        radio = dict(variable=self.group_mode_var, font=("Segoe UI", 9, "bold"), fg=C["text"], bg=bg,
                     selectcolor=C["card"], activebackground=bg, activeforeground=C["blue"], command=self._sync_group_inputs)

        row1 = tk.Frame(self.body, bg=bg)
        row1.pack(fill=tk.X, pady=2)
        tk.Radiobutton(row1, text=f" {t('sub_group_existing')}", value="existing",
                       state=tk.NORMAL if groups else tk.DISABLED, **radio).pack(side=tk.LEFT)
        self.group_combo = ttk.Combobox(row1, state="readonly", width=30, font=("Segoe UI", 9),
                                        values=[g["name"] for g in groups])
        if groups:
            self.group_combo.current(0)
        self.group_combo.pack(side=tk.RIGHT)

        row2 = tk.Frame(self.body, bg=bg)
        row2.pack(fill=tk.X, pady=2)
        tk.Radiobutton(row2, text=f" {t('sub_group_new')}", value="new", **radio).pack(side=tk.LEFT)
        self.new_group_entry = tk.Entry(
            row2, textvariable=self.new_group_var, font=("Segoe UI", 9), width=32,
            bg=C["card"], fg=C["text"], insertbackground=C["text"], relief=tk.FLAT,
        )
        self.new_group_entry.pack(side=tk.RIGHT, ipady=2)

        tk.Checkbutton(
            self.body, text=f" {t('sub_start_after')}", variable=self.start_after_var,
            font=("Segoe UI", 9), fg=C["text"], bg=bg, selectcolor=C["card"],
            activebackground=bg, activeforeground=C["blue"],
        ).pack(anchor="w", pady=(10, 0))
        tk.Label(
            self.body, text=t("sub_ports_hint", port=subscriptions.get_start_port()),
            font=("Segoe UI", 8), fg=C["subtext"], bg=bg,
        ).pack(anchor="w", pady=(4, 0))

        self._sync_group_inputs()
        self.back_btn.config(command=self.show_url_step)
        self.next_btn.config(text=t("btn_sub_add"), command=self.start_import, state=tk.NORMAL)
        self._fit()

    def _sync_group_inputs(self):
        existing = self.group_mode_var.get() == "existing"
        self.group_combo.config(state="readonly" if existing else tk.DISABLED)
        self.new_group_entry.config(state=tk.DISABLED if existing else tk.NORMAL)

    # Import
    def _target_group(self) -> tuple[Optional[str], Optional[str]]:
        """(existing group id, new group name); validates the new name up front."""
        if self.group_mode_var.get() == "existing":
            idx = self.group_combo.current()
            if 0 <= idx < len(self._groups):
                return self._groups[idx]["id"], None
            raise proxy_groups.GroupNameError("empty")
        name = self.new_group_var.get()
        proxy_groups.validate_new_name(name)
        return None, name

    def start_import(self):
        if self._busy:
            return
        try:
            group_id, new_name = self._target_group()
        except proxy_groups.GroupNameError as err:
            key = "err_group_name_taken" if err.code == "taken" else "err_group_name_empty"
            self.status_lbl.config(text=t(key, name=err.name), fg=C["red"])
            return

        self._busy = True
        for b in (self.next_btn, self.back_btn):
            b.config(state=tk.DISABLED)
        self.status_lbl.config(text=f"⏳ {t('sub_loading')}", fg=C["yellow"])

        url = self.url_var.get()
        existing_urls = [str(inst.cfg.get("url", "")) for inst in self.app.instances]
        fallback = self.app._subscription_fallback_proxies()
        start_after = bool(self.start_after_var.get())

        def worker():
            try:
                body, route = subscriptions.fetch_subscription(url, fallback, log=self.app._log)
                parsed = subscriptions.extract_vless_links(subscriptions.decode_subscription(body), existing_urls)
                outcome = ("ok", parsed, route)
            except subscriptions.SubscriptionError as err:
                outcome = ("error", err, None)
            except Exception as err:  # never leave the dialog stuck in "loading"
                outcome = ("error", subscriptions.SubscriptionError("network", str(err)), None)
            self.app.after(0, lambda: self._finish_import(outcome, url, group_id, new_name, start_after))

        threading.Thread(target=worker, daemon=True, name="subscription-import").start()

    def _finish_import(self, outcome, url, group_id, new_name, start_after):
        if not self.winfo_exists():
            return
        self._busy = False
        kind, payload, route = outcome
        if kind == "error":
            self._show_error(payload)
            return
        parsed: subscriptions.ParsedSubscription = payload
        if not parsed.links:
            self._show_error(subscriptions.SubscriptionError("no_new_links"), parsed)
            return
        try:
            result = self.app.add_subscription_proxies(url, parsed.links, group_id, new_name, start_after)
        except subscriptions.SubscriptionError as err:
            self._show_error(err)
            return
        except proxy_groups.GroupNameError as err:
            self._show_error(subscriptions.SubscriptionError("group", t(
                "err_group_name_taken" if err.code == "taken" else "err_group_name_empty", name=err.name)))
            return
        self.show_result(result, parsed, route)

    def _show_error(self, err: "subscriptions.SubscriptionError", parsed=None):
        messages = {
            "bad_url": t("err_sub_bad_url"),
            "network": t("err_sub_network"),
            "http_status": t("err_sub_http", code=err.detail),
            "bad_format": t("err_sub_format"),
            "too_large": t("err_sub_format"),
            "no_ports": t("err_sub_no_ports", count=err.detail),
            "group": err.detail,
        }
        if err.code == "no_new_links" and parsed is not None:
            text = t("err_sub_no_links", dup=parsed.duplicates, other=parsed.unsupported, bad=parsed.invalid)
        else:
            text = messages.get(err.code, str(err))
        self.status_lbl.config(text=f"⚠️ {text}", fg=C["red"])
        for b in (self.next_btn, self.back_btn):
            b.config(state=tk.NORMAL)
        self._fit()

    def show_result(self, result: dict, parsed: "subscriptions.ParsedSubscription", route: str):
        self._clear(0)
        bg = C["bg"]
        ports = result["ports"]
        tk.Label(
            self.body, text=f"✅ {t('sub_result_added', count=len(ports), first=ports[0], last=ports[-1], group=result['group_name'])}",
            font=("Segoe UI", 10, "bold"), fg=C["green"], bg=bg, wraplength=480, justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 6))
        tk.Label(
            self.body, text=t("sub_result_skipped", dup=parsed.duplicates, other=parsed.unsupported, bad=parsed.invalid),
            font=("Segoe UI", 9), fg=C["subtext"], bg=bg, wraplength=480, justify=tk.LEFT,
        ).pack(anchor="w")
        tk.Label(
            self.body, text=t("sub_result_route", route=t("sub_route_direct") if route == "direct" else route),
            font=("Segoe UI", 8), fg=C["muted"], bg=bg,
        ).pack(anchor="w", pady=(4, 0))
        self.back_btn.pack_forget()
        self.cancel_btn.pack_forget()
        self.next_btn.config(text=t("btn_close_dialog"), command=self._close, state=tk.NORMAL)
        self._fit()

    def _close(self):
        if self._busy:
            return  # the import is still running; its result would have nowhere to go
        self.destroy()


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

        # Telegram port: the old default 1373 becomes 9910 (once; a custom port is kept)
        try:
            settings_manager.migrate_legacy_telegram_port()
        except Exception:
            pass

        # Worker threads never touch Tk directly: their after() calls land in this queue
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._drain_ui_queue()

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
        self.restart_telegram_proxy_var = tk.BooleanVar(value=bool(settings_manager.get_setting("restart_telegram_proxy", True)))

        # Protocols & Window State Handlers
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.bind("<Unmap>", self._on_main_unmap, add="+")
        self.bind("<Deactivate>", self._on_main_deactivate, add="+")

        self._load_saved_data()

        # Zapret2 Watcher Background Service
        z_prefs = settings_manager.get_zapret2_settings()
        self.zapret_watcher = Zapret2Watcher(
            enabled=z_prefs["enabled"],
            watch_main=z_prefs["watch_main"],
            watch_tg=z_prefs["watch_tg"],
            tg_proxy_provider=self.get_telegram_proxies,
            tg_restart_handler=self.restart_telegram_proxies,
        )
        self.zapret_watcher.start()
        self._build_main_ui()
        self._update_telegram_dispatcher_pool()
        self._setup_tray()
        self._poll_loop()

        # Single-Instance IPC & Wake-from-sleep Auto-Recovery
        self._power_watcher = PowerBroadcastWatcher(on_resume=self._on_power_resume)
        self._power_watcher.start()
        self._ipc_server = SingleInstanceIpcServer(on_show=self._ipc_on_show, on_restart=self._ipc_on_restart)
        self._ipc_server.start()

        # WSL watchdog: recovers a hung WSL, leaves a healthy or stopped one alone
        self.wsl_watchdog = WslWatchdog(
            enabled=bool(settings_manager.get_setting("wsl_watchdog_enabled", True)),
            auto_recover=bool(settings_manager.get_setting("wsl_watchdog_auto_recover", True)),
            recover_gui=bool(settings_manager.get_setting("wsl_watchdog_recover_gui", False)),
            on_log=self._log,
            on_alert=self._tray_notify,
            on_change=self._tray_refresh_menu,
        )
        self.wsl_watchdog.start()

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

        title_badge = MacButton(
            self.top_bar, text=t("app_badge"), variant="primary", interactive=False,
            font=ui_font(self, 9, True), padx=10, pady=4,
        )
        title_badge.pack(side=tk.LEFT, padx=(10, 8), pady=8)
        self.title_badge = title_badge

        self.count_label = tk.Label(
            self.top_bar, text="", font=(ui_font(self).cget("family"), 9, "bold"), fg=C["text"], bg=C["surface"],
        )
        self.count_label.pack(side=tk.LEFT, padx=4)

        # Quick Add Button in header (far right)
        self.add_proxy_btn = MacButton(
            self.top_bar, text=t("btn_new_tab"), variant="success", command=self.add_new_instance,
        )
        self.add_proxy_btn.pack(side=tk.RIGHT, padx=(4, 10), pady=8)

        # RestartServices split button (directly adjacent to Add Button)
        self.restart_container = tk.Frame(self.top_bar, bg=C["surface"])
        self.restart_container.pack(side=tk.RIGHT, padx=(0, 6), pady=8)

        # its width covers every label it shows, so it never shifts its neighbours
        self.btn_restart_services = MacButton(
            self.restart_container, text=f"🔄 {t('btn_restart_services')}", variant="primary",
            command=self.execute_restart_services,
            width_labels=(f"⏳ {t('msg_restart_in_progress')}", f"✅ {t('btn_restart_services')}"),
        )
        self.btn_restart_services.pack(side=tk.LEFT)

        self.btn_restart_arrow = MacButton(
            self.restart_container, text="▼", variant="secondary", padx=8,
            font=ui_font(self, 8, True), command=self.toggle_restart_services_menu,
        )
        self.btn_restart_arrow.pack(side=tk.LEFT, padx=(3, 0), fill=tk.Y)

        # 2. Main Navigation Notebook
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        family = ui_font(self).cget("family")
        style.configure(
            "TNotebook.Tab",
            background=C["surface"], foreground=MAC["label_2"],
            padding=[14, 7], font=(family, 9, "bold"), borderwidth=0, focuscolor=C["surface"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", C["bg"]), ("active", MAC["control"])],
            foreground=[("selected", MAC["label"]), ("active", MAC["label"])],
        )

        self.nav_notebook = ttk.Notebook(self)
        self.nav_notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Overview
        self.overview_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.overview_frame, text=t("nav_overview"))
        self._build_overview_tab()

        # Tabs 2-6 get their (empty) pages right away so the tab strip is complete, but their
        # contents are built one per event-loop turn after the window is already on screen.
        # Tab 2: Proxies (Paginated)
        self.proxies_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.proxies_frame, text=t("nav_proxies"))

        # Tab 3: Options (NEW)
        self.options_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.options_frame, text=t("nav_options"))

        # Tab 4: Backup & Security
        self.backup_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.backup_frame, text=t("nav_backup"))

        # Tab 5: WSL Isolation Guard
        self.wsl_iso_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.wsl_iso_frame, text=t("nav_wsl_isolation"))

        # Tab 6: Localization
        self.loc_frame = tk.Frame(self.nav_notebook, bg=C["bg"])
        self.nav_notebook.add(self.loc_frame, text=t("nav_localization"))

        self._pending_tab_builders = [
            self._build_proxies_tab,
            self._build_options_tab,
            self._build_backup_tab,
            self._build_wsl_isolation_tab,
            self._build_localization_tab,
        ]
        # Each tab is built the first time it is opened. Building them in the background
        # right after launch landed in the middle of the proxies' start wave and froze the
        # window for up to a second per tab.
        self._tab_builder_for = {
            str(self.proxies_frame): self._build_proxies_tab,
            str(self.options_frame): self._build_options_tab,
            str(self.backup_frame): self._build_backup_tab,
            str(self.wsl_iso_frame): self._build_wsl_isolation_tab,
            str(self.loc_frame): self._build_localization_tab,
        }
        # Opening a tab that is not built yet builds everything that is still pending
        self.nav_notebook.bind("<<NotebookTabChanged>>", self._on_nav_tab_changed, add="+")

        self._update_header_stats()

    def _build_next_deferred_tab(self):
        """Build one pending tab, then yield to the event loop before the next one."""
        pending = getattr(self, "_pending_tab_builders", None)
        if not pending:
            return
        quiet_since = getattr(self, "_start_queue_idle_at", None)
        if getattr(self, "_start_queue_busy", False) or (
                quiet_since is not None and (time.monotonic() - quiet_since) * 1000 < TAB_BUILD_QUIET_MS):
            # proxies are (still) starting: building tabs now only adds GUI-thread stalls
            self.after(500, self._build_next_deferred_tab)
            return
        builder = pending.pop(0)
        try:
            builder()
        except Exception as err:
            self._log(f"⚠️ [GUI] Tab build failed ({builder.__name__}): {err}")
        if pending:
            self.after(15, self._build_next_deferred_tab)

    def _on_nav_tab_changed(self, event=None):
        # The event also fires for the initial Overview selection, which needs nothing extra
        try:
            selected = self.nav_notebook.select()
        except Exception:
            return
        if selected != str(self.overview_frame):
            self._ensure_tab_built(selected)

    def _ensure_tab_built(self, frame) -> None:
        """Build one tab now if it has not been built yet (frame widget or its path)."""
        builder = getattr(self, "_tab_builder_for", {}).get(str(frame))
        pending = getattr(self, "_pending_tab_builders", None)
        if builder is None or not pending or builder not in pending:
            return
        pending.remove(builder)
        try:
            builder()
        except Exception as err:
            self._log(f"⚠️ [GUI] Tab build failed ({builder.__name__}): {err}")

    def _ensure_tabs_built(self):
        """Synchronously build every tab that is still pending."""
        while getattr(self, "_pending_tab_builders", None):
            self._build_next_deferred_tab()

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
        self.overview_btn_box = btn_box

        self.start_all_btn = MacButton(btn_box, text=t("btn_start_all"), variant="success", command=self.start_all)
        self.start_all_btn.pack(side=tk.LEFT, padx=3)
        self.stop_all_btn = MacButton(btn_box, text=t("btn_stop_all"), variant="danger", command=self.stop_all)
        self.stop_all_btn.pack(side=tk.LEFT, padx=3)
        self.refresh_geo_btn = MacButton(btn_box, text=t("btn_refresh_all_geo"), variant="secondary",
                                         command=self.refresh_all_geo)
        self.refresh_geo_btn.pack(side=tk.LEFT, padx=3)

        # Zapret2 Watcher Block (Auto-recovery & Watcher)
        self.zapret_watcher_widget = ZapretRecoveryWidget(self.overview_frame, watcher=self.zapret_watcher)
        self.zapret_watcher_widget.pack(fill=tk.X, padx=px, pady=(0, 8))

        # View switcher (list / by groups) and the groups manager button
        view_bar = tk.Frame(self.overview_frame, bg=bg)
        view_bar.pack(fill=tk.X, padx=px, pady=(0, 2))

        # [ List | By groups ]  |  [Finland ×] [ALL ×]  [+ Group]
        # The view switch is one segmented control; open group tabs live after a divider
        # and look like tabs, not like a third view button.
        self.overview_view_mode = proxy_groups.get_view_mode()
        self.view_segmented = MacSegmented(
            view_bar, [(proxy_groups.VIEW_LIST, t("view_list")), (proxy_groups.VIEW_GROUPS, t("view_groups"))],
            value=self.overview_view_mode if self.overview_view_mode in (proxy_groups.VIEW_LIST, proxy_groups.VIEW_GROUPS) else "",
            command=self.set_overview_view_mode,
        )
        self.view_segmented.pack(side=tk.LEFT)
        self.view_divider = MacDivider(view_bar, height=22)
        self.view_divider.pack(side=tk.LEFT, fill=tk.Y)
        self._group_tabs_frame = tk.Frame(view_bar, bg=bg)   # packed only while it has tabs
        self.add_group_tab_btn = MacButton(
            view_bar, text=t("btn_add_group_tab"), variant="tinted", command=self.open_group_tab_picker,
        )
        self.add_group_tab_btn.pack(side=tk.LEFT)
        self._rebuild_group_tabs()

        # Actions get their own row: open group tabs would push them off the view row
        actions_bar = tk.Frame(self.overview_frame, bg=bg)
        actions_bar.pack(fill=tk.X, padx=px, pady=(4, 2))

        self.groups_btn = MacButton(actions_bar, text=t("btn_groups"), variant="secondary",
                                    command=self.open_groups_manager)
        self.groups_btn.pack(side=tk.RIGHT)

        self._selection_mode = False
        self._selected: set[ProxyInstance] = set()
        self.select_mode_btn = MacButton(actions_bar, text=t("btn_select_mode"), variant="secondary",
                                         command=self.toggle_selection_mode)
        self.select_mode_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self._style_select_mode_button()

        # One Telegram button: copies the link to the shared Telegram port (dispatcher)
        size = max(16, int(round(18 * float(self.winfo_fpixels("1i")) / 96.0)))
        self._tg_icon_main = telegram_photo(self, size)
        self.tg_link_button = MacButton(
            actions_bar, text="" if self._tg_icon_main else "✈", image=self._tg_icon_main,
            variant="secondary", padx=8, command=self._copy_tg_proxy_link,
        )
        self.tg_link_button.pack(side=tk.RIGHT, padx=(0, 6))

        self.add_subscription_btn = MacButton(actions_bar, text=t("btn_add_subscription"), variant="success",
                                              command=self.open_subscription_dialog)
        self.add_subscription_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # Bulk actions, shown only in selection mode
        self.selection_bar = tk.Frame(self.overview_frame, bg=bg)
        for label_key, command in (("btn_select_all", self.select_all_proxies), ("btn_clear_selection", self.clear_proxy_selection)):
            MacButton(self.selection_bar, text=t(label_key), variant="secondary", command=command).pack(
                side=tk.LEFT, padx=(0, 6))
        self.selection_count_lbl = tk.Label(self.selection_bar, text="", font=(ui_font(self).cget("family"), 9, "bold"),
                                            fg=C["subtext"], bg=bg)
        self.selection_count_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self.bulk_group_btn = MacButton(self.selection_bar, text=t("btn_move_to_group"), variant="primary")
        self.bulk_group_btn.config(command=lambda: self._open_bulk_group_menu(self.bulk_group_btn))
        self.bulk_group_btn.pack(side=tk.RIGHT)

        # Scrollable container for proxy rows
        canvas_frame = tk.Frame(self.overview_frame, bg=bg)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=px, pady=(4, 10))
        self._overview_canvas_frame = canvas_frame

        # The proxy list is drawn as canvas items (see overview_view.py): no widget per card
        self.overview_canvas = tk.Canvas(canvas_frame, bg=bg, highlightthickness=0)
        self.overview_scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.overview_canvas.yview)
        self.overview_canvas.configure(yscrollcommand=self.overview_scrollbar.set)
        self.overview_view = OverviewView(self.overview_canvas, C)
        self.overview_view.on_card_context = self._open_card_context_menu

        # Smooth mousewheel handling
        def _on_overview_mousewheel(event):
            try:
                if self.overview_canvas.winfo_exists():
                    self.overview_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        def _bind_overview_wheel(e):
            self.overview_canvas.bind_all("<MouseWheel>", _on_overview_mousewheel)

        def _unbind_overview_wheel(e):
            self.overview_canvas.unbind_all("<MouseWheel>")

        self.overview_canvas.bind("<Enter>", _bind_overview_wheel)
        self.overview_canvas.bind("<Leave>", _unbind_overview_wheel)

        self.overview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.overview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.refresh_overview()

    def update_overview_card(self, inst: ProxyInstance):
        """Mark one proxy's card(s) for a redraw. Requests are collected and applied together
        on the next idle moment: a proxy going through 4-6 states while starting costs one
        redraw, and a status-only change is just new colors (see OverviewView.update_card)."""
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, lambda: self.update_overview_card(inst))
            except Exception:
                pass
            return
        view = getattr(self, "overview_view", None)
        if view is None or inst not in view.card_slots:
            return          # not on screen (another group's tab): nothing to draw
        dirty = self.__dict__.setdefault("_dirty_cards", set())
        dirty.add(inst)
        if not getattr(self, "_card_flush_pending", False):
            self._card_flush_pending = True
            try:
                self.after_idle(self._flush_card_updates)
            except Exception:
                self._card_flush_pending = False

    def _flush_card_updates(self):
        self._card_flush_pending = False
        dirty = getattr(self, "_dirty_cards", set())
        self._dirty_cards = set()
        view = getattr(self, "overview_view", None)
        if view is None or not dirty:
            return
        names = None
        if self.overview_view_mode == proxy_groups.VIEW_LIST:
            names = {g["id"]: g["name"] for g in proxy_groups.load_groups()}
        index = {inst: i for i, inst in enumerate(self.instances)}
        for inst in dirty:
            if inst not in view.card_slots or inst not in index:
                continue
            badge = ", ".join(names[g] for g in inst.get_group_ids(set(names))) or None if names is not None else None
            try:
                view.update_card(self._card_model(inst, index[inst], badge))
            except tk.TclError:
                pass

    def refresh_overview(self):
        """Ask for a redraw of the Overview list. Calls are coalesced into one redraw on the
        next idle moment, so a burst (dozens of proxies changing state) costs one redraw."""
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.refresh_overview)
            except Exception:
                pass
            return
        if getattr(self, "_overview_refresh_pending", False):
            return
        self._overview_refresh_pending = True
        try:
            self.after_idle(self._render_overview)
        except Exception:
            self._overview_refresh_pending = False

    def _render_overview(self, reset_scroll: bool = False):
        """Rebuild the row models for the current view and draw them (tens of ms)."""
        self._overview_refresh_pending = False
        view = getattr(self, "overview_view", None)
        if view is None:
            return
        try:
            if not view.canvas.winfo_exists():
                return
        except Exception:
            return

        if not hasattr(self, "overview_view_mode"):
            self.overview_view_mode = proxy_groups.get_view_mode()
        if not hasattr(self, "_selected"):
            self._selected = set()
        self._selected &= set(self.instances)  # forget removed proxies

        groups = proxy_groups.load_groups()
        known = {g["id"] for g in groups}
        names = {g["id"]: g["name"] for g in groups}
        index = {inst: i for i, inst in enumerate(self.instances)}
        member_ids = {inst: inst.get_group_ids(known) for inst in self.instances}

        tab_gid = proxy_groups.group_id_of_view(self.overview_view_mode)
        tab_group = next((g for g in groups if g["id"] == tab_gid), None)
        if tab_gid and tab_group is None:
            # The tab's group vanished between refreshes: show all groups instead
            self.overview_view_mode = proxy_groups.VIEW_GROUPS

        rows: list = []
        if tab_group is not None:
            members = [inst for inst in self.instances if tab_group["id"] in member_ids[inst]]
            rows.append(self._header_model(tab_group, members, first=True))
            if not members:
                rows.append(HintModel(t("group_empty_hint")))
            rows.extend(self._card_model(inst, index[inst], None) for inst in members)
        elif self.overview_view_mode == proxy_groups.VIEW_GROUPS:
            if not self.instances and not groups:
                rows.append(HintModel(t("empty_overview"), italic=False, big=True))
            sections = proxy_groups.partition(self.instances, groups, lambda i: member_ids[i])
            for sec_idx, (group, members) in enumerate(sections):
                rows.append(self._header_model(group, members, first=(sec_idx == 0)))
                if group is not None and not members:
                    rows.append(HintModel(t("group_empty_hint")))
                rows.extend(self._card_model(inst, index[inst], None) for inst in members)
        else:
            if not self.instances:
                rows.append(HintModel(t("empty_overview"), italic=False, big=True))
            for inst in self.instances:
                badge = ", ".join(names[g] for g in member_ids[inst]) or None
                rows.append(self._card_model(inst, index[inst], badge))

        view.render(rows, reset_scroll=reset_scroll)
        self._sync_selection_bar()

    def _card_model(self, inst: ProxyInstance, idx: int, group_badge: Optional[str]) -> CardModel:
        """Everything one proxy card shows, as data."""
        h, port = inst.get_listen()
        http_port = inst.get_http_port()
        flag = inst.geo_info.get("flag", "🌐")
        country = inst.geo_info.get("country", "Unknown")
        ip = inst.geo_info.get("ip", "")

        if inst.is_transitioning:
            dot = C["yellow"]
        elif inst.running and inst.healthy:
            dot = C["green"]
        elif inst.running:
            dot = C["yellow"]
        else:
            dot = C["red"]

        badges = []
        if inst.cfg.get("killswitch", False):
            badges.append((" 🛡️ KS ", C["green"]))
        if inst.is_system_proxy():
            badges.append((f" {t('badge_system_proxy')} ", C["yellow"]))
        if inst.is_work_proxy():
            badges.append((f" {t('badge_work_proxy')} ", C["teal"]))
        if inst.is_telegram_proxy():
            badges.append((f" {t('badge_telegram_proxy')} ", C["peach"]))
        if group_badge:
            badges.append((f" 📁 {group_badge} ", C["blue"]))

        # fixed width over all its labels: a status change then never moves other buttons
        widths = (t("btn_on"), t("btn_off"), "⏳ ...")
        if inst.is_transitioning:
            toggle = OverviewButton("⏳ ...", None, bg=MAC["orange"], fg="#1d1d1f", enabled=False, bold=True,
                                    name="toggle", width_labels=widths)
        elif inst.running:
            toggle = OverviewButton(t("btn_off"), inst.toggle_async, bg=MAC["red"], fg="#ffffff", bold=True,
                                    name="toggle", width_labels=widths)
        else:
            toggle = OverviewButton(t("btn_on"), inst.toggle_async, bg=MAC["green"], fg="#ffffff", bold=True,
                                    name="toggle", width_labels=widths)
        buttons = [toggle]
        buttons += [
            OverviewButton("📁", lambda i=inst: self._open_group_menu(i, None), name="groups"),
            OverviewButton("🔍", inst.check_geo_now, name="geo"),
            OverviewButton(t("btn_view_tab"), lambda n=idx: self._jump_to_instance(n), name="view"),
        ]
        for b in buttons:
            if b.bg == "#313244":
                b.bg = MAC["control"]
            if b.fg == "#cdd6f4":
                b.fg = MAC["label"]

        selecting = getattr(self, "_selection_mode", False)
        return CardModel(
            key=inst,
            dot=dot,
            order_text=f"#{format_order(inst.get_order())}",
            name=inst.get_display_name(),
            ports=f"SOCKS5: {h}:{port}  |  HTTP: {h}:{http_port}",
            geo=f"{flag} {country}" + (f" ({ip})" if ip else ""),
            geo_fg=C["yellow"] if country != "Unknown" else C["muted"],
            on_order=lambda i=inst: self.prompt_reorder_proxy(i),
            on_rename=lambda i=inst: self.prompt_rename_proxy(i),
            badges=badges,
            buttons=buttons,
            checked=(inst in self._selected) if selecting else None,
            on_check=(lambda value, i=inst: self._on_card_selected(i, value)) if selecting else None,
        )

    # ── Proxy Groups ──────────────────────────────────────────
    def _style_view_mode_buttons(self):
        """Reflect the current view: the segmented control shows List / By groups, or
        nothing when a group tab is the view; the active tab chip is highlighted."""
        seg = getattr(self, "view_segmented", None)
        if seg is not None:
            try:
                in_tab = proxy_groups.group_id_of_view(self.overview_view_mode) is not None
                seg.set_value("" if in_tab else self.overview_view_mode)
            except tk.TclError:
                pass
        for gid, chip in getattr(self, "_group_tab_widgets", {}).items():
            try:
                chip.set_active(self.overview_view_mode == proxy_groups.group_view_mode(gid))
            except tk.TclError:
                pass

    def set_overview_view_mode(self, mode: str):
        if mode == getattr(self, "overview_view_mode", None):
            return
        self.overview_view_mode = mode
        proxy_groups.set_view_mode(mode)
        self._style_view_mode_buttons()
        self._render_overview(reset_scroll=True)

    # ── Overview Group Tabs ───────────────────────────────────
    def _rebuild_group_tabs(self):
        """Recreate the single-group tabs from settings; drop the view of a vanished tab."""
        frame = getattr(self, "_group_tabs_frame", None)
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()
        self._group_tab_widgets: dict[str, MacTabChip] = {}
        names = {g["id"]: g["name"] for g in proxy_groups.load_groups()}
        tabs = proxy_groups.get_group_tabs()
        for gid in tabs:
            chip = MacTabChip(
                frame, text=f"📁 {names[gid]}",
                command=lambda g=gid: self.set_overview_view_mode(proxy_groups.group_view_mode(g)),
                on_close=lambda g=gid: self.close_group_tab(g),
            )
            chip.pack(side=tk.LEFT, padx=(0, 6))
            self._group_tab_widgets[gid] = chip
        # an empty frame keeps its last size in Tk: show the tab strip only when it has tabs
        add_btn = getattr(self, "add_group_tab_btn", None)
        if tabs and not frame.winfo_manager():
            frame.pack(side=tk.LEFT, before=add_btn) if add_btn is not None else frame.pack(side=tk.LEFT)
        elif not tabs and frame.winfo_manager():
            frame.pack_forget()

        current_gid = proxy_groups.group_id_of_view(getattr(self, "overview_view_mode", None))
        if current_gid and current_gid not in tabs:
            self.overview_view_mode = proxy_groups.VIEW_GROUPS
            proxy_groups.set_view_mode(self.overview_view_mode)
            self._style_view_mode_buttons()

    def open_group_tabs(self, group_ids: list[str]):
        """Open tabs for the given groups and switch to the last one."""
        if not group_ids:
            return
        proxy_groups.open_group_tabs(group_ids)
        self._rebuild_group_tabs()
        self.set_overview_view_mode(proxy_groups.group_view_mode(group_ids[-1]))

    def close_group_tab(self, group_id: str):
        """✕ on a tab: close it; if it was the shown view, fall back to "By groups"."""
        was_active = self.overview_view_mode == proxy_groups.group_view_mode(group_id)
        proxy_groups.close_group_tab(group_id)
        if was_active:
            self.overview_view_mode = proxy_groups.VIEW_GROUPS
            proxy_groups.set_view_mode(self.overview_view_mode)
        self._rebuild_group_tabs()
        self.refresh_overview()

    def open_group_tab_picker(self):
        dlg = getattr(self, "_group_tab_picker", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()
            return
        self._group_tab_picker = GroupTabPickerDialog(self)

    def _header_model(self, group: Optional[dict], members: list, first: bool) -> HeaderModel:
        """Section header: a real group gets inline rename; in selection mode a non-empty
        section gets a select/unselect button."""
        select_button = None
        if getattr(self, "_selection_mode", False) and members:
            all_in = all(inst in self._selected for inst in members)
            select_button = OverviewButton(
                t("btn_unselect_group") if all_in else t("btn_select_group"),
                lambda m=list(members): self.toggle_group_selection(m),
                bg=MAC["control"], fg="#64a8ff", bold=True, name="select_group",
            )
        on_rename = None
        if group is not None:
            gid, old_name = group["id"], group["name"]

            def on_rename(text: str, from_blur: bool, gid=gid, old_name=old_name) -> bool:
                if proxy_groups.normalize_name(text) == old_name:
                    return True
                try:
                    proxy_groups.rename_group(gid, text)
                except proxy_groups.GroupNameError as err:
                    if from_blur:
                        return True          # clicking away with an invalid name just cancels
                    self._show_group_name_error(err, self)
                    return False             # keep editing after an explicit Enter
                except KeyError:
                    return True
                self.after_idle(self.on_groups_changed)
                return True

        return HeaderModel(
            key=group["id"] if group is not None else None,
            icon="📁" if group is not None else "📂",
            name=group["name"] if group is not None else t("group_unsorted"),
            count_text=f"({len(members)})",
            first=first,
            on_rename=on_rename,
            select_button=select_button,
        )

    # ── Overview Selection Mode ───────────────────────────────
    def _style_select_mode_button(self):
        btn = getattr(self, "select_mode_btn", None)
        if btn is None:
            return
        btn.config(selected=bool(getattr(self, "_selection_mode", False)))

    def toggle_selection_mode(self):
        """Show/hide checkboxes on proxy cards together with the bulk actions bar."""
        self._selection_mode = not getattr(self, "_selection_mode", False)
        self._selected = set()
        self._style_select_mode_button()
        bar = getattr(self, "selection_bar", None)
        if bar is not None:
            if self._selection_mode:
                bar.pack(fill=tk.X, padx=12, pady=(4, 0), before=self._overview_canvas_frame)
            else:
                bar.pack_forget()
        self._render_overview()

    def _on_card_selected(self, inst: ProxyInstance, selected: bool):
        if selected:
            self._selected.add(inst)
        else:
            self._selected.discard(inst)
        self._sync_selection_ui()

    def select_all_proxies(self):
        self._selected = set(self.instances)
        self._sync_selection_ui()

    def clear_proxy_selection(self):
        self._selected = set()
        self._sync_selection_ui()

    def toggle_group_selection(self, members: list):
        """Select every proxy of a section, or unselect them when all are already selected."""
        alive = [inst for inst in members if inst in self.instances]
        if alive and all(inst in self._selected for inst in alive):
            self._selected.difference_update(alive)
        else:
            self._selected.update(alive)
        self._sync_selection_ui()

    def _sync_selection_ui(self):
        """Selection changed: redraw the list (checkboxes, group buttons) and the bar."""
        if not getattr(self, "_selection_mode", False):
            return
        self._render_overview()

    def _sync_selection_bar(self):
        if not getattr(self, "_selection_mode", False):
            return
        selected = getattr(self, "_selected", set())
        try:
            if hasattr(self, "selection_count_lbl") and self.selection_count_lbl.winfo_exists():
                if not getattr(self, "_flash_active", False):
                    self.selection_count_lbl.config(text=t("lbl_selected", count=len(selected)))
            if hasattr(self, "bulk_group_btn") and self.bulk_group_btn.winfo_exists():
                self.bulk_group_btn.config(state=tk.NORMAL if selected else tk.DISABLED)
        except Exception:
            pass

    def selected_proxies(self) -> list[ProxyInstance]:
        """Selected proxies in display order."""
        return [inst for inst in self.instances if inst in getattr(self, "_selected", set())]

    def add_selected_to_group(self, group_id: str) -> tuple[int, int]:
        """“Add to group”: the selected proxies also join `group_id`, keeping their other groups.

        Only references are added: no proxy is created and no port is taken.
        Returns (added, already there); saves once.
        """
        chosen = self.selected_proxies()
        added = sum(1 for inst in chosen if proxy_groups.add_group_ref(inst.cfg, group_id))
        if added:
            self.save_all()
            self.on_groups_changed()
        group = proxy_groups.get_group(group_id)
        if group is not None and chosen:
            self._flash_selection_info(t("added_to_group_info", name=group["name"], added=added, already=len(chosen) - added))
        return added, len(chosen) - added

    def _flash_selection_info(self, text: str, ms: int = 3000):
        """Show a result in the selection bar for a moment, then the usual counter again."""
        lbl = getattr(self, "selection_count_lbl", None)
        if lbl is None:
            return
        try:
            lbl.config(text=text, fg=C["green"])
        except Exception:
            return
        self._flash_active = True

        def restore():
            self._flash_active = False
            try:
                if lbl.winfo_exists():
                    lbl.config(fg=C["subtext"])
            except Exception:
                return
            self._sync_selection_bar()

        self.after(ms, restore)

    def _open_bulk_group_menu(self, anchor: tk.Widget):
        """“Add to group” for the selection: an existing group or a new one the app proposes."""
        chosen = self.selected_proxies()
        if not chosen:
            return
        menu = tk.Menu(
            self, tearoff=0, bg=C["surface"], fg=C["text"],
            activebackground=C["hover"], activeforeground=C["blue"], font=("Segoe UI", 9),
        )
        known = {g["id"] for g in proxy_groups.load_groups()}
        for g in proxy_groups.load_groups():
            all_in = all(g["id"] in inst.get_group_ids(known) for inst in chosen)
            menu.add_command(
                label=f"📁 {g['name']}" + (f"   {t('group_all_already_in')}" if all_in else ""),
                state=tk.DISABLED if all_in else tk.NORMAL,
                command=lambda gid=g["id"]: self.add_selected_to_group(gid),
            )
        menu.add_separator()
        menu.add_command(label=t("group_menu_new"), command=self.add_selected_to_new_group)
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            menu.grab_release()

    def add_selected_to_new_group(self) -> Optional[dict]:
        chosen = self.selected_proxies()
        if not chosen:
            return None
        group = self.prompt_new_group(
            self, suggested=self.suggest_group_name(),
            prompt=t("new_group_for_selected_prompt", count=len(chosen)),
        )
        if group is not None:
            self.add_selected_to_group(group["id"])
        return group

    def _open_card_context_menu(self, inst: ProxyInstance, x_root: int, y_root: int):
        """Right click on a proxy card: per-proxy switches."""
        if inst not in self.instances:
            return
        menu = tk.Menu(
            self, tearoff=0, bg=C["surface"], fg=C["text"],
            activebackground=C["hover"], activeforeground=C["blue"], font=("Segoe UI", 9),
            selectcolor=C["blue"],
        )
        ks_var = tk.BooleanVar(value=bool(inst.cfg.get("killswitch", False)))
        tg_var = tk.BooleanVar(value=inst.is_telegram_proxy())
        menu._vars = (ks_var, tg_var)  # keep them alive while the menu is open
        menu.add_command(label=inst.get_display_name(), state=tk.DISABLED)
        menu.add_separator()
        menu.add_checkbutton(label=f"🛡️ {t('ctx_killswitch')}", variable=ks_var,
                             command=lambda: inst.set_killswitch(bool(ks_var.get())))
        menu.add_checkbutton(label=f"✈️ {t('ctx_telegram_proxy')}", variable=tg_var,
                             command=lambda: inst.set_telegram_proxy(bool(tg_var.get())))
        self._card_context_menu = menu
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def apply_telegram_port(self, raw) -> bool:
        """Validate and apply the Telegram port right away: save it and move the running
        dispatcher to it in the background (a restart of its listener takes a moment)."""
        status = getattr(self, "tg_port_status", None)

        def show(text: str, color: str):
            try:
                if status is not None and status.winfo_exists():
                    status.config(text=text, fg=C[color])
            except Exception:
                pass

        try:
            port = int(str(raw).strip())
        except (TypeError, ValueError):
            show(t("tg_port_invalid", min=TG_PORT_MIN, max=65535), "red")
            return False
        if not TG_PORT_MIN <= port <= 65535:
            show(t("tg_port_invalid", min=TG_PORT_MIN, max=65535), "red")
            return False

        from vless2socks import telegram_proxy
        disp = telegram_proxy.get_tg_dispatcher()
        current = settings_manager.get_telegram_proxy_port()
        if port == current and disp.listen_port == port:
            show(t("tg_port_applied", port=port), "green")
            return True
        if port != disp.listen_port and not is_port_free(port):
            show(t("tg_port_busy", port=port), "red")
            return False

        settings_manager.set_telegram_proxy_port(port)
        show(t("tg_port_applying", port=port), "yellow")

        def move():
            ok = True
            try:
                disp.set_listen_port(port)       # restarts the listener if it was running
                if disp._running:
                    ok = disp.is_running()
            except Exception:
                ok = False
            self.after(0, lambda: show(t("tg_port_applied" if ok else "tg_port_failed", port=port),
                                       "green" if ok else "red"))
            if hasattr(self, "zapret_watcher") and self.zapret_watcher:
                try:
                    self.zapret_watcher.trigger_immediate_check()
                except Exception:
                    pass

        submit_background(move)
        return True

    def _report_telegram_health(self):
        """Tell the Telegram dispatcher which Telegram proxies passed their tunnel probe,
        so it rotates away from a proxy whose server is down even if its port is open."""
        try:
            from vless2socks import telegram_proxy
            disp = telegram_proxy.get_tg_dispatcher()
        except Exception:
            return
        for inst in self.instances:
            if not inst.is_telegram_proxy():
                continue
            try:
                host, port = inst.get_listen()
                disp.set_health(host or "127.0.0.1", int(port), bool(inst.running and inst.healthy))
            except Exception:
                pass

    def _open_group_menu(self, inst: ProxyInstance, anchor: tk.Widget):
        """Popup on a card's 📁 button: tick the groups this proxy belongs to (several allowed)."""
        groups = proxy_groups.load_groups()
        member_of = set(inst.get_group_ids({g["id"] for g in groups}))
        menu = tk.Menu(
            self, tearoff=0, bg=C["surface"], fg=C["text"],
            activebackground=C["hover"], activeforeground=C["blue"], font=("Segoe UI", 9),
            selectcolor=C["blue"],
        )
        menu._vars = []  # keep the BooleanVars alive while the menu is shown
        for g in groups:
            var = tk.BooleanVar(value=g["id"] in member_of)
            menu._vars.append(var)
            menu.add_checkbutton(
                label=f"📁 {g['name']}", variable=var,
                command=lambda gid=g["id"], v=var: inst.set_group_membership(gid, bool(v.get())),
            )
        if groups:
            menu.add_separator()
        menu.add_command(label=f"📂 {t('group_menu_clear')}", command=inst.clear_groups)

        def create_and_add():
            group = self.prompt_new_group(self, suggested=self.suggest_group_name())
            if group is not None:
                self.on_groups_changed()
                inst.set_group_membership(group["id"], True)

        menu.add_command(label=t("group_menu_new"), command=create_and_add)
        try:
            if anchor is not None:
                menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
            else:
                menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _show_group_name_error(self, err: "proxy_groups.GroupNameError", parent: tk.Misc):
        key = "err_group_name_taken" if err.code == "taken" else "err_group_name_empty"
        messagebox.showerror(t("app_title"), t(key, name=err.name), parent=parent)

    def suggest_group_name(self) -> str:
        """First free "Group N" name, offered when the app proposes a new group."""
        taken = {g["name"].casefold() for g in proxy_groups.load_groups()}
        n = 1
        while t("group_suggest_name", n=n).casefold() in taken:
            n += 1
        return t("group_suggest_name", n=n)

    def prompt_new_group(self, parent: tk.Misc, suggested: str = "", prompt: str = "") -> Optional[dict]:
        """Ask for a name (pre-filled with `suggested`) and create the group; None if cancelled."""
        initial = suggested
        while True:
            res = simpledialog.askstring(
                t("new_group_title"), prompt or t("new_group_prompt"), initialvalue=initial, parent=parent,
            )
            if res is None:
                return None
            try:
                return proxy_groups.create_group(res)
            except proxy_groups.GroupNameError as err:
                self._show_group_name_error(err, parent)
                initial = res

    def prompt_rename_group(self, group: dict, parent: tk.Misc) -> bool:
        initial = group["name"]
        while True:
            res = simpledialog.askstring(
                t("rename_group_title"), t("rename_group_prompt", name=group["name"]),
                initialvalue=initial, parent=parent,
            )
            if res is None:
                return False
            try:
                proxy_groups.rename_group(group["id"], res)
                return True
            except proxy_groups.GroupNameError as err:
                self._show_group_name_error(err, parent)
                initial = res
            except KeyError:
                return False

    def on_groups_changed(self):
        """Groups were created, renamed or deleted: refresh everything that shows them."""
        self._rebuild_group_tabs()  # renamed tab titles, tabs of deleted groups
        self.refresh_overview()
        for inst in self.instances:
            inst._sync_group_selector()

    def open_groups_manager(self):
        dlg = getattr(self, "_groups_dialog", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()
            return
        self._groups_dialog = GroupsManagerDialog(self)

    def _ask_choice(self, parent: tk.Misc, title: str, message: str, options: list[tuple[str, str, str]]) -> Optional[str]:
        """Modal dialog with one button per option (key, label, color key); None if closed."""
        result: dict[str, Optional[str]] = {"key": None}
        win = tk.Toplevel(parent)
        win.title(title)
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(parent)

        tk.Label(
            win, text=message, font=("Segoe UI", 10), fg=C["text"], bg=C["bg"],
            wraplength=420, justify=tk.LEFT, anchor="w",
        ).pack(fill=tk.X, padx=18, pady=(16, 12))

        for key, label, color in options:
            def choose(k=key):
                result["key"] = k
                win.destroy()
            tk.Button(
                win, text=label, font=("Segoe UI", 9, "bold"),
                bg=C[color], fg="#1e1e2e" if color != "overlay" else C["text"],
                activebackground=C["hover"], relief=tk.FLAT, padx=12, pady=5, cursor="hand2",
                anchor="w", command=choose,
            ).pack(fill=tk.X, padx=18, pady=3)
        tk.Frame(win, bg=C["bg"], height=12).pack()

        win.bind("<Escape>", lambda e: win.destroy())
        _center_over(win, parent)
        win.grab_set()
        win.focus_force()
        parent.wait_window(win)
        return result["key"]

    def delete_group_flow(self, group: dict, parent: tk.Misc) -> bool:
        """Confirm and delete a group, either alone (proxies → Unsorted) or with its proxies."""
        if not messagebox.askyesno(t("del_group_title"), t("del_group_confirm", name=group["name"]), parent=parent):
            return False

        known = {g["id"] for g in proxy_groups.load_groups()}
        members = [inst for inst in self.instances if group["id"] in inst.get_group_ids(known)]
        # Proxies are shared by reference: one that is also in another group is not deleted
        # with this group, it only leaves it
        exclusive = [inst for inst in members if inst.get_group_ids(known) == [group["id"]]]
        shared = [inst for inst in members if inst not in exclusive]
        choice = self._ask_choice(
            parent, t("del_group_title"), t("del_group_choice", name=group["name"], count=len(members)),
            [
                ("with_proxies", t("btn_del_group_with_proxies"), "red"),
                ("only_group", t("btn_del_group_only"), "blue"),
                ("cancel", t("btn_cancel"), "overlay"),
            ],
        )
        if choice == "only_group":
            for inst in members:
                proxy_groups.remove_group_ref(inst.cfg, group["id"])
            proxy_groups.delete_group(group["id"])
            self.save_all()
            self.on_groups_changed()
            return True

        if choice == "with_proxies":
            if exclusive:
                names = "\n".join(f"• {inst.get_display_name()} (:{inst.get_listen()[1]})" for inst in exclusive[:15])
                if len(exclusive) > 15:
                    names += f"\n… +{len(exclusive) - 15}"
                text = t("del_group_proxies_confirm", count=len(exclusive), names=names)
                if shared:
                    text += "\n\n" + t("del_group_shared_kept", count=len(shared))
                if not messagebox.askyesno(t("del_group_title"), text, icon=messagebox.WARNING, parent=parent):
                    return False
            for inst in shared:
                proxy_groups.remove_group_ref(inst.cfg, group["id"])
            proxy_groups.delete_group(group["id"])
            self._remove_instances(exclusive)  # saves, also the trimmed memberships of shared proxies
            self.on_groups_changed()
            return True
        return False

    def _remove_instances(self, members: list[ProxyInstance]):
        """Remove proxies from the app now; their processes are stopped in the background."""
        for inst in members:
            if inst in self.instances:
                self.instances.remove(inst)
        self.save_all()
        self.refresh_current_page_tabs()
        self.refresh_overview()
        self._update_header_stats()
        self.update_tray_icon()

        def stop_all_removed():
            for inst in members:
                try:
                    inst.destroy()
                except Exception as err:
                    self._log(f"⚠️ [Groups] Failed to stop removed proxy {inst.get_display_name()}: {err}")
            self.after(0, self.update_tray_icon)
            self.after(0, self._update_telegram_dispatcher_pool)

        threading.Thread(target=stop_all_removed, daemon=True, name="remove-group-proxies").start()

    def _jump_to_instance(self, global_idx: int):
        target_page = global_idx // PAGE_SIZE
        target_tab_idx = global_idx % PAGE_SIZE
        self.current_page = target_page
        self.nav_notebook.select(self.proxies_frame)
        self._ensure_tab_built(self.proxies_frame)
        self.refresh_current_page_tabs()
        if target_tab_idx < len(self.sub_notebook.tabs()):
            self.sub_notebook.select(target_tab_idx)

    def _copy_tg_proxy_link(self, inst=None):
        from vless2socks import telegram_proxy
        url = telegram_proxy.build_tg_socks_url()
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.update()
        except Exception:
            pass
        msg = t("msg_tg_link_copied", url=url)
        try:
            if hasattr(self, "status_bar") and self.status_bar:
                self.status_bar.set_status(f"📋 {url}", temp_sec=5)
            messagebox.showinfo("Telegram Proxy", msg, parent=self)
        except Exception:
            pass

    def _update_telegram_dispatcher_pool(self):
        try:
            from vless2socks import telegram_proxy
            disp = telegram_proxy.get_tg_dispatcher()
            disp.set_pool(self.instances)
            tg_insts = [i for i in getattr(self, "instances", []) if i.is_telegram_proxy()]
            if tg_insts and not disp.is_running():
                # start() waits for the listener to come up; keep that wait off the GUI thread
                threading.Thread(target=disp.start, daemon=True).start()
            elif not tg_insts and disp.is_running():
                disp.stop()
        except Exception as err:
            self._log(f"⚠️ [TelegramProxy] dispatcher pool update failed: {err}")

    def _get_active_telegram_proxy_socket(self) -> str:
        """Return current active upstream socket for TelegramProxy, or 'не выбрано' / 'undefined'."""
        try:
            from vless2socks import telegram_proxy
            disp = telegram_proxy.get_tg_dispatcher()
            act = disp.get_active_proxy()
            if act and act.get("host") and act.get("port"):
                return f"{act['host']}:{act['port']}"
        except Exception:
            pass
        tg_insts = [i for i in getattr(self, "instances", []) if i.is_telegram_proxy()]
        if tg_insts:
            h, p = tg_insts[0].get_listen()
            return f"{h}:{p}"
        lang = "ru"
        try:
            from i18n import get_current_language
            lang = get_current_language()
        except Exception:
            pass
        return "undefined" if lang.lower().startswith("en") else "не выбрано"

    def get_telegram_proxies(self) -> list:
        """Return list of active ProxyInstance configurations flagged as TelegramProxy."""
        proxies = []
        for inst in getattr(self, "instances", []):
            try:
                if inst.is_telegram_proxy():
                    h, p = inst.get_listen()
                    proxies.append({
                        "id": getattr(inst, "global_id", None),
                        "name": inst.cfg.get("name", ""),
                        "host": h or "127.0.0.1",
                        "port": int(p) if str(p).isdigit() else 1081,
                        "running": inst.running or (inst.process and inst.process.poll() is None),
                        "instance": inst,
                    })
            except Exception:
                pass
        return proxies

    def restart_telegram_proxies(self, targets: list = None) -> bool:
        """Restart proxies flagged as TelegramProxy if inactive or targeted."""
        all_ok = True
        try:
            tg_insts = []
            if targets:
                target_ports = set()
                for t in targets:
                    if isinstance(t, dict):
                        target_ports.add(t.get("port"))
                    elif hasattr(t, "get_listen"):
                        _, p = t.get_listen()
                        if str(p).isdigit():
                            target_ports.add(int(p))
                for inst in getattr(self, "instances", []):
                    _, p = inst.get_listen()
                    if int(p) in target_ports if str(p).isdigit() else False:
                        tg_insts.append(inst)
            else:
                tg_insts = [i for i in getattr(self, "instances", []) if i.is_telegram_proxy()]

            for inst in tg_insts:
                try:
                    # Живые апстримы не трогаем: если упал только диспетчер, хватит его перезапуска ниже
                    if inst.check_health_sync():
                        continue
                    self._log(f"🔄 [ZapretWatcher] Restarting telegram proxy instance {inst.cfg.get('name', '')}...")
                    inst.restart(blocking=False)
                except Exception as e:
                    self._log(f"⚠️ [ZapretWatcher] Error restarting telegram proxy: {e}")
                    all_ok = False

            # Гарантируем запуск и актуализацию пула диспетчера TelegramProxy
            try:
                from vless2socks import telegram_proxy
                disp = telegram_proxy.get_tg_dispatcher()
                disp.set_pool(self.instances)
                disp.rotate_to_next()
                if not disp.is_running():
                    disp.start()
            except Exception as e:
                self._log(f"⚠️ [ZapretWatcher] TelegramProxy dispatcher restart failed: {e}")
                all_ok = False
        except Exception as e:
            self._log(f"❌ [ZapretWatcher] restart_telegram_proxies failed: {e}")
            all_ok = False
        return all_ok


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

    def _save_start_port(self) -> bool:
        """Options: persist the first port new proxies may take (part of settings.json, so of backups)."""
        try:
            port = subscriptions.parse_start_port(self.start_port_var.get())
        except ValueError:
            self.start_port_status.config(
                text=t("err_start_port", min=subscriptions.MIN_START_PORT, max=subscriptions.MAX_START_PORT), fg=C["red"],
            )
            return False
        subscriptions.set_start_port(port)
        self.start_port_var.set(str(port))
        self.start_port_status.config(text=t("start_port_saved", port=port), fg=C["green"])
        return True

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
        if not hasattr(self, "sub_notebook"):
            # Not built yet: it renders its tabs itself when first opened
            return
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
        self.opt_canvas_window = opt_canvas.create_window((0, 0), window=opt_content, anchor="nw")
        def _on_opt_canvas_configure(e):
            opt_canvas.itemconfig(self.opt_canvas_window, width=e.width)
        opt_canvas.bind("<Configure>", _on_opt_canvas_configure)
        opt_canvas.configure(yscrollcommand=opt_scrollbar.set)

        def _on_opt_mousewheel(event):
            try:
                delta = int(-1 * (event.delta / 120)) if event.delta else 0
                if delta != 0:
                    opt_canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        def _bind_opt_wheel(e):
            opt_canvas.bind_all("<MouseWheel>", _on_opt_mousewheel)

        def _unbind_opt_wheel(e):
            opt_canvas.unbind_all("<MouseWheel>")

        opt_canvas.bind("<Enter>", _bind_opt_wheel)
        opt_canvas.bind("<Leave>", _unbind_opt_wheel)
        opt_content.bind("<Enter>", _bind_opt_wheel)
        opt_content.bind("<Leave>", _unbind_opt_wheel)

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

        # 0.5. Start port for new proxies (manual "Add" and subscriptions)
        card_port = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card_port.pack(fill=tk.X, pady=6, padx=2)

        port_row = tk.Frame(card_port, bg=C["card"])
        port_row.pack(fill=tk.X, padx=12, pady=(10, 2))
        tk.Label(
            port_row, text=f"  {t('opt_start_port_title')}", font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"],
        ).pack(side=tk.LEFT)
        self.start_port_var = tk.StringVar(value=str(subscriptions.get_start_port()))
        start_port_entry = tk.Entry(
            port_row, textvariable=self.start_port_var, font=("Consolas", 10), width=7, justify=tk.CENTER,
            bg=C["overlay"], fg=C["text"], insertbackground=C["text"], relief=tk.FLAT,
        )
        start_port_entry.pack(side=tk.LEFT, padx=(8, 6), ipady=2)
        start_port_entry.bind("<Return>", lambda e: self._save_start_port())
        tk.Button(
            port_row, text=t("btn_save_start_port"), font=("Segoe UI", 9, "bold"),
            bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=2, cursor="hand2", command=self._save_start_port,
        ).pack(side=tk.LEFT)
        self.start_port_status = tk.Label(port_row, text="", font=("Segoe UI", 8, "bold"), fg=C["green"], bg=C["card"])
        self.start_port_status.pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            card_port, text=t("opt_start_port_desc", min=subscriptions.MIN_START_PORT, max=subscriptions.MAX_START_PORT),
            font=("Segoe UI", 8), fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
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

        # 5. TelegramProxy Virtual Socket Port
        card5 = tk.Frame(opt_content, bg=C["card"], relief=tk.FLAT, borderwidth=1)
        card5.pack(fill=tk.X, pady=6, padx=2)

        tk.Label(
            card5, text=f"✈️  {t('opt_tg_proxy_port_title')}",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"],
        ).pack(anchor="w", padx=12, pady=(10, 2))

        tk.Label(
            card5, text=t("opt_tg_proxy_port_desc"), font=("Segoe UI", 8),
            fg=C["subtext"], bg=C["card"], wraplength=700, justify=tk.LEFT,
        ).pack(anchor="w", padx=36, pady=(0, 6))

        tg_port_row = tk.Frame(card5, bg=C["card"])
        tg_port_row.pack(fill=tk.X, padx=36, pady=(2, 10))

        tk.Label(
            tg_port_row, text=t("opt_tg_proxy_port_lbl"), font=("Segoe UI", 9, "bold"),
            fg=C["text"], bg=C["card"],
        ).pack(side=tk.LEFT, padx=(0, 8))

        curr_tg_port = settings_manager.get_telegram_proxy_port()
        self.tg_port_entry = tk.Entry(
            tg_port_row, font=("Consolas", 10), width=12,
            bg=C["overlay"], fg=C["text"], insertbackground=C["text"],
            relief=tk.FLAT, borderwidth=4,
        )
        self.tg_port_entry.pack(side=tk.LEFT, padx=(0, 8))
        self.tg_port_entry.insert(0, str(curr_tg_port))
        attach_clipboard_and_context_menu(self.tg_port_entry)

        def _apply_typed_port(event=None):
            self._tg_port_job = None
            self.apply_telegram_port(self.tg_port_entry.get())

        def _on_tg_port_typing(event=None):
            # applied when typing pauses, not on every key: "9910" must not go through 9, 99, 991
            if event is not None and event.keysym in ("Return", "KP_Enter", "Tab"):
                return
            job = getattr(self, "_tg_port_job", None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
            self._tg_port_job = self.after(TG_PORT_APPLY_DELAY_MS, _apply_typed_port)

        self.tg_port_entry.bind("<KeyRelease>", _on_tg_port_typing)
        self.tg_port_entry.bind("<Return>", _apply_typed_port)
        self.tg_port_entry.bind("<KP_Enter>", _apply_typed_port)
        self.tg_port_entry.bind("<FocusOut>", _apply_typed_port)

        def _reset_tg_port():
            self.tg_port_entry.delete(0, tk.END)
            self.tg_port_entry.insert(0, str(settings_manager.DEFAULT_TELEGRAM_PROXY_PORT))
            self.apply_telegram_port(settings_manager.DEFAULT_TELEGRAM_PROXY_PORT)

        reset_tg_btn = tk.Button(
            tg_port_row, text=t("btn_reset_tg_port"), font=("Segoe UI", 8, "bold"),
            bg=C["overlay"], fg=C["peach"], activebackground=C["hover"], relief=tk.FLAT,
            padx=8, pady=2, command=_reset_tg_port,
        )
        reset_tg_btn.pack(side=tk.LEFT)

        self.tg_port_status = tk.Label(tg_port_row, text="", font=("Segoe UI", 8, "bold"), fg=C["green"], bg=C["card"])
        self.tg_port_status.pack(side=tk.LEFT, padx=(10, 0))

        tg_link_row = tk.Frame(card5, bg=C["card"])
        tg_link_row.pack(fill=tk.X, padx=36, pady=(0, 10))
        size = max(16, int(round(16 * float(self.winfo_fpixels("1i")) / 96.0)))
        self._tg_icon_options = telegram_photo(self, size)
        self.tg_link_options_button = tk.Button(
            tg_link_row, image=self._tg_icon_options, compound=tk.LEFT,
            text=f"  {t('btn_copy_tg_link_options')}" if self._tg_icon_options else f"✈  {t('btn_copy_tg_link_options')}",
            font=("Segoe UI", 9, "bold"), bg=C["blue"], fg="#1e1e2e", activebackground=C["teal"],
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2", command=self._copy_tg_proxy_link,
        )
        self.tg_link_options_button.pack(side=tk.LEFT)

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
        try:
            if self.winfo_viewable():
                dlg.grab_set()
        except Exception:
            pass

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

            orig_text = t("btn_copy_prompt")
            def _make_copy_cmd(txt, btn_ref):
                def _copy():
                    try:
                        self.clipboard_clear()
                        self.clipboard_append(txt)
                        try:
                            if btn_ref.winfo_exists():
                                btn_ref.config(text="✓ " + t("btn_copy_prompt"), bg=C["green"], fg="#1e1e2e")
                        except Exception:
                            pass
                        def _revert(b=btn_ref, ot=orig_text):
                            try:
                                if b.winfo_exists():
                                    b.config(text=ot, bg=C["overlay"], fg=C["text"])
                            except Exception:
                                pass
                        self.after(1800, _revert)
                    except Exception as ex:
                        pass
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
            if hasattr(self, "start_port_var"):
                self.start_port_var.set(str(subscriptions.get_start_port()))
            # The restored Telegram port is what the user had: never "migrate" it later,
            # and move the running dispatcher to it now
            settings_manager.set_setting("telegram_proxy_port_migrated", True)
            restored_tg_port = settings_manager.get_telegram_proxy_port()
            if hasattr(self, "tg_port_entry"):
                try:
                    self.tg_port_entry.delete(0, tk.END)
                    self.tg_port_entry.insert(0, str(restored_tg_port))
                except Exception:
                    pass
            self.apply_telegram_port(restored_tg_port)
            # ...and its language
            restored_lang = settings_manager.get_setting("language", get_current_language())
            if restored_lang != get_current_language():
                self.after_idle(lambda lang=restored_lang: self._apply_language(lang))
            self.refresh_current_page_tabs()
            self.on_groups_changed()  # restored settings.json brings its own groups
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

        # Picking a language applies it at once (after this click is handled: the
        # rebuild replaces these very radio buttons)
        on_pick = lambda: self.after_idle(self._apply_language)
        r_en = tk.Radiobutton(
            opt_frame, text=t("lang_en"), variable=self.lang_var, value="en",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"], padx=14, pady=10,
            command=on_pick,
        )
        r_en.pack(anchor="w")

        r_ru = tk.Radiobutton(
            opt_frame, text=t("lang_ru"), variable=self.lang_var, value="ru",
            font=("Segoe UI", 10, "bold"), fg=C["text"], bg=C["card"], selectcolor=C["overlay"],
            activebackground=C["card"], activeforeground=C["blue"], padx=14, pady=10,
            command=on_pick,
        )
        r_ru.pack(anchor="w")

    def _apply_language(self, lang: Optional[str] = None):
        """Switch the interface language now. Tabs that were opened are rebuilt in the new
        language; tabs never opened stay unbuilt and come up in it when first opened."""
        selected = lang or (self.lang_var.get() if hasattr(self, "lang_var") else get_current_language())
        if selected == get_current_language():
            return
        save_language_preference(selected)

        self.title(t("app_title"))
        frames = [
            ("nav_overview", self.overview_frame, self._build_overview_tab),
            ("nav_proxies", self.proxies_frame, self._build_proxies_tab),
            ("nav_options", self.options_frame, self._build_options_tab),
            ("nav_backup", self.backup_frame, self._build_backup_tab),
            ("nav_wsl_isolation", self.wsl_iso_frame, self._build_wsl_isolation_tab),
            ("nav_localization", self.loc_frame, self._build_localization_tab),
        ]
        pending = getattr(self, "_pending_tab_builders", [])
        for key, frame, builder in frames:
            self.nav_notebook.tab(frame, text=t(key))
            if builder in pending:
                continue                      # not built yet: it will be, in the new language
            for widget in frame.winfo_children():
                widget.destroy()
            builder()

        self._update_header_stats()
        if hasattr(self, "btn_restart_services") and self.btn_restart_services and self.btn_restart_services.winfo_exists():
            self.btn_restart_services.config(text=f"🔄 {t('btn_restart_services')}")
        if HAS_TRAY and hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.menu = self._build_tray_menu()
        self.update_tray_icon()

    # ── Global Actions & Startup Routines ─────────────────────
    def autostart_configured_proxies(self):
        """Auto-start all proxies with valid URLs if autostart option is enabled."""
        to_start = []
        for inst in self.instances:
            url = inst.cfg.get("url", "").strip()
            if (url.startswith("vless://") or url.startswith("socks5://") or url.startswith("socks://") or url.startswith("wireguard://") or url.startswith("wg://")) and not inst.running:
                to_start.append(inst)
        # Non-blocking and throttled: a few at a time instead of every proxy in the same instant
        started_count = self.start_proxies_throttled(to_start)
        if started_count > 0:
            self.refresh_overview()
            self.update_tray_icon()
            self._update_header_stats()
        self._update_telegram_dispatcher_pool()

    def add_new_instance(self):
        """Add a new proxy instance."""
        used_ports = set()
        for inst in self.instances:
            _, p_str = inst.get_listen()
            try:
                used_ports.add(int(p_str))
            except ValueError:
                pass
        try:
            free_port = subscriptions.allocate_ports(1, subscriptions.get_start_port(), used_ports, is_port_free)[0]
        except subscriptions.SubscriptionError:
            free_port = find_free_port(subscriptions.get_start_port(), exclude=used_ports)

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

        new_id = self._next_global_id()
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
        self._ensure_tab_built(self.proxies_frame)
        self.refresh_current_page_tabs()
        if tab_idx_on_page < len(self.sub_notebook.tabs()):
            self.sub_notebook.select(tab_idx_on_page)

    # ── Subscriptions ─────────────────────────────────────────
    def open_subscription_dialog(self):
        dlg = getattr(self, "_subscription_dialog", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()
            return
        self._subscription_dialog = SubscriptionDialog(self)

    def _subscription_fallback_proxies(self) -> list[str]:
        """HTTP inbounds of running proxies (System Proxy first): routes to fetch a
        subscription through when the direct connection is blocked."""
        running = [inst for inst in self.instances if inst.running]
        running.sort(key=lambda inst: not inst.is_system_proxy())
        return [f"http://127.0.0.1:{inst.get_http_port()}" for inst in running]

    def add_subscription_proxies(
        self,
        url: str,
        links: list[str],
        group_id: Optional[str],
        new_group_name: Optional[str] = None,
        start_after: bool = False,
    ) -> dict:
        """Create ready-to-start vless proxies for `links` on free ports from the start port up.

        Ports are allocated before anything is created, so a shortage changes nothing.
        """
        used_ports: set[int] = set()
        for inst in self.instances:
            try:
                used_ports.add(int(inst.get_listen()[1]))
            except ValueError:
                pass
        ports = subscriptions.allocate_ports(len(links), subscriptions.get_start_port(), used_ports, is_port_free)

        if new_group_name:
            group = proxy_groups.create_group(new_group_name)
        else:
            group = proxy_groups.get_group(group_id)
        gid = group["id"] if group else None
        record = subscriptions.record_subscription(url, subscriptions.TYPE_VLESS, gid, len(links))

        base = load_base_config()
        max_order = max((inst.get_order() for inst in self.instances), default=0.0)
        first_order = float(int(max_order)) + 1.0
        created: list[ProxyInstance] = []
        for offset, (link, port) in enumerate(zip(links, ports)):
            cfg = subscriptions.build_proxy_config(base, link, port, first_order + offset, gid, record["id"])
            inst = ProxyInstance(self, cfg, self._next_global_id())
            self.instances.append(inst)
            created.append(inst)

        self.sort_instances()
        self.save_all()
        self.refresh_current_page_tabs()
        self.on_groups_changed()
        self._update_header_stats()
        self._log(f"🔗 [Subscription] Added {len(created)} vless proxies on ports {ports[0]}-{ports[-1]}")

        if start_after:
            for inst in created:
                inst.start_async()
        return {
            "instances": created,
            "ports": ports,
            "group_id": gid,
            "group_name": group["name"] if group else t("group_unsorted"),
            "subscription": record,
        }

    def _next_global_id(self) -> int:
        """Id for a new proxy. Ids name the .instance_<id>.json config files, so after a
        deletion len(instances) may belong to a live proxy; max + 1 never does."""
        return max((inst.global_id for inst in self.instances), default=-1) + 1

    def remove_instance(self, inst: ProxyInstance):
        if inst in self.instances:
            self.instances.remove(inst)
        self.save_all()
        self.refresh_current_page_tabs()
        self.refresh_overview()
        self._update_header_stats()
        self.update_tray_icon()

    def start_proxies_throttled(self, instances: list) -> int:
        """Start proxies at most START_CONCURRENCY at a time, the next one as soon as a
        start finishes. Launching dozens together started every launcher, xray and
        helper tool in the same instant. Returns how many were queued."""
        pending = deque(inst for inst in instances if not inst.running and not inst.is_transitioning)
        queued = len(pending)
        lock = threading.Lock()
        state = {"active": 0}
        if queued:
            self._start_queue_busy = True

        def launch_next():
            while True:
                with lock:
                    if not pending and state["active"] == 0 and getattr(self, "_start_queue_busy", False):
                        self._start_queue_busy = False
                        self._start_queue_idle_at = time.monotonic()
                    if state["active"] >= START_CONCURRENCY or not pending:
                        return
                    inst = pending.popleft()
                    if inst not in self.instances or inst.running or inst.is_transitioning:
                        continue
                    state["active"] += 1
                done = {"v": False}

                def finish(*_args, d=done):
                    with lock:
                        if d["v"]:
                            return
                        d["v"] = True
                        state["active"] -= 1
                    # "Started" means the launcher process exists; give its own start-up a moment
                    self.after(START_SPACING_MS, launch_next)

                try:
                    inst.start_async(callback=finish)
                except Exception as err:
                    self._log(f"⚠️ [Start] {inst.get_display_name()}: {err}")
                if not inst.is_transitioning:
                    finish()                    # refused (no URL...) or already done
                else:
                    self.after(20000, finish)   # a start that never reports back must not hold a slot

        launch_next()
        return queued

    def start_all(self):
        """Asynchronously start all proxies that are not running without blocking GUI."""
        self.start_proxies_throttled(list(self.instances))
        self.update_tray_icon()
        self._update_header_stats()

    def stop_all(self):
        """Asynchronously stop all proxies that are running without blocking GUI."""
        for inst in self.instances:
            if (inst.running or inst.process) and not inst.is_transitioning:
                inst.stop_async()
        self.update_tray_icon()
        self._update_header_stats()

    def refresh_all_geo(self):
        for inst in self.instances:
            inst.check_geo_now()

    def request_save(self, delay_ms: int = 1000):
        """Save instances.json soon, once for a whole burst (e.g. dozens of proxies starting)."""
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self.request_save(delay_ms))
            return
        if getattr(self, "_save_pending", False):
            return
        self._save_pending = True

        def do_save():
            self._save_pending = False
            self.save_all()
        try:
            self.after(delay_ms, do_save)
        except Exception:
            self._save_pending = False
            self.save_all()

    def save_all(self):
        data = [inst.get_config() for inst in self.instances]
        save_instances(data)

    def _update_header_stats(self):
        """Refresh the "Accounts | Active" counter once per burst of state changes."""
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self._update_header_stats)
            except Exception:
                pass
            return
        if getattr(self, "_header_stats_pending", False):
            return
        self._header_stats_pending = True
        try:
            self.after_idle(self._update_header_stats_now)
        except Exception:
            self._update_header_stats_now()

    def _update_header_stats_now(self):
        self._header_stats_pending = False
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

        # Checkboxes: WSL, WorkProxy, SystemProxy, TelegramProxy
        tg_socket = self._get_active_telegram_proxy_socket()
        chk_opts = [
            (f"🐧  {t('lbl_restart_wsl')}", self.restart_wsl_var, C["yellow"], "wsl"),
            (f"💼  {t('lbl_restart_work_proxy')}", self.restart_work_proxy_var, C["teal"], "work"),
            (f"⚙️  {t('lbl_restart_system_proxy')}", self.restart_system_proxy_var, C["blue"], "system"),
            (f"✈️  TelegramProxy ({tg_socket})", self.restart_telegram_proxy_var, C["peach"], "telegram"),
        ]

        def _restart_single(service):
            self.close_restart_services_menu()
            self.restart_single_service(service)

        for label_text, var, accent_color, service in chk_opts:
            row = tk.Frame(inner, bg=C["card"], cursor="hand2")
            row.pack(fill=tk.X, pady=3)

            lbl = tk.Label(
                row, text=label_text, font=("Segoe UI", 9, "bold"),
                fg=accent_color, bg=C["card"], cursor="hand2",
            )
            lbl.pack(side=tk.LEFT)

            # Link-like hover: underline + red while the pointer is over the label
            lbl.bind("<Enter>", lambda e, l=lbl: l.config(font=("Segoe UI", 9, "bold underline"), fg=C["red"]))
            lbl.bind("<Leave>", lambda e, l=lbl, c=accent_color: l.config(font=("Segoe UI", 9, "bold"), fg=c))

            cb = OrangeCheckbox(
                row, variable=var,
                bg=C["card"],
                orange_color=C["orange"],
                size=18,
                command=self._on_restart_flags_changed,
            )
            cb.pack(side=tk.RIGHT)

            # Clicking the label restarts only this service; the checkbox (and empty row area) toggles the flag
            lbl.bind("<Button-1>", lambda e, s=service: _restart_single(s))
            row.bind("<Button-1>", lambda e, c=cb: c._toggle())

        # Footer Hint
        hint_lbl = tk.Label(
            inner, text=t("restart_menu_hint"),
            font=("Segoe UI", 7), fg=C["subtext"], bg=C["card"],
            wraplength=238, justify=tk.LEFT,
        )
        hint_lbl.pack(anchor="w", pady=(8, 0))

        # Geometry & Coordinates positioning
        self.update_idletasks()
        try:
            arrow_rx = self.btn_restart_arrow.winfo_rootx()
            arrow_rw = self.btn_restart_arrow.winfo_width()
            main_ry = self.btn_restart_services.winfo_rooty()
            main_h = self.btn_restart_services.winfo_height()

            target_w = 265
            target_h = 212
            popup_x = (arrow_rx + arrow_rw) - target_w
            popup_y = main_ry + main_h + 3
        except Exception:
            popup_x = self.winfo_rootx() + 300
            popup_y = self.winfo_rooty() + 50
            target_w = 265
            target_h = 212

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
        tg_p = bool(self.restart_telegram_proxy_var.get())
        settings_manager.set_restart_services_flags(wsl, work, sys_p, tg_p)

    def execute_restart_services(self, only: Optional[str] = None):
        """Restart all services whose checkboxes are currently checked (WSL, WorkProxy, SystemProxy, TelegramProxy).

        With ``only`` ("wsl" / "work" / "system" / "telegram") restart just that service, ignoring the checkboxes.
        """
        if only:
            wsl = only == "wsl"
            work = only == "work"
            sys_p = only == "system"
            tg_p = only == "telegram"
        else:
            wsl = bool(self.restart_wsl_var.get())
            work = bool(self.restart_work_proxy_var.get())
            sys_p = bool(self.restart_system_proxy_var.get())
            tg_p = bool(self.restart_telegram_proxy_var.get())

        if not (wsl or work or sys_p or tg_p):
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

        # Everything that reads widget state happens here, on the GUI thread; the worker only waits and logs.
        # Collect every proxy instance to restart (an instance with several flags is restarted once).
        groups = []
        if work:
            groups.append(("WorkProxy", lambda i: i.is_work_proxy()))
        if sys_p:
            groups.append(("SystemProxy", lambda i: i.is_system_proxy()))
        if tg_p:
            groups.append(("TelegramProxy", lambda i: i.is_telegram_proxy()))

        targets = []
        counts = {}
        for group_name, belongs in groups:
            cnt = 0
            for inst in self.instances:
                if belongs(inst):
                    cnt += 1
                    if inst not in targets:
                        targets.append(inst)
            counts[group_name] = cnt

        if tg_p:
            try:
                from vless2socks import telegram_proxy
                telegram_proxy.get_tg_dispatcher().set_pool(self.instances)
            except Exception as err:
                self._log(f"⚠️ [RestartServices] TelegramProxy pool update warning: {err}")

        def _finish_hud(text):
            try:
                if getattr(self, "restart_hud_win", None) and self.restart_hud_win.winfo_exists():
                    self.restart_hud_win.set_finished(text)
            except Exception:
                pass

        self._set_restart_button_busy(True)

        def _worker():
            restarted_items = []
            try:
                # 1. Kick all proxy restarts at once: each instance stops/starts in its own worker thread
                all_done = threading.Event()
                remaining = [len(targets)]
                done_lock = threading.Lock()

                def _one_done(_ok=None):
                    with done_lock:
                        remaining[0] -= 1
                        if remaining[0] <= 0:
                            all_done.set()

                def _kick():
                    for inst in targets:
                        try:
                            if inst.is_transitioning:
                                self._log(f"⚠️ [RestartServices] {inst.get_display_name()} is busy, skipped.")
                                _one_done()
                                continue
                            inst.restart_async(callback=_one_done)
                        except Exception as err:
                            self._log(f"⚠️ [RestartServices] Proxy restart warning: {err}")
                            _one_done()

                if targets:
                    self._log(f"🔄 [RestartServices] Restarting {len(targets)} proxy instance(s) in parallel...")
                    self.after(0, _kick)
                else:
                    all_done.set()

                # 2. WSL restarts in this thread while the proxies restart in theirs
                if wsl:
                    self._log("🔄 [RestartServices] Restarting WSL...")
                    ok, msg = restart_wsl()
                    if ok:
                        restarted_items.append("WSL")
                        self._log("✅ [RestartServices] WSL successfully restarted.")
                    else:
                        self._log(f"⚠️ [RestartServices] {msg}")

                if not all_done.wait(timeout=RESTART_PROXIES_TIMEOUT_SEC):
                    self._log(f"⚠️ [RestartServices] {remaining[0]} proxy instance(s) did not confirm restart in time.")

                for group_name, cnt in counts.items():
                    if group_name != "TelegramProxy":
                        restarted_items.append(f"{group_name} ({cnt})")
                        self._log(f"✅ [RestartServices] Restarted {cnt} {group_name} instance(s).")

                # 3. TelegramProxy dispatcher
                if tg_p:
                    cnt = counts.get("TelegramProxy", 0)
                    try:
                        from vless2socks import telegram_proxy
                        disp = telegram_proxy.get_tg_dispatcher()
                        disp.rotate_to_next()
                        if not disp.is_running():
                            disp.start()
                    except Exception as err:
                        self._log(f"⚠️ [RestartServices] TelegramProxy dispatcher restart warning: {err}")
                    restarted_items.append(f"TelegramProxy ({cnt})" if cnt else "TelegramProxy")
                    self._log(f"✅ [RestartServices] Restarted TelegramProxy ({cnt} instance(s)).")

                self.after(500, self.refresh_overview)
                self.after(500, self._update_header_stats)
                summary_str = ", ".join(restarted_items) if restarted_items else "Done"
                self._log(f"🎉 [RestartServices] Finished restarting: {summary_str}.")
                self.after(0, lambda: self._set_restart_button_success(summary_str))
                self.after(0, lambda: _finish_hud(summary_str))
            except Exception as e:
                self._log(f"❌ [RestartServices] Error: {e}")
                self.after(0, lambda: self._set_restart_button_busy(False))
                self.after(0, lambda: _finish_hud("Error"))

        threading.Thread(target=_worker, daemon=True).start()

    def after(self, ms, func=None, *args):
        """Thread-safe ``after``: calls made from worker threads are queued and scheduled by the GUI thread."""
        if func is not None and threading.current_thread() is not threading.main_thread():
            pending = getattr(self, "_ui_queue", None)
            if pending is not None:
                pending.put((ms, func, args))
                return None
        return super().after(ms, func, *args)

    def _drain_ui_queue(self):
        """GUI-thread pump for callbacks queued by worker threads through ``after``."""
        try:
            while True:
                ms, func, args = self._ui_queue.get_nowait()
                try:
                    super().after(ms, func, *args)
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            super().after(UI_QUEUE_POLL_MS, self._drain_ui_queue)

    def _log(self, msg: str):
        """Application-level logger for background service operations."""
        if threading.current_thread() is not threading.main_thread():
            # The HUD and the WSL log are widgets: hand the line over to the GUI thread
            try:
                self.after(0, self._log, msg)
            except Exception:
                pass
            return
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
                text=f"⏳ {t('msg_restart_in_progress')}", state=tk.DISABLED, variant="secondary",
            )
        else:
            self.btn_restart_services.config(
                text=f"🔄 {t('btn_restart_services')}", state=tk.NORMAL, variant="primary",
            )

    def _set_restart_button_success(self, text: str):
        """Briefly show success badge on button before reverting to normal."""
        if not hasattr(self, "btn_restart_services") or not self.btn_restart_services or not self.btn_restart_services.winfo_exists():
            return
        self.btn_restart_services.config(text=f"✅ {text}", state=tk.NORMAL, variant="success")
        self.after(2500, lambda: self._set_restart_button_busy(False))

    # ── System Tray ───────────────────────────────────────────
    def _build_tray_menu(self):
        """Build tray context menu with RestartServices and its settings submenu."""
        return pystray.Menu(
            pystray.MenuItem(t("tray_show"), self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"🔄 {t('btn_restart_services')}", self._tray_restart_services),
            pystray.MenuItem(f"✈️ {t('lbl_restart_telegram_proxy')}", self._tray_restart_telegram_proxy),
            pystray.MenuItem(
                lambda _: f"🐧 {t('lbl_wsl_watchdog')}: {self._wsl_watchdog_status_text()}",
                self._tray_toggle_wsl_watchdog,
                checked=lambda _: bool(settings_manager.get_setting("wsl_watchdog_enabled", True)),
            ),
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
                    pystray.MenuItem(
                        f"✈️ {t('lbl_restart_telegram_proxy')}",
                        self._tray_toggle_telegram_proxy,
                        checked=lambda _: bool(settings_manager.get_setting("restart_telegram_proxy", True)),
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

    def _tray_restart_telegram_proxy(self, icon=None, item=None):
        """Restart only TelegramProxy (flagged instances + dispatcher) from system tray context menu."""
        self.after(0, lambda: self.restart_single_service("telegram"))

    def _tray_toggle_telegram_proxy(self, icon=None, item=None):
        cur = bool(settings_manager.get_setting("restart_telegram_proxy", True))
        new_val = not cur
        settings_manager.set_setting("restart_telegram_proxy", new_val)
        self.restart_telegram_proxy_var.set(new_val)
        if self._tray_icon:
            self._tray_icon.update_menu()

    def restart_single_service(self, service: str):
        """Restart one RestartServices entry ("wsl" / "work" / "system" / "telegram") regardless of the checkboxes."""
        # A restart is already running (main button is disabled while busy)
        if self.btn_restart_services and str(self.btn_restart_services["state"]) == tk.DISABLED:
            return
        self.execute_restart_services(only=service)

    def _tray_toggle_wsl_watchdog(self, icon=None, item=None):
        new_val = not bool(settings_manager.get_setting("wsl_watchdog_enabled", True))
        settings_manager.set_setting("wsl_watchdog_enabled", new_val)
        self.wsl_watchdog.set_enabled(new_val)
        if self._tray_icon:
            self._tray_icon.update_menu()

    def _wsl_watchdog_status_text(self) -> str:
        watchdog = getattr(self, "wsl_watchdog", None)
        if not watchdog or not watchdog.enabled:
            return "off"
        state = watchdog.get_state()
        return "recovering" if state["recovering"] else state["status"]

    def _tray_refresh_menu(self):
        try:
            if self._tray_icon:
                self._tray_icon.update_menu()
        except Exception:
            pass

    def _tray_notify(self, message: str):
        """Balloon from the tray icon; safe from any thread, silent when there is no tray."""
        try:
            if self._tray_icon:
                self._tray_icon.notify(message[:250], "vless2socks")
        except Exception:
            pass

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

    def _ipc_on_show(self):
        try:
            self.after(0, self._do_show)
        except Exception:
            pass

    def _ipc_on_restart(self):
        try:
            self.after(0, self.restart_all)
        except Exception:
            pass

    def _on_power_resume(self):
        self._log("⚡ [PowerWatcher] System resumed from sleep. Scheduling self-recovery in 2.5s...")
        if getattr(self, "wsl_watchdog", None):
            self.wsl_watchdog.note_resume()
        try:
            self.after(2500, self._perform_power_recovery)
        except Exception:
            pass

    def _perform_power_recovery(self):
        self._log("⚡ [PowerWatcher] Re-healing proxies and restoring tray icon after sleep...")
        self._reinit_tray()
        for inst in list(self.instances):
            try:
                if inst.running or inst._should_run:
                    inst._upstream_failures = 0
                    inst._last_tunnel_probe = 0.0
                    self._log(f"⚡ [PowerWatcher] Restarting active proxy {inst.cfg.get('name', '')}...")
                    inst.restart(blocking=False)
            except Exception as e:
                self._log(f"⚠️ [PowerWatcher] Error restarting {inst.cfg.get('name', '')}: {e}")
        self.refresh_overview()
        self.update_tray_icon()
        self._update_header_stats()

    def _reinit_tray(self):
        """Re-initializes pystray icon if the OS notification area desynced during sleep."""
        if not HAS_TRAY:
            return
        try:
            if self._tray_icon:
                self._tray_icon.stop()
        except Exception:
            pass
        self._tray_icon = None
        self._tray_thread = None
        self.after(300, self._setup_tray)
        self.after(600, self.update_tray_icon)

    def _show_from_tray(self, icon=None, item=None):
        self.after(0, self._do_show)

    def _do_show(self):
        try:
            self.deiconify()
        except Exception:
            pass
        self._hidden = False

        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = self.winfo_id()
                parent_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
                target_hwnd = parent_hwnd if parent_hwnd else hwnd
                ctypes.windll.user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            except Exception:
                pass

        try:
            self.lift()
            self.attributes("-topmost", True)
            self.attributes("-topmost", False)
            self.focus_force()
        except Exception:
            pass

    def _exit_from_tray(self, icon=None, item=None):
        self.after(0, self._exit)

    def _exit(self):
        if getattr(self, "_power_watcher", None):
            try:
                self._power_watcher.stop()
            except Exception:
                pass
        if getattr(self, "_ipc_server", None):
            try:
                self._ipc_server.stop()
            except Exception:
                pass
        if getattr(self, "wsl_watchdog", None):
            try:
                self.wsl_watchdog.stop()
            except Exception:
                pass
        if getattr(self, "zapret_watcher", None):
            try:
                self.zapret_watcher.stop()
            except Exception:
                pass
        if getattr(self, "restart_hud_win", None):
            try:
                self.restart_hud_win.close()
            except Exception:
                pass
        self.save_all()
        # Out of sight at once: the proxies go down in the background, all together
        try:
            self.withdraw()
        except Exception:
            pass
        shutdown = self.shutdown_all_proxies()
        deadline = time.monotonic() + EXIT_TIMEOUT_S

        def finish():
            if shutdown.is_alive() and time.monotonic() < deadline:
                self.after(50, finish)
                return
            if self._tray_icon:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass
            self.destroy()

        finish()

    def shutdown_all_proxies(self) -> threading.Thread:
        """Kill every proxy at once on exit, in a background thread.

        Stopping them one by one cost a taskkill, a wait of up to 2 s and two netstat
        runs per proxy on the GUI thread. Here all launcher/xray trees are terminated
        in one pass through the Win32 API, then a single port sweep catches leftovers
        (proxies adopted from an older session, orphaned xray).
        """
        procs = []
        ports: set[int] = set()
        files = []
        for inst in self.instances:
            inst._manual_stop = True
            inst._should_run = False
            try:
                inst._cancel_reconnect()
            except Exception:
                pass
            if inst.process is not None:
                procs.append(inst.process)
            try:
                port = int(inst.get_listen()[1])
                ports.update((port, inst.get_http_port()))
                files.append(ROOT_DIR / "runtime" / f"xray-config-{port}.json")
            except ValueError:
                pass
            files.append(inst.config_path)
            inst.process = None
            inst.running = False
            inst.healthy = False

        def worker():
            try:
                kill_process_trees([p.pid for p in procs])
            except Exception:
                pass
            end = time.monotonic() + 3.0
            for proc in procs:
                try:
                    proc.wait(timeout=max(0.05, end - time.monotonic()))
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            try:
                free_ports(ports)
            except Exception:
                pass
            for path in files:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass

        thread = threading.Thread(target=worker, daemon=True, name="shutdown-proxies")
        thread.start()
        return thread

    # ── Background Poll Loop ──────────────────────────────────
    # ── Background Poll Loop (Non-blocking Asynchronous) ──────
    def _poll_loop(self):
        # One sweep at a time: a sweep over dozens of proxies (up to 0.6 s port check plus a
        # 1 s tunnel probe each) used to outlast the 4 s period, so sweeps piled up as
        # parallel threads competing with the GUI thread.
        if not getattr(self, "_poll_sweep_running", False):
            self._poll_sweep_running = True
            instances = list(self.instances)

            def _bg_poll():
                statuses = []
                try:
                    from concurrent.futures import ThreadPoolExecutor

                    def check(inst):
                        try:
                            return inst, inst.check_health_sync(timeout=0.6)
                        except Exception:
                            return inst, None

                    # Checks wait on sockets, not on the CPU: run them side by side
                    with ThreadPoolExecutor(max_workers=HEALTH_CHECK_WORKERS, thread_name_prefix="health") as pool:
                        statuses = [(inst, alive) for inst, alive in pool.map(check, instances) if alive is not None]
                finally:
                    try:
                        self.after(0, lambda: self._apply_poll_results(statuses))
                    except Exception:
                        self._poll_sweep_running = False

            threading.Thread(target=_bg_poll, daemon=True, name="health-sweep").start()

        # Check periodic auto-backup asynchronously
        def _bg_backup():
            try:
                ok, msg, path = backup_manager.check_and_run_auto_backup()
                if ok:
                    self.after(0, self._on_auto_backup_done)
            except Exception:
                pass
        threading.Thread(target=_bg_backup, daemon=True).start()

        self.after(4000, self._poll_loop)

    def _apply_poll_results(self, statuses):
        self._poll_sweep_running = False
        changed = False
        for inst, alive in statuses:
            if inst in self.instances:
                if inst.apply_health_status(alive):
                    changed = True
        self._report_telegram_health()
        if changed:
            self.update_tray_icon()
            self._update_header_stats()

    def _on_auto_backup_done(self):
        if hasattr(self, "backup_list_container"):
            self._render_available_backups()
            if hasattr(self, "last_backup_time_lbl"):
                now_str = settings_manager.get_setting("last_backup_time", "-")
                self.last_backup_time_lbl.config(text=t("lbl_last_backup_time", time=now_str), fg=C["green"])


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
        try:
            if request_show_running_instance():
                return 0
        except Exception:
            pass
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
