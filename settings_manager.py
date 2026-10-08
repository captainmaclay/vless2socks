"""Settings manager for vlesstosocks5.

Stores persistent user preferences in settings.json:
- language ('en' by default, 'ru')
- force_port_takeover (True by default): Reclaim busy ports before starting proxy
- autostart_proxies (True by default): Start all configured proxies on app launch
- force_restart (True by default): Automatically restart proxies on crash or disconnect
- start_minimized_tray (False by default): Launch minimized in system tray
- auto_reconnect (True by default): Automatically attempt to restart failed proxies
- reconnect_intervals ("10, 15, 30, 60, 120, 180, 30" by default): Retry backoff schedule
- auto_backup_enabled (False by default): Periodic background backup
- backup_interval_hours (24 by default): Backup interval in hours
- backup_dir: Folder for saving .hbak backups
- last_backup_time: Timestamp of last successful backup
- backup_password: Master encryption password
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vless2socks.paths import APP_DIR

ROOT_DIR = APP_DIR
SETTINGS_FILE = ROOT_DIR / "settings.json"

DEFAULT_BACKUP_DIR = str(Path.home() / "Documents" / "VlessBackups")
DEFAULT_RECONNECT_INTERVALS = "10, 15, 30, 60, 120, 180, 30"

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "en",
    "force_port_takeover": True,
    "autostart_proxies": True,
    "force_restart": True,
    "start_minimized_tray": False,
    "auto_reconnect": True,
    "reconnect_intervals": DEFAULT_RECONNECT_INTERVALS,
    "auto_backup_enabled": False,
    "backup_interval_hours": 24,
    "backup_dir": DEFAULT_BACKUP_DIR,
    "last_backup_time": "-",
    "backup_password": "",
    "system_proxy": "127.0.0.1:1015",
    "work_proxy": "127.0.0.1:1030",
    "System_Proxy": True,
    "Work_Proxy": True,
    "restart_wsl": True,
    "restart_work_proxy": True,
    "restart_system_proxy": True,
    "zapret2_watcher_enabled": True,
    "zapret2_watch_main": True,
    "zapret2_watch_tg": True,
}


def parse_reconnect_intervals(raw_val: Any) -> list[int]:
    """Parse comma-separated intervals string into list of positive integers in seconds."""
    if isinstance(raw_val, list):
        parsed = [int(x) for x in raw_val if str(x).isdigit() and int(x) > 0]
        return parsed if parsed else [10, 15, 30, 60, 120, 180, 30]
    if isinstance(raw_val, str):
        parts = [p.strip() for p in raw_val.split(",") if p.strip()]
        res = []
        for p in parts:
            try:
                v = int(p)
                if v > 0:
                    res.append(v)
            except ValueError:
                pass
        return res if res else [10, 15, 30, 60, 120, 180, 30]
    return [10, 15, 30, 60, 120, 180, 30]


def load_settings() -> dict[str, Any]:
    """Load settings from settings.json, falling back to defaults."""
    cfg = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception:
            pass
    return cfg


def save_settings(settings: dict[str, Any]) -> None:
    """Save full settings dict to settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass


def get_setting(key: str, default: Any = None) -> Any:
    """Get single setting value."""
    cfg = load_settings()
    if default is not None:
        return cfg.get(key, default)
    return cfg.get(key, DEFAULT_SETTINGS.get(key))


def set_setting(key: str, value: Any) -> None:
    """Update and persist a single setting value."""
    cfg = load_settings()
    cfg[key] = value
    save_settings(cfg)


def get_system_proxy_target() -> str:
    """Get configured endpoint for System_Proxy, default 127.0.0.1:1015."""
    return str(get_setting("system_proxy", "127.0.0.1:1015"))


def get_work_proxy_target() -> str:
    """Get configured endpoint for Work_Proxy, default 127.0.0.1:1030."""
    return str(get_setting("work_proxy", "127.0.0.1:1030"))


def is_system_proxy_enabled() -> bool:
    """Check if System_Proxy flag is active."""
    return bool(get_setting("System_Proxy", get_setting("system_proxy_flag", True)))


def is_work_proxy_enabled() -> bool:
    """Check if Work_Proxy flag is active."""
    return bool(get_setting("Work_Proxy", get_setting("work_proxy_flag", True)))


def get_restart_services_flags() -> dict[str, bool]:
    """Get active flags for RestartServices."""
    return {
        "wsl": bool(get_setting("restart_wsl", True)),
        "work_proxy": bool(get_setting("restart_work_proxy", True)),
        "system_proxy": bool(get_setting("restart_system_proxy", True)),
        "telegram_proxy": bool(get_setting("restart_telegram_proxy", True)),
    }


def set_restart_services_flags(
    wsl: bool,
    work_proxy: bool,
    system_proxy: bool,
    telegram_proxy: bool = True,
) -> None:
    """Persist user checkbox choices for RestartServices."""
    cfg = load_settings()
    cfg["restart_wsl"] = bool(wsl)
    cfg["restart_work_proxy"] = bool(work_proxy)
    cfg["restart_system_proxy"] = bool(system_proxy)
    cfg["restart_telegram_proxy"] = bool(telegram_proxy)
    save_settings(cfg)


DEFAULT_TELEGRAM_PROXY_PORT = 1373


def get_telegram_proxy_port() -> int:
    """Return user-configured TelegramProxy virtual socket port (default 1373)."""
    val = get_setting("telegram_proxy_port", DEFAULT_TELEGRAM_PROXY_PORT)
    try:
        p = int(val)
        if 1 <= p <= 65535:
            return p
    except Exception:
        pass
    return DEFAULT_TELEGRAM_PROXY_PORT


def set_telegram_proxy_port(port: int) -> None:
    """Persist user-configured TelegramProxy virtual socket port."""
    p = int(port)
    set_setting("telegram_proxy_port", p)


def get_zapret2_settings() -> dict[str, bool]:
    """Get active preferences for Zapret2 Watcher."""
    return {
        "enabled": bool(get_setting("zapret2_watcher_enabled", True)),
        "watch_main": bool(get_setting("zapret2_watch_main", True)),
        "watch_tg": bool(get_setting("zapret2_watch_tg", True)),
    }


def set_zapret2_settings(enabled: bool, watch_main: bool, watch_tg: bool) -> None:
    """Persist user choices for Zapret2 Watcher."""
    cfg = load_settings()
    cfg["zapret2_watcher_enabled"] = bool(enabled)
    cfg["zapret2_watch_main"] = bool(watch_main)
    cfg["zapret2_watch_tg"] = bool(watch_tg)
    save_settings(cfg)

