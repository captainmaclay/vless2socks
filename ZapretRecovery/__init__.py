"""
ZapretRecovery - Интеграционный модуль мониторинга и автоматического восстановления
модулей Zapret 2 (winws2.exe и Telegram Proxy) для vless2socks.
"""

from .core import (
    is_winws2_running,
    is_tg_proxy_running,
    start_winws2,
    stop_winws2,
    start_tg_proxy,
    stop_tg_proxy,
    restart_tg_proxy,
    detect_tg_proxy_relay_error,
    get_system_status,
)
from .watcher import Zapret2Watcher
from .widget import ZapretRecoveryWidget, OrangeCheckbox

__all__ = [
    "is_winws2_running",
    "is_tg_proxy_running",
    "start_winws2",
    "stop_winws2",
    "start_tg_proxy",
    "stop_tg_proxy",
    "restart_tg_proxy",
    "detect_tg_proxy_relay_error",
    "get_system_status",
    "Zapret2Watcher",
    "ZapretRecoveryWidget",
    "OrangeCheckbox",
]

