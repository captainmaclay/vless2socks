"""Unit tests verifying Force-Restart flag on Overview tab, persistence, backup export/import, and reconnect behavior for VLESS and SOCKS5."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backup_manager
import settings_manager
from gui import ProxyInstance, VlessApp, is_port_alive


class ForceRestartSettingsAndBackupTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.orig_root = backup_manager.ROOT_DIR
        self.orig_settings = backup_manager.SETTINGS_FILE
        self.orig_settings_mgr_file = settings_manager.SETTINGS_FILE

        backup_manager.ROOT_DIR = self.test_dir
        backup_manager.SETTINGS_FILE = self.test_dir / "settings.json"
        settings_manager.SETTINGS_FILE = self.test_dir / "settings.json"

    def tearDown(self):
        backup_manager.ROOT_DIR = self.orig_root
        backup_manager.SETTINGS_FILE = self.orig_settings
        settings_manager.SETTINGS_FILE = self.orig_settings_mgr_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_force_restart_is_true(self):
        # When settings.json doesn't exist, default should be True
        val = settings_manager.get_setting("force_restart")
        self.assertTrue(val)
        self.assertTrue(settings_manager.DEFAULT_SETTINGS.get("force_restart"))

    def test_force_restart_persistence_across_restarts(self):
        # Toggle to False
        settings_manager.set_setting("force_restart", False)
        self.assertFalse(settings_manager.get_setting("force_restart"))

        # Re-read directly from disk
        with open(self.test_dir / "settings.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("force_restart", data)
        self.assertFalse(data["force_restart"])

        # Toggle back to True
        settings_manager.set_setting("force_restart", True)
        self.assertTrue(settings_manager.get_setting("force_restart"))

    def test_force_restart_preserved_in_backup_export_and_import(self):
        # 1. Set force_restart to False
        settings_manager.set_setting("force_restart", False)
        self.assertFalse(settings_manager.get_setting("force_restart"))

        # 2. Export encrypted backup
        pwd = "SecretPassword123!"
        backup_file = backup_manager.export_encrypted_backup(pwd, dest_dir=self.test_dir)
        self.assertTrue(backup_file.exists())

        # 3. Simulate reset / changed setting
        settings_manager.set_setting("force_restart", True)
        self.assertTrue(settings_manager.get_setting("force_restart"))

        # 4. Restore backup
        restored = backup_manager.restore_encrypted_backup(backup_file, pwd)
        self.assertGreaterEqual(restored, 0)

        # 5. Verify restored force_restart is False
        restored_val = settings_manager.get_setting("force_restart")
        self.assertFalse(restored_val)


class ForceRestartBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.orig_settings_file = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.test_dir / "settings.json"

        self.mock_app = MagicMock()
        self.mock_app.instances = []
        self.mock_app.save_all = MagicMock()
        self.mock_app.update_tray_icon = MagicMock()
        self.mock_app.refresh_overview = MagicMock()
        self.mock_app._update_header_stats = MagicMock()

        self.vless_cfg = {
            "name": "TestVLESS",
            "listen": "127.0.0.1:18881",
            "url": "vless://uuid@domain.com:443?security=tls#vless_node",
        }
        self.socks5_cfg = {
            "name": "TestSOCKS5",
            "listen": "127.0.0.1:18882",
            "url": "socks5://user:pass@domain.com:1080#socks_node",
        }

        with patch.object(ProxyInstance, "_init_geo_from_url"):
            self.vless_inst = ProxyInstance(self.mock_app, self.vless_cfg, 0)
            self.socks5_inst = ProxyInstance(self.mock_app, self.socks5_cfg, 1)

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_vless_reconnects_when_force_restart_enabled(self):
        # When force_restart is True, failure triggers reconnect
        with patch("settings_manager.get_setting", side_effect=lambda k, d=None: True if k in ("force_restart", "auto_reconnect") else d):
            # 1. Test process termination in _watcher
            self.vless_inst.running = True
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.poll.return_value = 1
            self.vless_inst.process = mock_proc

            with patch.object(self.vless_inst, "_schedule_reconnect") as mock_sched:
                self.vless_inst._watcher()
                self.mock_app.after.assert_any_call(0, self.vless_inst._schedule_reconnect)

            # 2. Test dead port in poll_health
            self.vless_inst.running = True
            self.vless_inst.healthy = True
            self.vless_inst.process = None

            with patch("gui.is_port_alive", return_value=False), \
                 patch.object(self.vless_inst, "_schedule_reconnect") as mock_sched:
                self.vless_inst.poll_health()
                mock_sched.assert_called_once()

    def test_vless_does_not_reconnect_when_force_restart_disabled(self):
        # When auto_reconnect / force_restart is False, failure must NOT trigger reconnect
        with patch("settings_manager.get_setting", side_effect=lambda k, d=None: False if k in ("force_restart", "auto_reconnect") else d):
            # 1. Test process termination in _watcher
            self.vless_inst.running = True
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.poll.return_value = 1
            self.vless_inst.process = mock_proc

            self.vless_inst._watcher()
            # after should not have been called with _schedule_reconnect
            for call in self.mock_app.after.call_args_list:
                self.assertNotEqual(call[0][1], self.vless_inst._schedule_reconnect)

            # 2. Test dead port in poll_health
            self.vless_inst.running = True
            self.vless_inst.healthy = True
            self.vless_inst.process = None

            with patch("gui.is_port_alive", return_value=False), \
                 patch.object(self.vless_inst, "_schedule_reconnect") as mock_sched:
                self.vless_inst.poll_health()
                mock_sched.assert_not_called()

            # 3. Direct call to _schedule_reconnect should early exit
            with patch.object(self.vless_inst, "_do_reconnect") as mock_do:
                self.vless_inst._schedule_reconnect()
                mock_do.assert_not_called()

    def test_socks5_reconnects_when_force_restart_enabled(self):
        # SOCKS5 profile with auto_reconnect / force_restart True
        with patch("settings_manager.get_setting", side_effect=lambda k, d=None: True if k in ("force_restart", "auto_reconnect") else d):
            # 1. Process termination
            self.socks5_inst.running = True
            mock_proc = MagicMock()
            mock_proc.returncode = -9
            mock_proc.poll.return_value = -9
            self.socks5_inst.process = mock_proc

            with patch.object(self.socks5_inst, "_schedule_reconnect"):
                self.socks5_inst._watcher()
                self.mock_app.after.assert_any_call(0, self.socks5_inst._schedule_reconnect)

            # 2. Dead port in poll_health
            self.socks5_inst.running = True
            self.socks5_inst.healthy = True
            self.socks5_inst.process = None

            with patch("gui.is_port_alive", return_value=False), \
                 patch.object(self.socks5_inst, "_schedule_reconnect") as mock_sched:
                self.socks5_inst.poll_health()
                mock_sched.assert_called_once()

    def test_socks5_does_not_reconnect_when_force_restart_disabled(self):
        # SOCKS5 profile with auto_reconnect / force_restart False
        with patch("settings_manager.get_setting", side_effect=lambda k, d=None: False if k in ("force_restart", "auto_reconnect") else d):
            # 1. Process termination
            self.socks5_inst.running = True
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.poll.return_value = 1
            self.socks5_inst.process = mock_proc

            self.socks5_inst._watcher()
            for call in self.mock_app.after.call_args_list:
                self.assertNotEqual(call[0][1], self.socks5_inst._schedule_reconnect)

            # 2. Dead port in poll_health
            self.socks5_inst.running = True
            self.socks5_inst.healthy = True
            self.socks5_inst.process = None

            with patch("gui.is_port_alive", return_value=False), \
                 patch.object(self.socks5_inst, "_schedule_reconnect") as mock_sched:
                self.socks5_inst.poll_health()
                mock_sched.assert_not_called()

            # 3. Direct call to _schedule_reconnect should early exit
            with patch.object(self.socks5_inst, "_do_reconnect") as mock_do:
                self.socks5_inst._schedule_reconnect()
                mock_do.assert_not_called()

    def test_toggle_force_restart_off_cancels_pending_timers(self):
        app = MagicMock(spec=VlessApp)
        app.auto_reconnect_var = MagicMock()
        app.auto_reconnect_var.get.return_value = False
        app.instances = [self.vless_inst, self.socks5_inst]
        app.save_all = MagicMock()

        self.vless_inst._reconnect_timer_id = "timer_1"
        self.socks5_inst._reconnect_timer_id = "timer_2"

        with patch.object(self.vless_inst, "_cancel_reconnect") as cancel_vless, \
             patch.object(self.socks5_inst, "_cancel_reconnect") as cancel_socks:
            VlessApp._on_options_auto_reconnect_toggled(app)

            cancel_vless.assert_called_once()
            cancel_socks.assert_called_once()


if __name__ == "__main__":
    unittest.main()
