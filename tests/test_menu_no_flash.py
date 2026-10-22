"""Slide-down menus (RestartServices ▼ and Watcher ▾) appear right under their button as a
thin strip and grow from there. Before, measuring the content showed the whole menu for a
moment at the window manager's default spot (top-left) before it jumped into place.
Also: Overview cards have no per-proxy geo (🔍) button any more.

Runs in the UI sandbox: no proxy is started, tray / IPC are off."""

import ctypes
import sys
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


class MenuPlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        s = float(cls.app.winfo_fpixels("1i")) / 96.0
        cls.app.geometry(f"{int(1280 * s)}x{int(800 * s)}+40+40")
        cls.sb.settle(20)
        cls.maps = []

        def on_map(event):
            w = event.widget
            if isinstance(w, tk.Toplevel):
                cls.maps.append((w, w.winfo_rootx(), w.winfo_rooty(), w.winfo_height()))
        cls.app.bind_class("Toplevel", "<Map>", on_map, add="+")

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        self.app.close_watcher_menu()
        self.app.close_restart_services_menu()
        self.sb.settle(5)
        self.maps.clear()

    def first_map(self, win):
        found = [m for m in self.maps if m[0] is win]
        self.assertTrue(found, "the menu was never shown")
        return found[0]

    def test_restart_menu_first_appears_under_its_button(self):
        self.app.open_restart_services_menu()
        win = self.app.restart_menu_win
        self.sb.settle(30)
        _, x, y, h = self.first_map(win)
        btn = self.app.btn_restart_services
        expected_y = btn.winfo_rooty() + btn.winfo_height() + 3
        self.assertLessEqual(abs(y - expected_y), 2, f"first shown at y={y}, expected {expected_y}")
        arrow = self.app.btn_restart_arrow
        self.assertLessEqual(x + win.winfo_width(), arrow.winfo_rootx() + arrow.winfo_width() + 2)
        self.assertLess(h, win.winfo_reqheight() // 2, "first shown at full height: no slide")
        self.assertGreaterEqual(win.winfo_height(), win.winfo_reqheight() - 2)   # slid open
        self.app.close_restart_services_menu()

    def test_watcher_menu_first_appears_under_its_button(self):
        self.app.open_watcher_menu()
        win = self.app.watcher_menu_win
        self.sb.settle(30)
        _, x, y, h = self.first_map(win)
        btn = self.app.watcher_btn
        self.assertLessEqual(abs(x - btn.winfo_rootx()), 2, f"first shown at x={x}")
        self.assertGreater(y, btn.winfo_rooty() + btn.winfo_height() - 1)
        self.assertLess(h, win.winfo_reqheight() // 2, "first shown at full height: no slide")
        self.assertGreaterEqual(win.winfo_height(), win.winfo_reqheight() - 2)
        self.app.close_watcher_menu()

    def _shown_before_placed(self, opener, attr):
        """Was the menu window already on screen when it first got its position?"""
        log = []

        class Recorder(tk.Toplevel):
            def geometry(self, newGeometry=None):
                if newGeometry and "+" in str(newGeometry) and not log:
                    log.append(bool(self.winfo_ismapped()))
                return super().geometry(newGeometry)

        from unittest import mock
        with mock.patch.object(tk, "Toplevel", Recorder):
            opener()
        self.sb.settle(20)
        self.assertIsInstance(getattr(self.app, attr), Recorder)
        self.assertEqual(len(log), 1, "the menu never got a position")
        return log[0]

    def test_restart_menu_is_hidden_until_placed(self):
        self.assertFalse(self._shown_before_placed(self.app.open_restart_services_menu, "restart_menu_win"))
        self.app.close_restart_services_menu()

    def test_watcher_menu_is_hidden_until_placed(self):
        self.assertFalse(self._shown_before_placed(self.app.open_watcher_menu, "watcher_menu_win"))
        self.app.close_watcher_menu()

    def test_reopening_does_not_flash_either(self):
        for _ in range(2):
            self.maps.clear()
            self.app.open_watcher_menu()
            win = self.app.watcher_menu_win
            self.sb.settle(20)
            _, x, _, _ = self.first_map(win)
            self.assertLessEqual(abs(x - self.app.watcher_btn.winfo_rootx()), 2)
            self.app.close_watcher_menu()
            self.sb.settle(5)


class NoGeoButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        cls.app.geometry("1400x900+40+40")
        cls.sb.settle(20)

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def test_cards_have_no_geo_button_but_keep_the_others(self):
        self.app.refresh_overview()
        self.app.update_idletasks()
        view = self.app.overview_view
        self.assertEqual(view.buttons("geo", "__any__"), [])
        cards = len(self.app.instances)
        for name in ("toggle", "groups", "view"):
            with self.subTest(button=name):
                self.assertEqual(len(view.buttons(name, "__any__")), cards)
        self.assertNotIn("🔍", " ".join(view.texts()))

    def test_refresh_all_geo_is_still_there(self):
        self.assertTrue(self.app.refresh_geo_btn.winfo_exists())


if __name__ == "__main__":
    unittest.main()
