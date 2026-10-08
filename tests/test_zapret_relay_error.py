"""
Tests for Telegram Proxy integration in ZapretRecovery:
Verifies that ZapretWatcher monitors the internal vless2socks Telegram Proxy,
handles recovery, and updates UI state correctly.
"""

import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ZapretRecovery
from ZapretRecovery import core
from ZapretRecovery.watcher import Zapret2Watcher
from ZapretRecovery.widget import ZapretRecoveryWidget, THEME
from vless2socks import telegram_proxy


class TestInternalTelegramProxyWatch(unittest.TestCase):

    def test_internal_tg_proxy_detects_all_proxies_down(self):
        """Когда пул пуст или все прокси недоступны, детектор фиксирует ошибку."""
        with patch("vless2socks.telegram_proxy.get_tg_dispatcher") as mock_get_disp:
            disp = MagicMock()
            disp.is_running.return_value = True
            disp.get_pool.return_value = [{"host": "127.0.0.1", "port": 1081}]
            disp.get_active_proxy.return_value = None  # ни один не доступен
            mock_get_disp.return_value = disp

            has_err, msg = core.detect_tg_proxy_relay_error()
            self.assertTrue(has_err)
            self.assertIn("недоступны", msg)

    def test_internal_tg_proxy_healthy_state(self):
        """Когда активный прокси доступен, детектор возвращает False."""
        with patch("vless2socks.telegram_proxy.get_tg_dispatcher") as mock_get_disp:
            disp = MagicMock()
            disp.is_running.return_value = True
            disp.get_pool.return_value = [{"host": "127.0.0.1", "port": 1081}]
            disp.get_active_proxy.return_value = {"host": "127.0.0.1", "port": 1081}
            mock_get_disp.return_value = disp

            has_err, msg = core.detect_tg_proxy_relay_error()
            self.assertFalse(has_err)
            self.assertEqual(msg, "")

    def test_watcher_reconnects_internal_tg_proxy_on_failure(self):
        """Watcher при сбое внутреннего TG Proxy вызывает restart_tg_proxy."""
        tg_socket_running = [False]
        restart_called = []

        def mock_is_tg():
            return tg_socket_running[0]

        def mock_restart_tg():
            restart_called.append(time.time())
            tg_socket_running[0] = True
            return True

        with patch("ZapretRecovery.watcher.core.is_winws2_running", return_value=True), \
             patch("ZapretRecovery.watcher.core.is_tg_proxy_running", side_effect=mock_is_tg), \
             patch("ZapretRecovery.watcher.core.get_configured_telegram_proxies", return_value=[{"host": "127.0.0.1", "port": 1081}]), \
             patch("ZapretRecovery.watcher.core.is_endpoint_reachable", side_effect=lambda h, p: mock_is_tg()), \
             patch("ZapretRecovery.watcher.core.detect_tg_proxy_relay_error", return_value=(False, "")), \
             patch("ZapretRecovery.watcher.core.start_tg_proxy", side_effect=mock_restart_tg), \
             patch("ZapretRecovery.watcher.core.restart_tg_proxy", side_effect=mock_restart_tg):

            events = []
            watcher = Zapret2Watcher(
                enabled=True,
                watch_main=True,
                watch_tg=True,
                status_callback=lambda s: events.append(dict(s)),
            )
            watcher.RETRY_SCHEDULE = [0.05, 0.1]
            watcher.NORMAL_POLL_INTERVAL = 0.05

            watcher.start()
            time.sleep(0.2)
            watcher.stop()

            self.assertGreaterEqual(len(restart_called), 1)
            final_state = watcher.get_state()
            self.assertTrue(final_state["tg_ok"])
            self.assertFalse(final_state["recovering"])

    def test_ui_widget_displays_relay_error_state(self):
        """Виджет отображает статус TG: RELAY ERR и текст реконнекта."""
        root = tk.Tk()
        root.withdraw()
        try:
            widget = ZapretRecoveryWidget(root)

            # Симулируем ошибку
            error_state = {
                "enabled": True,
                "watch_main": True,
                "watch_tg": True,
                "main_ok": True,
                "tg_ok": False,
                "relay_error": True,
                "relay_error_msg": "Все прокси недоступны",
                "recovering": True,
                "retry_count": 1,
            }
            widget._apply_watcher_state(error_state)

            self.assertEqual(widget.badge_tg.cget("text"), "TG: RELAY ERR")
            self.assertEqual(widget.badge_tg.cget("fg"), THEME["orange"])
            self.assertIn("Реконнект TG", widget.lbl_retry_info.cget("text"))

            # Симулируем восстановление
            ok_state = {
                "enabled": True,
                "watch_main": True,
                "watch_tg": True,
                "main_ok": True,
                "tg_ok": True,
                "relay_error": False,
                "relay_error_msg": "",
                "recovering": False,
                "retry_count": 0,
            }
            widget._apply_watcher_state(ok_state)

            self.assertEqual(widget.badge_tg.cget("text"), "TG Proxy: OK")
            self.assertEqual(widget.badge_tg.cget("fg"), THEME["green_fg"])
            self.assertEqual(widget.lbl_retry_info.cget("text"), "")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
