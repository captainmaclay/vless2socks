"""Startup flags: "Launch with Windows" (launch_at_startup) and "Start in tray"
(start_minimized_tray).

- both live in settings.json with the other flags and travel through .hbak backups;
- launch_at_startup is mirrored to HKCU\\...\\Run (a fake winreg here: the real registry
  is never touched);
- both are checkable items of the tray context menu, in sync with the Options checkboxes;
- a restore brings the checkboxes and the Run entry in line with the backup.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autostart
import backup_manager
import settings_manager


class FakeWinreg:
    """Just enough of winreg for autostart.py, backed by a dict."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[str, str] = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):
        return self._Key()

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        return self._Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class _TempSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v2s_startup_"))
        self.settings_file = self.tmp / "settings.json"
        self._saved_sm = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.settings_file
        self._saved_bm = {n: getattr(backup_manager, n)
                          for n in ("ROOT_DIR", "SETTINGS_FILE", "INSTANCES_FILE", "CONFIG_FILE", "ENV_FILE")}
        backup_manager.ROOT_DIR = self.tmp
        backup_manager.SETTINGS_FILE = self.settings_file
        backup_manager.INSTANCES_FILE = self.tmp / "instances.json"
        backup_manager.CONFIG_FILE = self.tmp / "config.json"
        backup_manager.ENV_FILE = self.tmp / ".env"
        self.reg = FakeWinreg()
        patcher = patch.object(autostart, "winreg", self.reg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self._saved_sm
        for n, v in self._saved_bm.items():
            setattr(backup_manager, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class AutostartModuleTest(_TempSettings):
    def test_defaults_are_off(self):
        self.assertIs(settings_manager.DEFAULT_SETTINGS["launch_at_startup"], False)
        self.assertIs(settings_manager.DEFAULT_SETTINGS["start_minimized_tray"], False)
        self.assertFalse(settings_manager.get_setting("launch_at_startup"))

    def test_set_enabled_writes_setting_and_run_entry(self):
        self.assertTrue(autostart.set_enabled(True))
        self.assertTrue(settings_manager.get_setting("launch_at_startup"))
        self.assertEqual(self.reg.values[autostart.VALUE_NAME], autostart.startup_command())
        self.assertTrue(autostart.is_registered())

        self.assertTrue(autostart.set_enabled(False))
        self.assertFalse(settings_manager.get_setting("launch_at_startup"))
        self.assertNotIn(autostart.VALUE_NAME, self.reg.values)
        # removing a missing entry is fine
        self.assertTrue(autostart.set_enabled(False))

    def test_startup_command_points_at_gui(self):
        cmd = autostart.startup_command()
        self.assertTrue(cmd.startswith('"'))
        self.assertIn("gui.py", cmd)
        self.assertIn("pythonw", cmd.lower())

    def test_sync_follows_the_setting(self):
        settings_manager.set_setting("launch_at_startup", True)
        autostart.sync_from_settings()
        self.assertEqual(autostart.read_registered_command(), autostart.startup_command())

        # a stale path (app folder moved) is rewritten
        self.reg.values[autostart.VALUE_NAME] = '"C:\\old\\pythonw.exe" "C:\\old\\gui.py"'
        autostart.sync_from_settings()
        self.assertEqual(autostart.read_registered_command(), autostart.startup_command())

        settings_manager.set_setting("launch_at_startup", False)
        autostart.sync_from_settings()
        self.assertIsNone(autostart.read_registered_command())

    def test_unsupported_platform_is_harmless(self):
        with patch.object(autostart, "winreg", None):
            self.assertFalse(autostart.is_supported())
            self.assertFalse(autostart.apply(True))
            self.assertIsNone(autostart.read_registered_command())


class BackupRoundTripTest(_TempSettings):
    def test_both_flags_survive_backup_and_restore(self):
        settings_manager.set_setting("launch_at_startup", True)
        settings_manager.set_setting("start_minimized_tray", True)
        backup_manager.INSTANCES_FILE.write_text(json.dumps([{"listen": "127.0.0.1:1015"}]), encoding="utf-8")
        hbak = backup_manager.export_encrypted_backup("pwd_startup_1!", self.tmp / "bak")

        settings_manager.set_setting("launch_at_startup", False)
        settings_manager.set_setting("start_minimized_tray", False)
        backup_manager.restore_encrypted_backup(hbak, "pwd_startup_1!")

        self.assertTrue(settings_manager.get_setting("launch_at_startup"))
        self.assertTrue(settings_manager.get_setting("start_minimized_tray"))
        autostart.sync_from_settings()
        self.assertTrue(autostart.is_registered())


class TrayMenuTest(_TempSettings):
    def _app(self):
        import gui
        app = MagicMock()
        app.after = lambda ms, fn: fn()
        app._set_launch_at_startup = lambda v: gui.VlessApp._set_launch_at_startup(app, v)
        app._set_start_minimized_tray = lambda v: gui.VlessApp._set_start_minimized_tray(app, v)
        return gui, app

    def test_menu_has_checkable_startup_items(self):
        import i18n
        gui, app = self._app()
        menu = gui.VlessApp._build_tray_menu(app)
        texts = {str(item.text): item for item in menu.items}
        launch = next(i for txt, i in texts.items() if i18n.t("tray_launch_at_startup") in txt)
        tray = next(i for txt, i in texts.items() if i18n.t("tray_start_minimized") in txt)

        self.assertFalse(launch.checked)
        self.assertFalse(tray.checked)
        settings_manager.set_setting("launch_at_startup", True)
        settings_manager.set_setting("start_minimized_tray", True)
        self.assertTrue(launch.checked)
        self.assertTrue(tray.checked)

    def test_tray_toggles_update_settings_registry_and_checkboxes(self):
        gui, app = self._app()
        gui.VlessApp._tray_toggle_launch_at_startup(app)
        self.assertTrue(settings_manager.get_setting("launch_at_startup"))
        self.assertTrue(autostart.is_registered())
        app.launch_at_startup_var.set.assert_called_with(True)

        gui.VlessApp._tray_toggle_launch_at_startup(app)
        self.assertFalse(settings_manager.get_setting("launch_at_startup"))
        self.assertFalse(autostart.is_registered())
        app.launch_at_startup_var.set.assert_called_with(False)

        gui.VlessApp._tray_toggle_start_minimized(app)
        self.assertTrue(settings_manager.get_setting("start_minimized_tray"))
        app.start_tray_var.set.assert_called_with(True)
        gui.VlessApp._tray_toggle_start_minimized(app)
        self.assertFalse(settings_manager.get_setting("start_minimized_tray"))
        app.start_tray_var.set.assert_called_with(False)
        self.assertGreaterEqual(app._tray_refresh_menu.call_count, 4)


class OptionsTabGuiTest(unittest.TestCase):
    """Real VlessApp in the UI sandbox (registry calls are recorded, not executed)."""

    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru", count=3)
        cls.app = cls.sb.app
        cls.launch_calls = list(cls.sb.autostart_calls)
        cls.app._ensure_tab_built(cls.app.options_frame)
        cls.sb.settle(5)

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def test_startup_sync_ran_on_launch(self):
        self.assertIn(("sync",), self.launch_calls)

    def test_checkboxes_exist_and_persist(self):
        import i18n
        self.assertTrue(hasattr(self.app, "launch_at_startup_var"))
        self.assertTrue(hasattr(self.app, "start_tray_var"))
        labels = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    labels.append(str(c.cget("text")))
                except Exception:
                    pass
                walk(c)
        walk(self.app.options_frame)
        self.assertTrue(any(i18n.t("opt_launch_at_startup_title") in s for s in labels))
        self.assertTrue(any(i18n.t("opt_start_minimized_tray_title") in s for s in labels))

        self.sb.autostart_calls.clear()
        self.app.launch_at_startup_var.set(True)
        self.app._set_launch_at_startup(True)
        self.assertTrue(settings_manager.get_setting("launch_at_startup"))
        self.assertIn(("apply", True), self.sb.autostart_calls)
        self.app._set_launch_at_startup(False)
        self.assertFalse(settings_manager.get_setting("launch_at_startup"))
        self.assertFalse(self.app.launch_at_startup_var.get())

        self.app._set_start_minimized_tray(True)
        self.assertTrue(settings_manager.get_setting("start_minimized_tray"))
        self.assertTrue(self.app.start_tray_var.get())
        self.app._set_start_minimized_tray(False)
        self.assertFalse(self.app.start_tray_var.get())


if __name__ == "__main__":
    unittest.main()
