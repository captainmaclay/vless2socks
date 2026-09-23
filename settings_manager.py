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
