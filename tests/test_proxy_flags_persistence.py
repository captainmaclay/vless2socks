"""Unit and integration tests for System_Proxy and Work_Proxy flags persistence,
UI methods, AppConfig parsing, and encrypted .hbak backup/restore cycle.
"""

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
from vless2socks.config import load_config


class MockApp:
    def __init__(self):
        self.instances = []
        self.saved_data = None
        self.overview_refreshed = False
        self.tabs_refreshed = False

    def save_all(self):
        self.saved_data = [inst.get_config() for inst in self.instances]

    def refresh_overview(self):
        self.overview_refreshed = True

    def refresh_current_page_tabs(self):
        self.tabs_refreshed = True

    def sort_instances(self):
        pass


class TestProxyFlagsPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_flags_test_"))
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

        self.password = "StrongMasterPass_12345!"
        self.app = MockApp()

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

    def test_default_flags_assignment(self):
        """Instances on 1015 default to System_Proxy=True, and 1030/WorProxy to Work_Proxy=True."""
        inst_1015 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015", "name": "System Proxy"}, 0)
        self.assertTrue(inst_1015.is_system_proxy())
        self.assertFalse(inst_1015.is_work_proxy())

        inst_1030 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1030", "name": "WorProxy"}, 1)
        self.assertFalse(inst_1030.is_system_proxy())
        self.assertTrue(inst_1030.is_work_proxy())

        inst_other = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1081", "name": "Other"}, 2)
        self.assertFalse(inst_other.is_system_proxy())
        self.assertFalse(inst_other.is_work_proxy())

    def test_dynamic_flags_toggling_and_config(self):
        """Toggling flags dynamically saves to cfg and get_config() reflects both case styles."""
        inst = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1082", "name": "CustomNode"}, 0)
        self.app.instances = [inst]

        # Enable both
        inst.set_system_proxy(True)
        inst.set_work_proxy(True)

        self.assertTrue(inst.is_system_proxy())
        self.assertTrue(inst.is_work_proxy())

        cfg = inst.get_config()
        self.assertTrue(cfg["system_proxy"])
        self.assertTrue(cfg["System_Proxy"])
        self.assertTrue(cfg["work_proxy"])
        self.assertTrue(cfg["Work_Proxy"])

        # Disable System_Proxy
        inst.set_system_proxy(False)
        self.assertFalse(inst.is_system_proxy())
        self.assertTrue(inst.is_work_proxy())

        cfg2 = inst.get_config()
        self.assertFalse(cfg2["system_proxy"])
        self.assertFalse(cfg2["System_Proxy"])
        self.assertTrue(cfg2["work_proxy"])
        self.assertTrue(cfg2["Work_Proxy"])

    def test_load_config_parsing(self):
        """AppConfig properly extracts system_proxy and work_proxy flags."""
        cfg_file = self.tmp_dir / "single_cfg.json"
        cfg_file.write_text(json.dumps({
            "url": "vless://00000000-0000-0000-0000-000000000000@example.com:443?security=tls",
            "listen": "127.0.0.1:1085",
            "System_Proxy": True,
            "Work_Proxy": True,
        }), encoding="utf-8")

        app_cfg = load_config(str(cfg_file))
        self.assertTrue(app_cfg.system_proxy)
        self.assertTrue(app_cfg.System_Proxy)
        self.assertTrue(app_cfg.work_proxy)
        self.assertTrue(app_cfg.Work_Proxy)

    def test_backup_and_restore_preserves_proxy_flags(self):
        """Exporting encrypted .hbak and restoring recovers System_Proxy and Work_Proxy flags intact."""
        instances_data = [
            {
                "name": "Corporate Gateway",
                "order": 0,
                "url": "",
                "listen": "127.0.0.1:1015",
                "killswitch": True,
                "system_proxy": True,
                "System_Proxy": True,
                "work_proxy": False,
                "Work_Proxy": False,
            },
            {
                "name": "WorProxy Office",
                "order": 1,
                "url": "wireguard://office.example.com:51820/?pk=ABC#WorProxy",
                "listen": "127.0.0.1:1030",
                "killswitch": True,
                "system_proxy": False,
                "System_Proxy": False,
                "work_proxy": True,
                "Work_Proxy": True,
            },
            {
                "name": "Hybrid Tunnel",
                "order": 2,
                "url": "vless://00000000-0000-0000-0000-000000000000@fi.example.com:443#FI",
                "listen": "127.0.0.1:1081",
                "killswitch": False,
                "system_proxy": True,
                "System_Proxy": True,
                "work_proxy": True,
                "Work_Proxy": True,
            },
            {
                "name": "Regular Proxy",
                "order": 3,
                "url": "vless://00000000-0000-0000-0000-000000000000@de.example.com:443#DE",
                "listen": "127.0.0.1:1082",
                "killswitch": False,
                "system_proxy": False,
                "System_Proxy": False,
                "work_proxy": False,
                "Work_Proxy": False,
            },
        ]

        with open(self.test_instances, "w", encoding="utf-8") as f:
            json.dump(instances_data, f, indent=2)

        settings_data = {
            "language": "ru",
            "system_proxy": "127.0.0.1:1015",
            "work_proxy": "127.0.0.1:1030",
            "System_Proxy": True,
            "Work_Proxy": True,
        }
        with open(self.test_settings, "w", encoding="utf-8") as f:
            json.dump(settings_data, f, indent=2)

        # 1. Export encrypted backup
        hbak_path = backup_manager.export_encrypted_backup(self.password, self.tmp_dir)
        self.assertTrue(hbak_path.exists())

        # 2. Modify / wipe local files
        self.test_instances.unlink()
        self.test_settings.unlink()

        # 3. Restore from backup
        count = backup_manager.restore_encrypted_backup(hbak_path, self.password)
        self.assertEqual(count, 4)

        # 4. Verify restored data
        self.assertTrue(self.test_instances.exists())
        with open(self.test_instances, "r", encoding="utf-8") as f:
            restored = json.load(f)

        self.assertEqual(len(restored), 4)

        # Check instance #0 (Corporate Gateway)
        sp = restored[0]
        self.assertEqual(sp["name"], "Corporate Gateway")
        self.assertEqual(sp["listen"], "127.0.0.1:1015")
        self.assertTrue(sp["system_proxy"])
        self.assertTrue(sp["System_Proxy"])
        self.assertFalse(sp["work_proxy"])
        self.assertFalse(sp["Work_Proxy"])

        # Check instance #1 (WorProxy Office)
        wp = restored[1]
        self.assertEqual(wp["name"], "WorProxy Office")
        self.assertEqual(wp["listen"], "127.0.0.1:1030")
        self.assertFalse(wp["system_proxy"])
        self.assertFalse(wp["System_Proxy"])
        self.assertTrue(wp["work_proxy"])
        self.assertTrue(wp["Work_Proxy"])

        # Check instance #2 (Hybrid Tunnel)
        hb = restored[2]
        self.assertEqual(hb["name"], "Hybrid Tunnel")
        self.assertTrue(hb["system_proxy"])
        self.assertTrue(hb["System_Proxy"])
        self.assertTrue(hb["work_proxy"])
        self.assertTrue(hb["Work_Proxy"])

        # Check instance #3 (Regular Proxy)
        rp = restored[3]
        self.assertEqual(rp["name"], "Regular Proxy")
        self.assertFalse(rp["system_proxy"])
        self.assertFalse(rp["System_Proxy"])
        self.assertFalse(rp["work_proxy"])
        self.assertFalse(rp["Work_Proxy"])

        # Check settings
        self.assertTrue(self.test_settings.exists())
        with open(self.test_settings, "r", encoding="utf-8") as f:
            restored_settings = json.load(f)
        self.assertEqual(restored_settings.get("system_proxy"), "127.0.0.1:1015")
        self.assertEqual(restored_settings.get("work_proxy"), "127.0.0.1:1030")
        self.assertTrue(restored_settings.get("System_Proxy"))
        self.assertTrue(restored_settings.get("Work_Proxy"))

    def test_wipe_all_sensitive_data_resets_default_flags(self):
        """Factory reset / wipe creates default instance on port 1015 with System_Proxy=True."""
        backup_manager.wipe_all_sensitive_data()
        self.assertTrue(self.test_instances.exists())
        with open(self.test_instances, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["listen"], "127.0.0.1:1015")
        self.assertTrue(data[0]["system_proxy"])
        self.assertTrue(data[0]["System_Proxy"])
        self.assertFalse(data[0]["work_proxy"])
        self.assertFalse(data[0]["Work_Proxy"])


if __name__ == "__main__":
    unittest.main()
