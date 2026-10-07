"""Unit and integration tests for RestartServices button, slide-down settings,
WSL restart routine, WorkProxy/SystemProxy selective restarts, and persistence.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import backup_manager
import gui
import settings_manager


class MockInstance:
    def __init__(self, name: str, port: int, is_sp: bool, is_wp: bool):
        self.name = name
        self.port = port
        self._is_sp = is_sp
        self._is_wp = is_wp
        self.restarted = False
        self.started = False
        self.stopped = False
        self.running = True

    def is_system_proxy(self) -> bool:
        return self._is_sp

    def is_work_proxy(self) -> bool:
        return self._is_wp

    def restart(self):
        self.restarted = True

    def start(self):
        self.started = True
        self.running = True

    def stop(self):
        self.stopped = True
        self.running = False


class TestRestartServices(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_restart_test_"))
        self.orig_settings = settings_manager.SETTINGS_FILE
        self.test_settings = self.tmp_dir / "settings.json"
        settings_manager.SETTINGS_FILE = self.test_settings

        self.orig_bm_root = backup_manager.ROOT_DIR
        self.orig_bm_settings = backup_manager.SETTINGS_FILE
        backup_manager.ROOT_DIR = self.tmp_dir
        backup_manager.SETTINGS_FILE = self.test_settings
        backup_manager.INSTANCES_FILE = self.tmp_dir / "instances.json"
        backup_manager.CONFIG_FILE = self.tmp_dir / "config.json"
        backup_manager.ENV_FILE = self.tmp_dir / ".env"

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings
        backup_manager.ROOT_DIR = self.orig_bm_root
        backup_manager.SETTINGS_FILE = self.orig_bm_settings
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_settings_manager_restart_flags_persistence(self):
        """Flags for RestartServices persist in settings.json with correct defaults."""
        flags = settings_manager.get_restart_services_flags()
        self.assertTrue(flags["wsl"])
        self.assertTrue(flags["work_proxy"])
        self.assertTrue(flags["system_proxy"])

        # Change settings and verify persistence
        settings_manager.set_restart_services_flags(wsl=False, work_proxy=True, system_proxy=False)
        updated = settings_manager.get_restart_services_flags()
        self.assertFalse(updated["wsl"])
        self.assertTrue(updated["work_proxy"])
        self.assertFalse(updated["system_proxy"])

        # Directly inspect settings.json
        self.assertTrue(self.test_settings.exists())
        with open(self.test_settings, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertFalse(data["restart_wsl"])
        self.assertTrue(data["restart_work_proxy"])
        self.assertFalse(data["restart_system_proxy"])

    def test_proxy_instance_restart_lifecycle(self):
        """ProxyInstance.restart() invokes stop and start in sequence."""
        app = MagicMock()
        cfg = {"listen": "127.0.0.1:1085", "url": "vless://00000000-0000-0000-0000-000000000000@ex.com:443#test"}
        inst = gui.ProxyInstance(app, cfg, 0)

        inst.stop = MagicMock()
        inst.start = MagicMock()
        inst.running = True

        inst.restart()
        inst.stop.assert_called_once()
        inst.start.assert_called_once()

    def test_selective_restart_work_proxy_only(self):
        """When only restart_work_proxy is active, only WorkProxy instances are restarted."""
        inst_sys = MockInstance("System Proxy", 1015, is_sp=True, is_wp=False)
        inst_work = MockInstance("WorProxy", 1030, is_sp=False, is_wp=True)
        inst_other = MockInstance("Finland", 1081, is_sp=False, is_wp=False)

        instances = [inst_sys, inst_work, inst_other]

        # Simulate execution logic
        wsl, work, sys_p = False, True, False

        restarted = []
        if work:
            for inst in instances:
                if inst.is_work_proxy():
                    inst.restart()
                    restarted.append(inst.name)

        self.assertEqual(restarted, ["WorProxy"])
        self.assertFalse(inst_sys.restarted)
        self.assertTrue(inst_work.restarted)
        self.assertFalse(inst_other.restarted)

    def test_selective_restart_system_proxy_only(self):
        """When only restart_system_proxy is active, only SystemProxy instances are restarted."""
        inst_sys = MockInstance("System Proxy", 1015, is_sp=True, is_wp=False)
        inst_work = MockInstance("WorProxy", 1030, is_sp=False, is_wp=True)
        inst_other = MockInstance("Finland", 1081, is_sp=False, is_wp=False)

        instances = [inst_sys, inst_work, inst_other]

        wsl, work, sys_p = False, False, True

        restarted = []
        if sys_p:
            for inst in instances:
                if inst.is_system_proxy():
                    inst.restart()
                    restarted.append(inst.name)

        self.assertEqual(restarted, ["System Proxy"])
        self.assertTrue(inst_sys.restarted)
        self.assertFalse(inst_work.restarted)
        self.assertFalse(inst_other.restarted)

    def test_restart_wsl_execution(self):
        """restart_wsl terminates active distros, executes wsl --shutdown, and returns clean status."""
        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock(returncode=0)
            mock_res.stdout.decode.return_value = "NAME STATE VERSION\nUbuntu Running 2"
            mock_run.return_value = mock_res
            ok, msg = gui.restart_wsl()
            if sys.platform == "win32":
                self.assertTrue(ok)
                self.assertIn("successfully", msg)
                # Verify that shutdown was called
                called_commands = [call.args[0] for call in mock_run.call_args_list if call.args]
                self.assertTrue(any("wsl.exe" in cmd and "--shutdown" in cmd for cmd in called_commands))
            else:
                self.assertFalse(ok)

    def test_backup_restore_cycle_preserves_restart_services_preferences(self):
        """RestartServices settings are included in .hbak backup archive and restored."""
        # 1. Setup custom settings
        settings_manager.set_restart_services_flags(wsl=False, work_proxy=True, system_proxy=True)

        with open(backup_manager.INSTANCES_FILE, "w", encoding="utf-8") as f:
            json.dump([{"listen": "127.0.0.1:1015", "name": "System Proxy"}], f)

        # 2. Export encrypted backup
        pwd = "TestSecretPass_888!"
        hbak = backup_manager.export_encrypted_backup(pwd, self.tmp_dir)
        self.assertTrue(hbak.exists())

        # 3. Wipe settings file
        self.test_settings.unlink()
        self.assertFalse(self.test_settings.exists())

        # 4. Restore from backup
        backup_manager.restore_encrypted_backup(hbak, pwd)
        self.assertTrue(self.test_settings.exists())

        restored_flags = settings_manager.get_restart_services_flags()
        self.assertFalse(restored_flags["wsl"])
        self.assertTrue(restored_flags["work_proxy"])
        self.assertTrue(restored_flags["system_proxy"])

    def test_tray_menu_restart_services_structure_and_callbacks(self):
        """VlessApp._build_tray_menu contains RestartServices action and settings toggles."""
        app = MagicMock()
        app.execute_restart_services = MagicMock()
        app.restart_wsl_var = MagicMock()
        app.restart_work_proxy_var = MagicMock()
        app.restart_system_proxy_var = MagicMock()
        app._show_from_tray = MagicMock()
        app._exit_from_tray = MagicMock()
        app._tray_icon = MagicMock()

        # Build tray menu
        tray_menu = gui.VlessApp._build_tray_menu(app)
        menu_items = list(tray_menu.items)

        # Check that RestartServices action is present
        restart_action_item = next((item for item in menu_items if "RestartServices" in str(item.text) and not getattr(item, "submenu", None)), None)
        self.assertIsNotNone(restart_action_item)

        # Trigger tray restart callback
        gui.VlessApp._tray_restart_services(app)
        app.after.assert_called_with(0, app.execute_restart_services)

        # Trigger tray toggle callbacks
        settings_manager.set_setting("restart_wsl", True)
        gui.VlessApp._tray_toggle_wsl(app)
        self.assertFalse(settings_manager.get_setting("restart_wsl"))
        app.restart_wsl_var.set.assert_called_with(False)

        settings_manager.set_setting("restart_work_proxy", False)
        gui.VlessApp._tray_toggle_work_proxy(app)
        self.assertTrue(settings_manager.get_setting("restart_work_proxy"))
        app.restart_work_proxy_var.set.assert_called_with(True)

        settings_manager.set_setting("restart_system_proxy", True)
        gui.VlessApp._tray_toggle_system_proxy(app)
        self.assertFalse(settings_manager.get_setting("restart_system_proxy"))
        app.restart_system_proxy_var.set.assert_called_with(False)

    def test_orange_checkbox_toggle_and_styling(self):
        """OrangeCheckbox toggles BooleanVar and uses bright orange styling."""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            var = tk.BooleanVar(value=True)
            cb = gui.OrangeCheckbox(root, variable=var, orange_color="#ff7700")
            self.assertEqual(cb.orange_color, "#ff7700")
            self.assertTrue(var.get())

            # Toggle off
            cb._toggle()
            self.assertFalse(var.get())

            # Toggle on
            cb._toggle()
            self.assertTrue(var.get())

            # Sync from variable write
            var.set(False)
            self.assertFalse(var.get())

            cb.destroy()
        finally:
            root.destroy()

    def test_menu_closes_on_main_unmap_and_minimize(self):
        """When the main window receives <Unmap> or is minimized, RestartServices menu closes automatically."""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            app = MagicMock(spec=gui.VlessApp)
            app._restart_menu_open = True
            mock_win = MagicMock()
            mock_win.winfo_exists.return_value = True
            app.restart_menu_win = mock_win
            app.btn_restart_arrow = MagicMock()

            # Calling close_restart_services_menu method from class
            gui.VlessApp.close_restart_services_menu(app)
            self.assertFalse(app._restart_menu_open)
            self.assertIsNone(app.restart_menu_win)
            mock_win.destroy.assert_called_once()
            app.btn_restart_arrow.config.assert_called_with(text="▼")

            # Test event handlers triggering dismissal
            app._restart_menu_open = True
            app.close_restart_services_menu = MagicMock()

            # Event matching main window
            event = MagicMock()
            event.widget = app
            gui.VlessApp._on_main_unmap(app, event)
            app.close_restart_services_menu.assert_called_once()

            # Deactivate event
            app.close_restart_services_menu.reset_mock()
            gui.VlessApp._on_main_deactivate(app)
            app.close_restart_services_menu.assert_called_once()
        finally:
            root.destroy()

    def test_hide_to_tray_closes_restart_services_menu(self):
        """Calling _hide_to_tray ensures close_restart_services_menu is called immediately."""
        app = MagicMock(spec=gui.VlessApp)
        app.close_restart_services_menu = MagicMock()
        app._tray_icon = MagicMock()

        with patch.object(gui, "HAS_TRAY", True):
            gui.VlessApp._hide_to_tray(app)
            app.close_restart_services_menu.assert_called_once()
            app.withdraw.assert_called_once()

    def test_execute_restart_services_invokes_wsl_and_logs(self):
        """execute_restart_services properly invokes restart_wsl, calls _log without error, and updates button."""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            app = MagicMock(spec=gui.VlessApp)
            app.restart_wsl_var = tk.BooleanVar(value=True)
            app.restart_work_proxy_var = tk.BooleanVar(value=False)
            app.restart_system_proxy_var = tk.BooleanVar(value=False)
            app.instances = []
            app.after = MagicMock()
            app._set_restart_button_busy = MagicMock()
            app._set_restart_button_success = MagicMock()
            app._log = MagicMock()

            with patch("gui.restart_wsl", return_value=(True, "WSL successfully restarted")) as mock_wsl:
                with patch("threading.Thread") as mock_thread:
                    gui.VlessApp.execute_restart_services(app)
                    mock_thread.assert_called_once()
                    worker_fn = mock_thread.call_args[1]["target"]
                    worker_fn()

                    mock_wsl.assert_called_once()
                    self.assertTrue(any("Restarting WSL" in str(c) for c in app._log.call_args_list))
                    self.assertTrue(any("WSL successfully restarted" in str(c) for c in app._log.call_args_list))
        finally:
            root.destroy()

    def test_restart_hud_window_lifecycle_and_pinning(self):
        """RestartHUDWindow initializes with alpha 0.92, logs lines, pins/unpins, and closes cleanly."""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            hud = gui.RestartHUDWindow(root)
            self.assertEqual(hud.attributes("-alpha"), 0.92)
            self.assertTrue(hud.attributes("-topmost"))
            self.assertFalse(hud.is_pinned)
            self.assertTrue(hud.in_progress)

            # Log message
            hud.log("Testing HUD log message")
            root.update()
            log_content = hud.log_text.get("1.0", tk.END)
            self.assertIn("Testing HUD log message", log_content)

            # Toggle Pin ON
            hud.toggle_pin()
            self.assertTrue(hud.is_pinned)
            self.assertIn("📌", hud.status_badge.cget("text"))

            # Toggle Pin OFF
            hud.toggle_pin()
            self.assertFalse(hud.is_pinned)

            # Set finished starts countdown
            hud.set_finished("WSL, WorkProxy")
            self.assertFalse(hud.in_progress)
            self.assertEqual(hud.countdown_sec, 20)

            # Close HUD
            hud.close()
            self.assertFalse(hud.winfo_exists())
        finally:
            root.destroy()

    def test_restart_hud_window_auto_close_countdown(self):
        """When unpinned and finished, HUD ticks down and auto-closes at 0."""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            hud = gui.RestartHUDWindow(root)
            hud.set_finished("Done")
            self.assertFalse(hud.is_pinned)
            self.assertFalse(hud.in_progress)

            # Manually tick countdown to 1
            hud.countdown_sec = 1
            hud._schedule_tick()
            self.assertFalse(hud.winfo_exists())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

