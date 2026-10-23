"""
Xshell 8 Setup & Automation Package for vless2socks
"""

from .installer import XshellInstaller
from .shortcuts import XshellShortcutManager
from .proxy import XshellProxyManager
from .session import (
    XshellSessionManager,
    encrypt_password,
    decrypt_password,
    get_windows_identity,
)

__all__ = [
    "XshellInstaller",
    "XshellShortcutManager",
    "XshellProxyManager",
    "XshellSessionManager",
    "encrypt_password",
    "decrypt_password",
    "get_windows_identity",
]
