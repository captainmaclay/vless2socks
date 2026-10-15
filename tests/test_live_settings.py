"""Telegram port and language: applied at once, kept in backups, applied after a restore."""

import gc
import shutil
import socket
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backup_manager
import gui
import i18n
import settings_manager
from vless2socks import telegram_proxy
from vless2socks.telegram_proxy import TelegramProxyDispatcher
from tests.test_proxy_groups import _GroupsTestApp


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class _OptionsApp(_GroupsTestApp):
    """after() from worker threads is run by the GUI thread, as in VlessApp."""

    def __init__(self):
        import queue
        self._calls = queue.SimpleQueue()
        super().__init__()
        self._drain()

    def after(self, ms, func=None, *args):
        if func is not None and gui.threading.current_thread() is not gui.threading.main_thread():
            self._calls.put((func, args))
            return None
        return tk.Tk.after(self, ms, func, *args)

    def _drain(self):
        while not self._calls.empty():
            func, args = self._calls.get_nowait()
            func(*args)
        self._drain_id = tk.Tk.after(self, 20, self._drain)

    def destroy(self):
        try:
            self.after_cancel(self._drain_id)
        except Exception:
            pass
        super().destroy()


for _n in ("_build_options_tab", "apply_telegram_port", "_save_start_port", "_on_options_auto_reconnect_toggled",
           "_on_force_restart_toggled", "open_launchers_guide_modal", "run_quick_launcher_generation",
           "_build_localization_tab", "_apply_language"):
    setattr(_OptionsApp, _n, getattr(gui.VlessApp, _n))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_live_"))
        self.orig_settings = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"
        self.orig_lang = i18n.get_current_language()

    def tearDown(self):
        i18n._CURRENT_LANGUAGE = self.orig_lang
        settings_manager.SETTINGS_FILE = self.orig_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pump(self, app, cond, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.update()
            if cond():
                return True
            time.sleep(0.02)
        return cond()


class TestTelegramPortLive(_Base):
    def setUp(self):
        super().setUp()
        self.disp = TelegramProxyDispatcher(listen_port=_free_port())
        self.disp.start()
        self.assertTrue(self.disp.is_running())
        self.patch = mock.patch.object(telegram_proxy, "get_tg_dispatcher", return_value=self.disp)
        self.patch.start()
        settings_manager.set_telegram_proxy_port(self.disp.listen_port)
        self.app = _OptionsApp()
        self.app.options_frame = tk.Frame(self.app)
        self.app.options_frame.pack()
        self.app._build_options_tab()
        self.app.deiconify()
        self.app.update()

    def tearDown(self):
        self.patch.stop()
        self.disp.stop()
        self.app.destroy()
        self.app = None
        gc.collect()
        super().tearDown()

    def _type(self, text):
        e = self.app.tg_port_entry
        e.delete(0, tk.END)
        e.focus_force()
        self.app.update()
        for ch in text:
            e.insert(tk.END, ch)
            e.event_generate("<KeyRelease>", keysym=ch)
            self.app.update()

    def test_typing_applies_once_after_a_pause_not_per_key(self):
        new = _free_port()
        old = self.disp.listen_port
        with mock.patch.object(self.disp, "set_listen_port", wraps=self.disp.set_listen_port) as moved:
            self._type(str(new))
            self.app.update()
            moved.assert_not_called()                 # nothing while typing
            self.assertTrue(self.pump(self.app, lambda: moved.called and self.disp.listen_port == new))
        self.assertEqual(moved.call_count, 1)
        self.assertEqual(moved.call_args.args[0], new)  # never 9, 99, 991...
        self.assertTrue(self.pump(self.app, lambda: _listening(new)))
        self.assertFalse(_listening(old))
        self.assertEqual(settings_manager.get_telegram_proxy_port(), new)
        # the background move reports back: "applied", not stuck on "applying"
        self.assertTrue(self.pump(self.app, lambda: self.app.tg_port_status.cget("text") == gui.t("tg_port_applied", port=new)),
                        self.app.tg_port_status.cget("text"))

    def test_enter_applies_at_once_without_blocking(self):
        new = _free_port()
        self._type(str(new))
        started = time.monotonic()
        self.app.tg_port_entry.event_generate("<Return>")
        self.assertLess(time.monotonic() - started, 0.5)   # the listener moves in the background
        self.assertTrue(self.pump(self.app, lambda: _listening(new)))
        self.assertEqual(settings_manager.get_telegram_proxy_port(), new)
        self.assertTrue(self.pump(self.app, lambda: self.app.tg_port_status.cget("text") == gui.t("tg_port_applied", port=new)))
        # the copied link follows the new port immediately
        self.assertEqual(telegram_proxy.build_tg_socks_url(), f"tg://socks?server=127.0.0.1&port={new}")

    def test_rejects_invalid_and_busy_ports(self):
        before = settings_manager.get_telegram_proxy_port()
        for bad in ("abc", "80", "70000", ""):
            self.assertFalse(self.app.apply_telegram_port(bad))
            self.assertEqual(self.app.tg_port_status.cget("fg"), gui.C["red"])
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            busy_port = busy.getsockname()[1]
            self.assertFalse(self.app.apply_telegram_port(busy_port))
            self.assertIn(str(busy_port), self.app.tg_port_status.cget("text"))
        self.assertEqual(settings_manager.get_telegram_proxy_port(), before)
        self.assertTrue(_listening(self.disp.listen_port))   # the running dispatcher was not touched

    def test_reset_goes_to_9910(self):
        with mock.patch.object(self.app, "apply_telegram_port") as apply:
            reset = next(w for w in self._walk(self.app.options_frame)
                         if isinstance(w, tk.Button) and w.cget("text") == gui.t("btn_reset_tg_port"))
            reset.invoke()
        apply.assert_called_once_with(9910)
        self.assertEqual(self.app.tg_port_entry.get(), "9910")

    @staticmethod
    def _walk(w):
        yield w
        for c in w.winfo_children():
            yield from TestTelegramPortLive._walk(c)


class TestTelegramPortBackup(_Base):
    def setUp(self):
        super().setUp()
        self.saved = {n: getattr(backup_manager, n) for n in ("ROOT_DIR", "SETTINGS_FILE", "INSTANCES_FILE", "CONFIG_FILE", "ENV_FILE")}
        backup_manager.ROOT_DIR = self.tmp
        backup_manager.SETTINGS_FILE = self.tmp / "settings.json"
        backup_manager.INSTANCES_FILE = self.tmp / "instances.json"
        backup_manager.CONFIG_FILE = self.tmp / "config.json"
        backup_manager.ENV_FILE = self.tmp / ".env"
        (self.tmp / "instances.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(backup_manager, n, v)
        super().tearDown()

    def _backup(self):
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        return backup_manager.export_encrypted_backup("Pass_123456!", out)

    def test_port_is_in_backup_and_restored(self):
        settings_manager.set_telegram_proxy_port(2345)
        hbak = self._backup()
        settings_manager.set_telegram_proxy_port(5432)
        backup_manager.restore_encrypted_backup(hbak, "Pass_123456!")
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 2345)

    def test_restored_old_port_is_not_migrated_away(self):
        settings_manager.set_setting("telegram_proxy_port", 1373)      # an old backup: 1373, no flag
        hbak = self._backup()
        settings_manager.set_telegram_proxy_port(9910)
        settings_manager.set_setting("telegram_proxy_port_migrated", True)
        backup_manager.restore_encrypted_backup(hbak, "Pass_123456!")

        app = mock.MagicMock()
        app.backup_pwd_entry.get.return_value = "Pass_123456!"
        with mock.patch.object(gui.messagebox, "showinfo"), \
                mock.patch.object(gui.backup_manager, "restore_encrypted_backup", return_value=1):
            gui.VlessApp._restore_specific_backup(app, str(hbak))
        app.apply_telegram_port.assert_called_once_with(1373)      # applied to the running dispatcher now
        self.assertFalse(settings_manager.migrate_legacy_telegram_port())
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 1373)

    def test_restore_applies_the_backups_language(self):
        i18n._CURRENT_LANGUAGE = "en"
        settings_manager.set_setting("language", "ru")              # what the restored file says
        app = mock.MagicMock()
        app.backup_pwd_entry.get.return_value = "x"
        app.after_idle.side_effect = lambda fn: fn()
        with mock.patch.object(gui.messagebox, "showinfo"), \
                mock.patch.object(gui.backup_manager, "restore_encrypted_backup", return_value=1):
            gui.VlessApp._restore_specific_backup(app, "x.hbak")
        app._apply_language.assert_called_once_with("ru")


class TestInstantLanguage(_Base):
    def setUp(self):
        super().setUp()
        i18n._CURRENT_LANGUAGE = "en"
        settings_manager.set_setting("language", "en")
        self.app = _OptionsApp()
        self.app.loc_frame = tk.Frame(self.app)
        self.app.loc_frame.pack()
        self.app._build_localization_tab()

    def tearDown(self):
        self.app.destroy()
        self.app = None
        gc.collect()
        super().tearDown()

    def test_no_apply_button_and_pick_switches_at_once(self):
        buttons = [w.cget("text") for w in self.app.loc_frame.winfo_children() if isinstance(w, tk.Button)]
        self.assertEqual(buttons, [])
        radios = [w for w in TestTelegramPortLive._walk(self.app.loc_frame) if isinstance(w, tk.Radiobutton)]
        ru = next(r for r in radios if r.cget("value") == "ru")
        with mock.patch.object(_OptionsApp, "_apply_language") as apply:
            ru.invoke()
            apply.assert_not_called()           # deferred: the rebuild replaces this very button
            self.app.update_idletasks()
            apply.assert_called_once()

    def test_apply_rebuilds_only_opened_tabs(self):
        app = mock.MagicMock()
        app.lang_var.get.return_value = "ru"
        frames = {n: tk.Frame(self.app) for n in ("ov", "px", "op", "bk", "ws", "lc")}
        for f in frames.values():
            tk.Label(f, text="old").pack()
        app.overview_frame, app.proxies_frame, app.options_frame = frames["ov"], frames["px"], frames["op"]
        app.backup_frame, app.wsl_iso_frame, app.loc_frame = frames["bk"], frames["ws"], frames["lc"]
        app._pending_tab_builders = [app._build_proxies_tab, app._build_backup_tab]   # never opened
        app._run_tab_builder = lambda b: gui.VlessApp._run_tab_builder(app, b)
        gui.VlessApp._apply_language(app)
        self.assertEqual(i18n.get_current_language(), "ru")
        self.assertEqual(settings_manager.get_setting("language"), "ru")
        for opened in ("_build_overview_tab", "_build_options_tab", "_build_wsl_isolation_tab", "_build_localization_tab"):
            getattr(app, opened).assert_called_once()
        app._build_proxies_tab.assert_not_called()
        app._build_backup_tab.assert_not_called()
        self.assertEqual(frames["ov"].winfo_children(), [])            # old widgets of opened tabs removed
        self.assertEqual(len(frames["px"].winfo_children()), 1)        # unopened tab untouched
        self.assertEqual(app.nav_notebook.tab.call_count, 6)           # every tab title translated

    def test_same_language_is_a_no_op(self):
        app = mock.MagicMock()
        app.lang_var.get.return_value = "en"
        gui.VlessApp._apply_language(app)
        app._build_overview_tab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
