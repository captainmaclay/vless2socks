"""Proxy configs (.instance_<id>.json) live in instances/, not in the app root;
files left in the root by older versions are moved there on start."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backup_manager
from vless2socks import paths


class InstancesDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v2s_inst_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_config_path_is_inside_instances(self):
        p = paths.instance_config_path(7, self.tmp)
        self.assertEqual(p, self.tmp / "instances" / ".instance_7.json")
        self.assertTrue(p.parent.is_dir())
        self.assertEqual(paths.instance_config_path(3).parent, paths.APP_DIR / "instances")

    def test_root_configs_are_migrated(self):
        for i in range(3):
            (self.tmp / f".instance_{i}.json").write_text(f'{{"n": {i}}}', encoding="utf-8")
        (self.tmp / "instances.json").write_text("[]", encoding="utf-8")
        self.assertEqual(paths.migrate_instance_configs(self.tmp), 3)
        self.assertEqual(list(self.tmp.glob(".instance_*.json")), [])
        moved = sorted(p.name for p in (self.tmp / "instances").glob(".instance_*.json"))
        self.assertEqual(moved, [".instance_0.json", ".instance_1.json", ".instance_2.json"])
        self.assertEqual((self.tmp / "instances" / ".instance_1.json").read_text(encoding="utf-8"), '{"n": 1}')
        self.assertTrue((self.tmp / "instances.json").exists())
        self.assertEqual(paths.migrate_instance_configs(self.tmp), 0)

    def test_gui_instance_uses_instances_dir(self):
        import gui
        from unittest.mock import MagicMock
        saved = gui.ROOT_DIR
        gui.ROOT_DIR = self.tmp
        try:
            inst = gui.ProxyInstance(MagicMock(), {"url": "", "listen": "127.0.0.1:39999"}, 42)
        finally:
            gui.ROOT_DIR = saved
        self.assertEqual(inst.config_path, self.tmp / "instances" / ".instance_42.json")

    def test_wipe_clears_both_locations(self):
        saved = {n: getattr(backup_manager, n) for n in ("ROOT_DIR", "INSTANCES_FILE", "CONFIG_FILE", "ENV_FILE", "SETTINGS_FILE")}
        backup_manager.ROOT_DIR = self.tmp
        backup_manager.INSTANCES_FILE = self.tmp / "instances.json"
        backup_manager.CONFIG_FILE = self.tmp / "config.json"
        backup_manager.ENV_FILE = self.tmp / ".env"
        backup_manager.SETTINGS_FILE = self.tmp / "settings.json"
        import settings_manager
        saved_sm = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"
        try:
            (self.tmp / ".instance_1.json").write_text("{}", encoding="utf-8")
            paths.instance_config_path(2, self.tmp).write_text("{}", encoding="utf-8")
            backup_manager.wipe_all_sensitive_data()
            self.assertEqual(list(self.tmp.rglob(".instance_*.json")), [])
        finally:
            for n, v in saved.items():
                setattr(backup_manager, n, v)
            settings_manager.SETTINGS_FILE = saved_sm


if __name__ == "__main__":
    unittest.main()
