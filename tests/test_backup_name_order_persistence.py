"""Integration tests for encrypted backup export/import preserving proxy names, orders, and 1015 killswitch."""

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

import backup_manager
import gui
import settings_manager


class TestBackupNameOrderPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_backup_test_"))
        self.orig_root = backup_manager.ROOT_DIR
        self.orig_instances = gui.INSTANCES_FILE
        self.orig_settings = gui.SETTINGS_FILE
        self.orig_config = gui.CONFIG_FILE
        self.orig_env = backup_manager.ENV_FILE

        backup_manager.ROOT_DIR = self.tmp_dir
        self.test_instances = self.tmp_dir / "instances.json"
        self.test_settings = self.tmp_dir / "settings.json"
        self.test_config = self.tmp_dir / "config.json"
        self.test_env = self.tmp_dir / ".env"

        gui.INSTANCES_FILE = self.test_instances
        gui.SETTINGS_FILE = self.test_settings
        gui.CONFIG_FILE = self.test_config

        backup_manager.INSTANCES_FILE = self.test_instances
        backup_manager.SETTINGS_FILE = self.test_settings
        backup_manager.CONFIG_FILE = self.test_config
        backup_manager.ENV_FILE = self.test_env

        self.password = "StrongMasterPass_999!"

    def tearDown(self):
        backup_manager.ROOT_DIR = self.orig_root
        gui.INSTANCES_FILE = self.orig_instances
        gui.SETTINGS_FILE = self.orig_settings
        gui.CONFIG_FILE = self.orig_config
        backup_manager.INSTANCES_FILE = self.orig_instances
        backup_manager.SETTINGS_FILE = self.orig_settings
        backup_manager.CONFIG_FILE = self.orig_config
        backup_manager.ENV_FILE = self.orig_env
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_backup_and_restore_preserves_custom_names_and_orders(self):
        """Exporting to .hbak and restoring preserves names, float orders, and killswitch."""
        instances_data = [
            {
                "name": "System Proxy",
                "order": 0,
                "url": "",
                "listen": "127.0.0.1:1015",
                "killswitch": True,
            },
            {
                "name": "VIP Finland",
                "order": 1.25,
                "url": "vless://00000000-0000-0000-0000-000000000000@fi.example.com:443#FI",
                "listen": "127.0.0.1:1081",
                "killswitch": False,
            },
            {
                "name": "Fallback Germany",
                "order": 2,
                "url": "vless://00000000-0000-0000-0000-000000000000@de.example.com:443#DE",
                "listen": "127.0.0.1:1082",
                "killswitch": True,
            },
        ]

        with open(self.test_instances, "w", encoding="utf-8") as f:
            json.dump(instances_data, f, indent=2)

        with open(self.test_settings, "w", encoding="utf-8") as f:
            json.dump({"language": "en", "auto_reconnect": True}, f)

        # 1. Export encrypted backup
        hbak_path = backup_manager.export_encrypted_backup(self.password, self.tmp_dir)
        self.assertTrue(hbak_path.exists())

        # 2. Modify / wipe local files
        self.test_instances.unlink()
        self.test_settings.unlink()

        # 3. Restore from backup
        count = backup_manager.restore_encrypted_backup(hbak_path, self.password)
        self.assertEqual(count, 3)

        # 4. Verify restored data
        self.assertTrue(self.test_instances.exists())
        with open(self.test_instances, "r", encoding="utf-8") as f:
            restored = json.load(f)

        self.assertEqual(len(restored), 3)

        # Check instance #0 (System Proxy)
        sp = restored[0]
        self.assertEqual(sp["name"], "System Proxy")
        self.assertEqual(sp["order"], 0)
        self.assertEqual(sp["listen"], "127.0.0.1:1015")
        self.assertTrue(sp["killswitch"])

        # Check instance #1 (VIP Finland with float order 1.25)
        fi = restored[1]
        self.assertEqual(fi["name"], "VIP Finland")
        self.assertEqual(fi["order"], 1.25)
        self.assertEqual(fi["listen"], "127.0.0.1:1081")
        self.assertFalse(fi["killswitch"])

        # Check instance #2 (Fallback Germany)
        de = restored[2]
        self.assertEqual(de["name"], "Fallback Germany")
        self.assertEqual(de["order"], 2)
        self.assertEqual(de["listen"], "127.0.0.1:1082")
        self.assertTrue(de["killswitch"])


if __name__ == "__main__":
    unittest.main()
