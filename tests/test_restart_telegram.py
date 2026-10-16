"""TelegramProxy in RestartServices: the menu row shows the live dispatcher port and
upstream (no hard-coded ports, no network on open), a restart keeps a working upstream.

The real dispatcher is never started: port 9910 belongs to the user's running app."""

import ctypes
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import settings_manager
from vless2socks import telegram_proxy
from vless2socks.telegram_proxy import TelegramProxyDispatcher


def _offline_dispatcher(port=9910):
    """A dispatcher that never binds a port."""
    disp = TelegramProxyDispatcher(listen_port=port)
    disp.start = mock.MagicMock(side_effect=lambda: setattr(disp, "_running", True))
    disp.stop = mock.MagicMock(side_effect=lambda: setattr(disp, "_running", False))
    disp.is_running = lambda: disp._running
    disp._running = False
    return disp


class TestDispatcherPool(unittest.TestCase):
    def test_entries_without_a_valid_port_are_skipped_not_1081(self):
        disp = _offline_dispatcher()
        disp.set_pool([{"name": "a", "telegram_proxy": True},
                       {"name": "b", "listen": "127.0.0.1:abc", "telegram_proxy": True},
                       {"name": "c", "listen": "127.0.0.1:31085", "telegram_proxy": True}])
        self.assertEqual([(p["host"], p["port"]) for p in disp.get_pool()], [("127.0.0.1", 31085)])

    def test_peek_active_does_not_probe_or_rotate(self):
        disp = _offline_dispatcher()
        disp.set_pool([{"name": f"p{p}", "listen": f"127.0.0.1:{p}", "telegram_proxy": True} for p in (1, 2, 3)])
        disp._current_index = 2
        with mock.patch.object(telegram_proxy, "is_endpoint_reachable", side_effect=AssertionError("network")):
            self.assertEqual(disp.peek_active()["port"], 3)
        self.assertEqual(disp._current_index, 2)
        self.assertIsNone(_offline_dispatcher().peek_active())


class TestRestartServicesTelegram(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        cls.app.geometry("+0+0")
        cls.sb.settle(10)

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        self.disp = _offline_dispatcher()
        self._p = mock.patch.object(telegram_proxy, "get_tg_dispatcher", return_value=self.disp)
        self._p.start()
        self._net = mock.patch.object(telegram_proxy, "is_endpoint_reachable", return_value=True)
        self._net.start()
        settings_manager.set_telegram_proxy_port(9910)
        self.tg = [i for i in self.app.instances if i.is_telegram_proxy()]
        self.assertGreaterEqual(len(self.tg), 3)        # sandbox data: #4, #5, #6 are TelegramProxy

    def tearDown(self):
        if self.app._restart_menu_open:
            self.app.close_restart_services_menu()
        self._net.stop()
        self._p.stop()
        settings_manager.set_telegram_proxy_port(9910)

    def menu_label(self):
        self.app.open_restart_services_menu()
        self.sb.settle(10)
        return self.app._restart_menu_tg_label

    def test_row_shows_settings_port_and_current_upstream(self):
        settings_manager.set_telegram_proxy_port(9555)
        self.disp.set_pool(self.app.instances)
        self.disp._current_index = 2                    # the dispatcher is on the third one
        lbl = self.menu_label()
        third = self.tg[2].get_listen()
        self.assertEqual(lbl.cget("text"), f"✈️  TelegramProxy :9555 → {third[0]}:{third[1]} (+2)")

    def test_row_without_flagged_proxies(self):
        flags = [(i, i.cfg.get("telegram_proxy"), i.cfg.get("TelegramProxy")) for i in self.tg]
        try:
            for i in self.tg:
                i.cfg["telegram_proxy"] = i.cfg["TelegramProxy"] = False
            self.assertEqual(self.menu_label().cget("text"), "✈️  TelegramProxy :9910 (не выбрано)")
        finally:
            for i, a, b in flags:
                i.cfg["telegram_proxy"], i.cfg["TelegramProxy"] = a, b

    def test_opening_the_menu_touches_no_network(self):
        self.disp.set_pool(self.app.instances)
        with mock.patch.object(telegram_proxy, "is_endpoint_reachable", side_effect=AssertionError("network")):
            self.menu_label()                           # used to call get_active_proxy (probes, rotation)

    def test_row_follows_port_and_upstream_changes_while_open(self):
        self.disp.set_pool(self.app.instances)
        lbl = self.menu_label()
        before = lbl.cget("text")
        settings_manager.set_telegram_proxy_port(9777)
        self.disp._current_index = 1
        self.app._refresh_restart_menu_telegram()
        second = self.tg[1].get_listen()
        self.assertNotEqual(lbl.cget("text"), before)
        self.assertEqual(lbl.cget("text"), f"✈️  TelegramProxy :9777 → {second[0]}:{second[1]} (+2)")

    def test_menu_is_wide_enough_for_every_row(self):
        lbl = self.menu_label()
        win = self.app.restart_menu_win
        self.sb.settle(20)                               # slide-down animation
        right = win.winfo_rootx() + win.winfo_width()
        self.assertLessEqual(lbl.winfo_rootx() + lbl.winfo_reqwidth(), right)
        self.assertGreaterEqual(win.winfo_height(), win.winfo_reqheight() - 2)

    def test_sync_keeps_a_working_upstream_and_applies_the_port(self):
        for i in self.tg:
            i.running, i.healthy = True, True
        try:
            self.disp.set_pool(self.app.instances)
            self.disp._current_index = 1
            settings_manager.set_telegram_proxy_port(9666)
            with mock.patch.object(self.disp, "rotate_to_next") as rotate:
                active = self.app._sync_telegram_dispatcher()
            rotate.assert_not_called()
            self.assertEqual(active["port"], int(self.tg[1].get_listen()[1]))
            self.assertEqual(self.disp.listen_port, 9666)
            self.disp.start.assert_called()
        finally:
            for i in self.tg:
                i.running, i.healthy = False, False

    def test_sync_moves_off_a_dead_upstream(self):
        self.disp.set_pool(self.app.instances)
        self.disp._current_index = 0
        dead = int(self.tg[0].get_listen()[1])
        with mock.patch.object(telegram_proxy, "is_endpoint_reachable", side_effect=lambda h, p, **k: p != dead):
            active = self.app._sync_telegram_dispatcher()
        self.assertNotEqual(active["port"], dead)

    def test_restart_services_telegram_does_not_force_rotation(self):
        logs = []
        done = threading.Event()

        def log(msg):
            logs.append(msg)
            if "Finished restarting" in msg:
                done.set()

        settings_manager.set_telegram_proxy_port(9444)
        self.disp.set_pool(self.app.instances)
        self.disp._current_index = 2
        with mock.patch.object(self.app, "_log", side_effect=log), \
                mock.patch.object(self.disp, "rotate_to_next") as rotate:
            self.app.execute_restart_services(only="telegram")
            deadline = time.monotonic() + 15
            while not done.is_set() and time.monotonic() < deadline:
                self.app.update()
                time.sleep(0.02)
        self.assertTrue(done.is_set(), logs)
        rotate.assert_not_called()
        third = self.tg[2].get_listen()
        self.assertIn(f"✈️ [RestartServices] TelegramProxy :9444 → {third[0]}:{third[1]}", logs)
        self.assertEqual(self.disp.listen_port, 9444)
        restarted = {p for kind, p in self.sb.start_attempts if kind == "restart_async"}
        self.assertEqual(restarted, {i.get_listen()[1] for i in self.tg})   # recorded, never launched
        self.assertEqual(self.sb.kill_attempts, [])


if __name__ == "__main__":
    unittest.main()
