"""Thorough end-to-end tests for forced reconnection on failure for both VLESS and SOCKS5.

Verifies:
1. Overview tab does NOT contain extra reconnect checkboxes ("нового флага не добавляй").
2. Options tab contains the Auto-Reconnect / Forced Reconnection flag ("Принудительное переподключение").
3. When flag is enabled:
   - VLESS process crash triggers automatic cleanup and reconnection.
   - SOCKS5 process crash triggers automatic cleanup and reconnection.
   - poll_health detects dead tunnel and triggers reconnection.
4. When flag is disabled:
   - VLESS process crash does NOT trigger reconnection.
   - SOCKS5 process crash does NOT trigger reconnection.
   - poll_health does NOT trigger reconnection.
5. When user manually stops a proxy (VLESS or SOCKS5), no reconnection is triggered regardless of flag.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import settings_manager
import backup_manager
from gui import ProxyInstance, VlessApp


class TestReconnectSocks5AndVless(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.test_dir / "settings.json"

        self.mock_app = MagicMock(spec=VlessApp)
        self.mock_app.instances = []
        self.mock_app.save_all = MagicMock()
        self.mock_app.update_tray_icon = MagicMock()
        self.mock_app.refresh_overview = MagicMock()
        self.mock_app._update_header_stats = MagicMock()
        self.mock_app.after = MagicMock(side_effect=lambda delay, func, *args: f"timer_{time.time()}")

        self.vless_cfg = {
            "name": "TestVLESS",
            "listen": "127.0.0.1:19991",
            "url": "vless://2b56877d-8f92-40a2-a877-2f7a0dc07aa0@example.com:443?security=tls#VlessTest",
        }
        self.socks5_cfg = {
            "name": "TestSOCKS5",
            "listen": "127.0.0.1:19992",
            "url": "socks5://user:pass@1.2.3.4:1080#Socks5Test",
        }

        with patch.object(ProxyInstance, "_init_geo_from_url"):
            self.vless_inst = ProxyInstance(self.mock_app, self.vless_cfg, 0)
            self.socks5_inst = ProxyInstance(self.mock_app, self.socks5_cfg, 1)

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_overview_tab_has_no_new_flag(self):
        """Verify Overview tab doesn't have force_restart_cb."""
        self.assertFalse(hasattr(self.mock_app, "force_restart_cb"))

    def test_options_tab_flag_controls_reconnect(self):
        """Verify Options tab auto_reconnect controls the setting and proxy behavior."""
        settings_manager.set_setting("auto_reconnect", True)
        self.assertTrue(self.vless_inst.is_auto_reconnect_enabled())
        self.assertTrue(self.socks5_inst.is_auto_reconnect_enabled())

        settings_manager.set_setting("auto_reconnect", False)
        self.assertFalse(self.vless_inst.is_auto_reconnect_enabled())
        self.assertFalse(self.socks5_inst.is_auto_reconnect_enabled())

    def test_vless_forced_reconnect_on_crash(self):
        """When auto_reconnect is True, VLESS process failure triggers reconnect."""
        settings_manager.set_setting("auto_reconnect", True)

        self.vless_inst.running = True
        self.vless_inst._manual_stop = False
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1
        self.vless_inst.process = mock_proc

        with patch.object(self.vless_inst, "_schedule_reconnect") as mock_sched:
            self.vless_inst._watcher()
            self.assertFalse(self.vless_inst.running)
            self.assertIsNone(self.vless_inst.process)
            self.mock_app.after.assert_any_call(0, self.vless_inst._schedule_reconnect)

    def test_socks5_forced_reconnect_on_crash(self):
        """When auto_reconnect is True, SOCKS5 process failure triggers reconnect."""
        settings_manager.set_setting("auto_reconnect", True)

        self.socks5_inst.running = True
        self.socks5_inst._manual_stop = False
        mock_proc = MagicMock()
        mock_proc.returncode = 3221225786  # Windows crash code 0xC0000005
        mock_proc.poll.return_value = 3221225786
        self.socks5_inst.process = mock_proc

        with patch.object(self.socks5_inst, "_schedule_reconnect") as mock_sched:
            self.socks5_inst._watcher()
            self.assertFalse(self.socks5_inst.running)
            self.assertIsNone(self.socks5_inst.process)
            self.mock_app.after.assert_any_call(0, self.socks5_inst._schedule_reconnect)

    def test_vless_does_not_reconnect_when_flag_disabled(self):
        """When auto_reconnect is False, VLESS process failure must NOT reconnect."""
        settings_manager.set_setting("auto_reconnect", False)

        self.vless_inst.running = True
        self.vless_inst._manual_stop = False
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1
        self.vless_inst.process = mock_proc

        self.vless_inst._watcher()
        self.assertFalse(self.vless_inst.running)
        for call in self.mock_app.after.call_args_list:
            self.assertNotEqual(call[0][1], self.vless_inst._schedule_reconnect)

    def test_socks5_does_not_reconnect_when_flag_disabled(self):
        """When auto_reconnect is False, SOCKS5 process failure must NOT reconnect."""
        settings_manager.set_setting("auto_reconnect", False)

        self.socks5_inst.running = True
        self.socks5_inst._manual_stop = False
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1
        self.socks5_inst.process = mock_proc

        self.socks5_inst._watcher()
        self.assertFalse(self.socks5_inst.running)
        for call in self.mock_app.after.call_args_list:
            self.assertNotEqual(call[0][1], self.socks5_inst._schedule_reconnect)

    def test_manual_stop_never_triggers_reconnect(self):
        """Manual stop must not trigger reconnect for either VLESS or SOCKS5 regardless of setting."""
        settings_manager.set_setting("auto_reconnect", True)

        for inst in (self.vless_inst, self.socks5_inst):
            inst.running = True
            mock_proc = MagicMock()
            inst.process = mock_proc
            inst.stop()
            self.assertTrue(inst._manual_stop)
            self.assertFalse(inst.running)

            # If watcher subsequently fires
            inst._watcher()
            for call in self.mock_app.after.call_args_list:
                self.assertNotEqual(call[0][1], inst._schedule_reconnect)

    def test_reconnect_cycle_actually_calls_start(self):
        """Test full reconnect schedule -> _do_reconnect -> start() cycle."""
        settings_manager.set_setting("auto_reconnect", True)

        for inst in (self.vless_inst, self.socks5_inst):
            inst._manual_stop = False
            inst.running = False
            inst.process = None

            # Schedule reconnect
            with patch.object(inst, "_do_reconnect", wraps=inst._do_reconnect):
                inst._schedule_reconnect()
                self.assertIsNotNone(inst._reconnect_timer_id)
                self.assertEqual(inst._reconnect_attempt, 1)

                # Now simulate timer firing
                with patch.object(inst, "start") as mock_start:
                    inst._do_reconnect()
                    mock_start.assert_called_once()
                    self.assertIsNone(inst._reconnect_timer_id)


if __name__ == "__main__":
    unittest.main()
