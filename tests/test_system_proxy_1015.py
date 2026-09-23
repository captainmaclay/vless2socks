"""Unit tests for System Proxy on port 1015 (#0) and Killswitch defaults."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import gui
from vless2socks.config import AppConfig, load_config
import backup_manager


class TestSystemProxy1015(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_test_1015_"))
        self.orig_instances = gui.INSTANCES_FILE
        self.test_instances = self.tmp_dir / "instances.json"
        gui.INSTANCES_FILE = self.test_instances
        backup_manager.INSTANCES_FILE = self.test_instances
        backup_manager.CONFIG_FILE = self.tmp_dir / "config.json"

    def tearDown(self):
        gui.INSTANCES_FILE = self.orig_instances
        backup_manager.INSTANCES_FILE = self.orig_instances
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ensure_system_proxy_prepends_1015(self):
        """When instances list does not contain 1015, ensure_system_proxy_1015 prepends it at index 0."""
        existing = [
            {"listen": "127.0.0.1:1081", "url": "vless://test1", "killswitch": False},
            {"listen": "127.0.0.1:1082", "url": "vless://test2", "killswitch": False},
        ]
        result = gui.ensure_system_proxy_1015(existing)
        self.assertEqual(len(result), 3)
        sp = result[0]
        self.assertEqual(sp["listen"], "127.0.0.1:1015")
        self.assertEqual(sp["name"], "System Proxy")
        self.assertEqual(sp["order"], 0)
        self.assertTrue(sp["killswitch"])
        self.assertEqual(sp["url"], "")

    def test_load_instances_creates_system_proxy_when_empty(self):
        """When instances.json is missing or empty, load_instances returns System Proxy :1015."""
        if self.test_instances.exists():
            self.test_instances.unlink()
        instances = gui.load_instances()
        self.assertGreaterEqual(len(instances), 1)
        sp = instances[0]
        self.assertIn("1015", sp["listen"])
        self.assertEqual(sp["name"], "System Proxy")
        self.assertEqual(sp["order"], 0)
        self.assertTrue(sp["killswitch"])

    def test_load_config_defaults_killswitch_true_for_1015(self):
        """AppConfig and load_config set killswitch=True for port 1015 by default."""
        cfg_file = self.tmp_dir / "test_cfg.json"
        cfg_file.write_text(json.dumps({
            "url": "vless://00000000-0000-0000-0000-000000000000@example.com:443?security=tls",
            "listen": "127.0.0.1:1015",
        }), encoding="utf-8")

        app_cfg = load_config(str(cfg_file))
        self.assertEqual(app_cfg.listen_port, 1015)
        self.assertTrue(app_cfg.killswitch)
        self.assertEqual(app_cfg.name, "System Proxy")
        self.assertEqual(app_cfg.order, 0.0)

    def test_load_config_preserves_explicit_killswitch_false_for_1015(self):
        """If user explicitly configured killswitch: false for 1015, respect it."""
        cfg_file = self.tmp_dir / "test_cfg2.json"
        cfg_file.write_text(json.dumps({
            "url": "vless://00000000-0000-0000-0000-000000000000@example.com:443?security=tls",
            "listen": "127.0.0.1:1015",
            "killswitch": False,
        }), encoding="utf-8")

        app_cfg = load_config(str(cfg_file))
        self.assertEqual(app_cfg.listen_port, 1015)
        self.assertFalse(app_cfg.killswitch)

    def test_wipe_all_sensitive_data_resets_to_system_proxy_1015(self):
        """Factory reset / wipe creates default instance on port 1015 with killswitch=True."""
        backup_manager.wipe_all_sensitive_data()
        self.assertTrue(self.test_instances.exists())
        with open(self.test_instances, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["listen"], "127.0.0.1:1015")
        self.assertEqual(data[0]["name"], "System Proxy")
        self.assertEqual(data[0]["order"], 0)
        self.assertTrue(data[0]["killswitch"])


if __name__ == "__main__":
    unittest.main()
