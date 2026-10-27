"""Launch vless2socks together with Windows (per-user, no admin rights).

The "launch_at_startup" flag in settings.json is the source of truth; this module
mirrors it into HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run. The entry is
re-synced on every GUI start and after a backup restore, so a restored flag (or an
app folder that moved) always ends up with a correct command line.
"""

from __future__ import annotations

import sys
from pathlib import Path

import settings_manager
from vless2socks.paths import APP_DIR, FROZEN

try:
    import winreg
except ImportError:  # non-Windows: autostart is simply unavailable
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "vless2socks"
SETTING_KEY = "launch_at_startup"


def is_supported() -> bool:
    return winreg is not None


def startup_command() -> str:
    """Command line Windows runs at logon: the .exe itself, or pythonw + gui.py."""
    if FROZEN:
        return f'"{Path(sys.executable).resolve()}"'
    pythonw = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        exe = Path(sys.executable)
        candidate = exe.with_name("pythonw.exe")
        pythonw = candidate if candidate.exists() else exe
    return f'"{pythonw}" "{APP_DIR / "gui.py"}"'


def read_registered_command() -> str | None:
    """Current Run entry value, or None when there is none."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except OSError:
        return None


def is_registered() -> bool:
    return read_registered_command() is not None


def apply(enabled: bool) -> bool:
    """Create or remove the Run entry. Returns True on success."""
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Persist the flag in settings.json and apply it to the registry."""
    settings_manager.set_setting(SETTING_KEY, bool(enabled))
    return apply(bool(enabled))


def sync_from_settings() -> bool:
    """Make the registry match the saved flag (and the current app path)."""
    enabled = bool(settings_manager.get_setting(SETTING_KEY, False))
    if enabled and read_registered_command() == startup_command():
        return True
    if not enabled and not is_registered():
        return True
    return apply(enabled)
