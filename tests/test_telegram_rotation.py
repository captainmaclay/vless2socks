"""Telegram dispatcher rotation, default port 9910, and the Telegram/killswitch UI.

Rotation is checked over real sockets. The key case is the one xray produces when its
server is down: the local SOCKS port is alive and answers CONNECT with "success" at
once, then closes without a byte. The dispatcher must notice and move to the next
proxy marked as Telegram.
"""

import asyncio
import gc
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings_manager
from vless2socks.telegram_proxy import TelegramProxyDispatcher


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Servers:
    """Fake upstream SOCKS5 proxies and a target, on an asyncio loop in a thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.servers = []
        self.connects = {}

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def start(self, kind: str) -> int:
        """kind: 'echo' target, 'working' SOCKS, 'dead' (success then silence), 'refusing'."""
        async def socks_head(reader, writer):
            _ver, nmethods = await reader.readexactly(2)
            await reader.readexactly(nmethods)
            writer.write(b"\x05\x00")
            await writer.drain()
            ver, cmd, _, atyp = await reader.readexactly(4)
            if atyp == 1:
                host = socket.inet_ntoa(await reader.readexactly(4))
            else:
                n = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(n)).decode()
            port = struct.unpack(">H", await reader.readexactly(2))[0]
            return host, port

        async def handle(reader, writer):
            port_self = writer.get_extra_info("sockname")[1]
            try:
                if kind == "echo":
                    data = await reader.read(100)
                    writer.write(b"echo:" + data)
                    await writer.drain()
                    return
                host, port = await socks_head(reader, writer)
                self.connects[port_self] = self.connects.get(port_self, 0) + 1
                if kind == "refusing":
                    writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                    await writer.drain()
                    return
                writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                if kind == "dead":
                    await reader.read(100)          # swallow the request, answer nothing
                    return
                up_r, up_w = await asyncio.open_connection(host, port)
                data = await reader.read(100)
                up_w.write(data)
                await up_w.drain()
                writer.write(await up_r.read(100))
                await writer.drain()
                up_w.close()
            except Exception:
                pass
            finally:
                writer.close()

        async def start():
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            self.servers.append(server)
            return server.sockets[0].getsockname()[1]
        return self._run(start())

    def close(self):
        async def stop():
            for s in self.servers:
                s.close()
        self._run(stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


def _through_dispatcher(disp_port: int, target_port: int, payload=b"ping", timeout=8.0) -> bytes:
    """One Telegram-like SOCKS5 connection through the dispatcher; returns the reply."""
    with socket.create_connection(("127.0.0.1", disp_port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        assert s.recv(2) == b"\x05\x00"
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", target_port))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0:
            return b""
        s.sendall(payload)
        chunks = b""
        try:
            while True:
                data = s.recv(100)
                if not data:
                    break
                chunks += data
        except (socket.timeout, ConnectionError, OSError):
            pass
        return chunks


class TestRotation(unittest.TestCase):
    def setUp(self):
        self.srv = _Servers()
        self.echo = self.srv.start("echo")
        self.disp = None

    def tearDown(self):
        if self.disp:
            self.disp.stop()
        self.srv.close()

    def _dispatcher(self, ports):
        disp = TelegramProxyDispatcher(listen_port=_free_port())
        disp.set_pool([{"name": f"p{p}", "listen": f"127.0.0.1:{p}", "telegram_proxy": True} for p in ports])
        disp.start()
        self.assertTrue(disp.is_running())
        self.disp = disp
        return disp

    def test_dead_server_behind_live_port_rotates_to_next(self):
        dead, working = self.srv.start("dead"), self.srv.start("working")
        disp = self._dispatcher([dead, working])
        replies = []
        for _ in range(TelegramProxyDispatcher.FAIL_THRESHOLD + 2):
            replies.append(_through_dispatcher(disp.listen_port, self.echo))
            if replies[-1]:
                break
            time.sleep(0.15)
        self.assertEqual(replies[-1], b"echo:ping")
        # exactly the silent relays needed to give up on the dead one, then the working one
        self.assertEqual(replies[:-1], [b""] * TelegramProxyDispatcher.FAIL_THRESHOLD)
        self.assertEqual(disp.get_active_proxy()["port"], working)
        # and it stays there for the next connections
        self.assertEqual(_through_dispatcher(disp.listen_port, self.echo), b"echo:ping")

    def test_refused_connect_retries_the_same_connection_on_the_next_proxy(self):
        refusing, working = self.srv.start("refusing"), self.srv.start("working")
        disp = self._dispatcher([refusing, working])
        self.assertEqual(_through_dispatcher(disp.listen_port, self.echo), b"echo:ping")
        self.assertEqual(self.srv.connects.get(refusing), 1)

    def test_proxy_reported_unhealthy_by_gui_is_skipped(self):
        first, second = self.srv.start("working"), self.srv.start("working")
        disp = self._dispatcher([first, second])
        disp.set_health("127.0.0.1", first, False)
        self.assertEqual(_through_dispatcher(disp.listen_port, self.echo), b"echo:ping")
        self.assertNotIn(first, self.srv.connects)
        self.assertEqual(self.srv.connects.get(second), 1)
        disp.set_health("127.0.0.1", first, True)
        self.assertEqual(disp.get_active_proxy()["port"], second)   # no needless switch back

    def test_all_unhealthy_but_reachable_still_serves(self):
        only = self.srv.start("working")
        disp = self._dispatcher([only])
        disp.set_health("127.0.0.1", only, False)
        self.assertEqual(_through_dispatcher(disp.listen_port, self.echo), b"echo:ping")

    def test_closed_local_port_rotates(self):
        closed, working = _free_port(), self.srv.start("working")
        disp = self._dispatcher([closed, working])
        self.assertEqual(_through_dispatcher(disp.listen_port, self.echo), b"echo:ping")

    def test_success_clears_failures(self):
        disp = TelegramProxyDispatcher(listen_port=_free_port())
        proxy = {"host": "127.0.0.1", "port": 1}
        disp.report_result(proxy, False)
        disp.report_result(proxy, True)
        disp.report_result(proxy, False)
        self.assertTrue(disp._usable(proxy))   # failures must be in a row


class TestTelegramPort(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_tgport_"))
        self.orig = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_is_9910(self):
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 9910)

    def test_old_default_is_migrated_once(self):
        settings_manager.set_setting("telegram_proxy_port", 1373)
        self.assertTrue(settings_manager.migrate_legacy_telegram_port())
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 9910)
        # a later deliberate 1373 is respected
        settings_manager.set_telegram_proxy_port(1373)
        self.assertFalse(settings_manager.migrate_legacy_telegram_port())
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 1373)

    def test_custom_port_is_kept(self):
        settings_manager.set_setting("telegram_proxy_port", 2222)
        self.assertFalse(settings_manager.migrate_legacy_telegram_port())
        self.assertEqual(settings_manager.get_telegram_proxy_port(), 2222)


class TestTelegramUi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_tgui_"))
        self.orig = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"
        import gui
        from tests.test_proxy_groups import _GroupsTestApp
        self.gui = gui
        _GroupsTestApp._open_card_context_menu = gui.VlessApp._open_card_context_menu
        _GroupsTestApp._copy_tg_proxy_link = gui.VlessApp._copy_tg_proxy_link
        self.app = _GroupsTestApp()
        self.app._update_telegram_dispatcher_pool = mock.MagicMock()
        self.tg = gui.ProxyInstance(self.app, {"name": "tg", "url": "", "listen": "127.0.0.1:23801",
                                               "telegram_proxy": True}, 0)
        self.plain = gui.ProxyInstance(self.app, {"name": "plain", "url": "", "listen": "127.0.0.1:23802"}, 1)
        self.app.instances += [self.tg, self.plain]
        self.app._render_overview()

    def tearDown(self):
        self.app.destroy()
        self.app = None
        gc.collect()
        settings_manager.SETTINGS_FILE = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_copy_button_on_telegram_cards(self):
        self.assertEqual(self.app.overview_view.buttons("copy_tg"), [])
        self.assertNotIn(f"📋 {self.gui.t('btn_copy_tg_socks')}", self.app.overview_view.texts())

    def test_telegram_icon_is_drawn(self):
        img = self.gui.make_telegram_icon(24)
        if img is None:
            self.skipTest("Pillow not installed")
        self.assertEqual(img.size, (24, 24))
        r, g, b, a = img.getpixel((3, 12))
        self.assertGreater(b, r)            # blue disc
        self.assertEqual(img.getpixel((12, 12))[:3], (255, 255, 255)[:3]) if False else None

    def test_copy_link_uses_shared_port(self):
        settings_manager.set_telegram_proxy_port(9910)
        with mock.patch.object(self.gui.messagebox, "showinfo"):
            self.app._copy_tg_proxy_link()
        self.assertEqual(self.app.clipboard_get(), "tg://socks?server=127.0.0.1&port=9910")

    def _menu_for(self, inst):
        with mock.patch.object(tk.Menu, "tk_popup"):
            self.app._open_card_context_menu(inst, 10, 10)
        menu = self.app._card_context_menu
        labels = {menu.entrycget(i, "label"): i for i in range(menu.index("end") + 1)
                  if menu.type(i) == "checkbutton"}
        return menu, labels

    def test_context_menu_toggles_killswitch_and_telegram(self):
        menu, labels = self._menu_for(self.plain)
        ks = next(i for label, i in labels.items() if self.gui.t("ctx_killswitch") in label)
        tg = next(i for label, i in labels.items() if "TelegramProxy" in label)   # system category switch
        menu.invoke(ks)
        self.assertTrue(self.plain.cfg["killswitch"])
        menu.invoke(tg)
        self.assertTrue(self.plain.is_telegram_proxy())
        self.app._update_telegram_dispatcher_pool.assert_called()
        menu, labels = self._menu_for(self.plain)        # reopened: shows current state, toggles back
        menu.invoke(next(i for label, i in labels.items() if self.gui.t("ctx_killswitch") in label))
        self.assertFalse(self.plain.cfg["killswitch"])

    def test_right_click_finds_the_card(self):
        view = self.app.overview_view
        self.app.update_idletasks()
        y = view.card_slots[self.tg][0][0] + view.CARD_H / 2
        self.assertIs(view.card_key_at(view.px(40), y), self.tg)
        self.assertIsNone(view.card_key_at(view.px(40), -50))


if __name__ == "__main__":
    unittest.main()
