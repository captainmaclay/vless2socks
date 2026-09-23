"""Comprehensive tests for Proxy Killswitch (IP leak protection), persistence, backup and verification."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from vless2socks.config import AppConfig, load_config
from vless2socks.url import SocksServer, VlessServer
from vless2socks.xray.config_builder import build_xray_config, describe_config
from vless2socks.ipcheck import IpReport, check_ip_leak_async
import backup_manager


class KillswitchConfigTest(unittest.TestCase):
    def test_default_killswitch_off(self):
        srv = SocksServer(address="1.1.1.1", port=1080)
        cfg = AppConfig(server=srv)
        self.assertFalse(cfg.killswitch)

    def test_load_config_reads_killswitch(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump({
                "url": "socks5://1.1.1.1:1080#test",
                "killswitch": True,
            }, tf)
            tf_path = tf.name

        try:
            cfg = load_config(tf_path)
            self.assertTrue(cfg.killswitch)
        finally:
            os.unlink(tf_path)

    def test_load_config_defaults_killswitch_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump({
                "url": "socks5://1.1.1.1:1080#test",
            }, tf)
            tf_path = tf.name

        try:
            cfg = load_config(tf_path)
            self.assertFalse(cfg.killswitch)
        finally:
            os.unlink(tf_path)


class KillswitchXrayRoutingTest(unittest.TestCase):
    def setUp(self):
        self.server = SocksServer(address="lv1.example.com", port=20039, username="u", password="p")

    def test_xray_config_without_killswitch(self):
        cfg = AppConfig(server=self.server, killswitch=False)
        xray_cfg = build_xray_config(cfg)
        self.assertNotIn("routing", xray_cfg)
        outbounds = xray_cfg["outbounds"]
        tags = [o.get("tag") for o in outbounds]
        self.assertIn("proxy", tags)
        self.assertIn("direct", tags)
        self.assertNotIn("block", tags)

    def test_xray_config_with_killswitch_enabled(self):
        cfg = AppConfig(server=self.server, killswitch=True)
        xray_cfg = build_xray_config(cfg)
        self.assertIn("routing", xray_cfg)
        routing = xray_cfg["routing"]
        self.assertEqual(routing.get("domainStrategy"), "AsIs")

        rules = routing.get("rules", [])
        self.assertGreaterEqual(len(rules), 2)
        # First rule: user inbounds strictly to proxy
        self.assertEqual(rules[0]["inboundTag"], ["socks-in", "http-in"])
        self.assertEqual(rules[0]["outboundTag"], "proxy")
        # Second rule: fallback from inbounds blocked
        self.assertEqual(rules[1]["inboundTag"], ["socks-in", "http-in"])
        self.assertEqual(rules[1]["outboundTag"], "block")

        outbounds = xray_cfg["outbounds"]
        tags = [o.get("tag") for o in outbounds]
        self.assertIn("block", tags)
        block_outbound = next(o for o in outbounds if o.get("tag") == "block")
        self.assertEqual(block_outbound.get("protocol"), "blackhole")

        desc = describe_config(xray_cfg)
        self.assertIn("killswitch", desc.lower())


class KillswitchBackupTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.orig_root = backup_manager.ROOT_DIR
        self.orig_instances = backup_manager.INSTANCES_FILE
        self.orig_config = backup_manager.CONFIG_FILE
        self.orig_settings = backup_manager.SETTINGS_FILE
        self.orig_env = backup_manager.ENV_FILE

        backup_manager.ROOT_DIR = self.test_dir
        backup_manager.INSTANCES_FILE = self.test_dir / "instances.json"
        backup_manager.CONFIG_FILE = self.test_dir / "config.json"
        backup_manager.SETTINGS_FILE = self.test_dir / "settings.json"
        backup_manager.ENV_FILE = self.test_dir / ".env"

    def tearDown(self):
        backup_manager.ROOT_DIR = self.orig_root
        backup_manager.INSTANCES_FILE = self.orig_instances
        backup_manager.CONFIG_FILE = self.orig_config
        backup_manager.SETTINGS_FILE = self.orig_settings
        backup_manager.ENV_FILE = self.orig_env
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_backup_export_and_import_preserves_killswitch(self):
        instances_data = [
            {"url": "socks5://u:p@lv1.example.com:20039#latvia", "listen": "127.0.0.1:1081", "killswitch": True},
            {"url": "socks5://127.0.0.1:1080#local", "listen": "127.0.0.1:1082", "killswitch": False},
        ]
        with open(backup_manager.INSTANCES_FILE, "w", encoding="utf-8") as f:
            json.dump(instances_data, f)

        backup_dir = self.test_dir / "backups"
        pwd = "TestSecretPassword123!"

        hbak_file = backup_manager.export_encrypted_backup(pwd, dest_dir=backup_dir)
        self.assertTrue(hbak_file.exists())

        # Wipe instances file
        backup_manager.INSTANCES_FILE.unlink()
        self.assertFalse(backup_manager.INSTANCES_FILE.exists())

        # Restore from encrypted backup
        restored_count = backup_manager.restore_encrypted_backup(hbak_file, pwd)
        self.assertEqual(restored_count, 2)
        self.assertTrue(backup_manager.INSTANCES_FILE.exists())

        with open(backup_manager.INSTANCES_FILE, "r", encoding="utf-8") as f:
            restored_instances = json.load(f)

        self.assertEqual(len(restored_instances), 2)
        self.assertTrue(restored_instances[0]["killswitch"])
        self.assertFalse(restored_instances[1]["killswitch"])


class KillswitchLeakCheckTest(unittest.IsolatedAsyncioTestCase):
    async def test_detects_ip_leak_when_direct_equals_tunnel(self):
        mock_report = IpReport(
            direct="195.24.32.10",
            tunnel="195.24.32.10",
            service="api.ipify.org",
            listen="127.0.0.1:1081",
        )
        with patch("vless2socks.ipcheck.compare_ip", return_value=mock_report):
            with patch("vless2socks.ipcheck.is_system_tun_active", return_value=False):
                is_leak, direct_ip, tunnel_ip, msg = await check_ip_leak_async("127.0.0.1", 1081)
                self.assertTrue(is_leak)
                self.assertEqual(direct_ip, "195.24.32.10")
                self.assertEqual(tunnel_ip, "195.24.32.10")
                self.assertIn("LEAK DETECTED", msg)

    async def test_detects_safe_when_system_tun_throne_active(self):
        """Когда в системе активен TUN/Throne, одинаковые IP не считаются утечкой провайдеру."""
        mock_report = IpReport(
            direct="62.60.234.188",
            tunnel="62.60.234.188",
            service="api.ipify.org",
            listen="127.0.0.1:1081",
        )
        with patch("vless2socks.ipcheck.compare_ip", return_value=mock_report):
            with patch("vless2socks.ipcheck.is_system_tun_active", return_value=True):
                is_leak, direct_ip, tunnel_ip, msg = await check_ip_leak_async("127.0.0.1", 1081)
                self.assertFalse(is_leak)
                self.assertIn("SAFE", msg)
                self.assertIn("Throne", msg)

    async def test_detects_safe_when_direct_differs_from_tunnel(self):
        mock_report = IpReport(
            direct="195.24.32.10",
            tunnel="91.200.12.55",
            service="api.ipify.org",
            listen="127.0.0.1:1081",
        )
        with patch("vless2socks.ipcheck.compare_ip", return_value=mock_report):
            is_leak, direct_ip, tunnel_ip, msg = await check_ip_leak_async("127.0.0.1", 1081)
            self.assertFalse(is_leak)
            self.assertEqual(direct_ip, "195.24.32.10")
            self.assertEqual(tunnel_ip, "91.200.12.55")
            self.assertIn("SAFE", msg)


class GuiKillswitchIntegrationTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception:
            self.skipTest("Tkinter display not available")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_gui_killswitch_lifecycle(self):
        import gui
        from tkinter import ttk

        app = self.root
        app.instances = []
        app.save_all = MagicMock()
        app.refresh_current_page_tabs = MagicMock()
        app.refresh_overview = MagicMock()
        app.update_tray_icon = MagicMock()

        cfg = {
            "url": "socks5://demo_user:test_pass123@lv1.example.com:20039#latvia",
            "listen": "127.0.0.1:1081",
            "killswitch": False,
        }
        inst = gui.ProxyInstance(app, cfg, 0)
        self.assertFalse(inst.cfg.get("killswitch"))

        nb = ttk.Notebook(self.root)
        frame = inst.build_tab_ui(nb)
        self.assertIsNotNone(frame)
        self.assertIsNotNone(inst.killswitch_var)
        self.assertFalse(inst.killswitch_var.get())

        # Toggle killswitch ON
        inst.killswitch_var.set(True)
        inst._on_killswitch_toggled()
        self.assertTrue(inst.cfg["killswitch"])
        self.assertTrue(inst.get_config()["killswitch"])
        app.save_all.assert_called()

        # Mock leak detection stopping proxy when no TUN is active
        inst.running = True
        inst.stop = MagicMock()
        with patch("vless2socks.ipcheck.is_system_tun_active", return_value=False):
            with patch("vless2socks.ipcheck.check_ip_leak", return_value=(True, "1.1.1.1", "1.1.1.1", "Leak!")):
                with patch("gui.messagebox.showerror"):
                    inst.verify_killswitch(manual=False, sync=True)
                    inst.stop.assert_called()

        # When Throne/TUN is active on host, proxy must NOT be stopped!
        inst.running = True
        inst.stop = MagicMock()
        with patch("vless2socks.ipcheck.is_system_tun_active", return_value=True):
            with patch("vless2socks.ipcheck.check_ip_leak", return_value=(True, "62.60.234.188", "62.60.234.188", "Leak!")):
                inst.verify_killswitch(manual=False, sync=True)
                inst.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
