"""Subscriptions: decoding, port allocation, start-port setting (incl. backup/restore),
the "Add subscription" wizard, and an end-to-end check that an imported proxy works.

The end-to-end test serves a real subscription over HTTP, imports it through the
wizard, starts the generated config with main.py exactly as the GUI does, and sends
an HTTP request through the resulting SOCKS5 port to a fake VLESS server.
Nothing here touches the real settings.json / instances.json or ports below 20000.
"""

import asyncio
import base64
import gc
import http.server
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backup_manager
import gui
import proxy_groups
import settings_manager
import subscriptions
from tests.fake_vless_server import FakeVlessServer
from tests.helpers import TinyHttpServer, socks5_connect
from tests.test_proxy_groups import _GroupsTestApp, _walk

TEST_START_PORT = 21080  # far away from the real proxies (1015, 1020, 1030, 1080+)


def _vless(host="127.0.0.1", port=443, remark="node", uid=None):
    return f"vless://{uid or uuid.uuid4()}@{host}:{port}?type=tcp&security=none#{remark}"


def _release_tk(test):
    """Free a test's Tk root on the main thread. Left to the cyclic GC, it could be
    collected inside some worker thread, and Tcl aborts the process when an
    interpreter is deleted outside the thread that created it."""
    test.app = None
    gc.collect()


class _TempSettingsMixin:
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_subs_test_"))
        self.orig_settings = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp_dir / "settings.json"

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestDecodeAndExtract(unittest.TestCase):
    def test_base64_plain_and_urlsafe(self):
        links = [_vless(remark="a"), _vless(remark="b")]
        text = "\n".join(links)
        std = base64.b64encode(text.encode()).decode()
        self.assertEqual(subscriptions.decode_subscription(std.encode()), links)
        # url-safe alphabet, no padding, wrapped lines
        urlsafe = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
        wrapped = "\n".join(urlsafe[i:i + 60] for i in range(0, len(urlsafe), 60))
        self.assertEqual(subscriptions.decode_subscription(wrapped), links)
        # plain text with CRLF and a BOM
        self.assertEqual(subscriptions.decode_subscription("﻿" + "\r\n".join(links) + "\r\n"), links)

    def test_garbage_is_rejected(self):
        with self.assertRaises(subscriptions.SubscriptionError) as ctx:
            subscriptions.decode_subscription(b"<html>not found</html>")
        self.assertEqual(ctx.exception.code, "bad_format")

    def test_extract_filters_and_dedupes(self):
        uid = str(uuid.uuid4())
        known = _vless(port=1111, remark="old", uid=uid)
        lines = [
            _vless(port=2222, remark="new1"),
            "vmess://abc",
            "trojan://x@y:1",
            "vless://no-port-here@host",
            _vless(port=1111, remark="renamed", uid=uid),   # same link as `known`, other remark
            _vless(port=3333, remark="new2"),
        ]
        lines.append(lines[0])  # repeated inside the subscription
        parsed = subscriptions.extract_vless_links(lines, existing_urls=[known])
        self.assertEqual([subscriptions.link_name(u) for u in parsed.links], ["new1", "new2"])
        self.assertEqual((parsed.duplicates, parsed.unsupported, parsed.invalid), (2, 2, 1))


class TestPortsAndConfig(_TempSettingsMixin, unittest.TestCase):
    def test_allocate_ascending_skipping_used_and_busy(self):
        busy = {1083, 11084}
        ports = subscriptions.allocate_ports(4, 1080, used_ports={1080, 1082}, is_free=lambda p: p not in busy)
        # 1080/1082 used, 1083 busy, 1084 has its HTTP port 11084 busy
        self.assertEqual(ports, [1081, 1085, 1086, 1087])

    def test_allocate_avoids_http_ports_of_existing_proxies(self):
        # an existing proxy on 1081 owns 11081 as HTTP; a proxy listening on 11080 makes 1080's HTTP clash
        ports = subscriptions.allocate_ports(2, 1080, used_ports={1081, 11082}, is_free=lambda p: True)
        self.assertEqual(ports, [1080, 1083])

    def test_allocate_raises_when_short(self):
        with self.assertRaises(subscriptions.SubscriptionError) as ctx:
            subscriptions.allocate_ports(3, subscriptions.MAX_START_PORT - 1, set(), lambda p: True)
        self.assertEqual(ctx.exception.code, "no_ports")

    def test_start_port_setting(self):
        self.assertEqual(subscriptions.get_start_port(), 1080)
        subscriptions.set_start_port(2000)
        self.assertEqual(subscriptions.get_start_port(), 2000)
        for bad in ("abc", "80", str(subscriptions.MAX_START_PORT + 1), ""):
            with self.assertRaises(ValueError):
                subscriptions.parse_start_port(bad)
        settings_manager.set_setting(subscriptions.START_PORT_KEY, "garbage")
        self.assertEqual(subscriptions.get_start_port(), 1080)

    def test_build_proxy_config_is_complete_vless(self):
        base = {"url": "", "listen": "127.0.0.1:1081", "backend": "auto", "system_proxy": True, "group": "old"}
        link = _vless(port=8443, remark="Netherlands%201")
        cfg = subscriptions.build_proxy_config(base, link, 1090, 7, "g1", "sub_1")
        self.assertEqual(cfg["url"], link)
        self.assertEqual(cfg["listen"], "127.0.0.1:1090")
        self.assertEqual(cfg["name"], "Netherlands 1")
        self.assertEqual((cfg["order"], cfg["groups"], cfg["subscription"]), (7, ["g1"], "sub_1"))
        self.assertNotIn("group", cfg)  # the base config's legacy key is dropped
        self.assertFalse(cfg["system_proxy"] or cfg["work_proxy"] or cfg["telegram_proxy"])
        for key in ("udp", "connectTimeout", "backend", "logLevel", "killswitch", "username", "password"):
            self.assertIn(key, cfg)
        bare = subscriptions.build_proxy_config(base, link, 1090, 7, None, "s")
        self.assertNotIn("group", bare)
        self.assertNotIn("groups", bare)

    def test_record_subscription_updates_same_url(self):
        a = subscriptions.record_subscription("https://x/sub", "vless", "g1", 3)
        b = subscriptions.record_subscription("https://x/sub", "vless", "g2", 5)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(subscriptions.load_subscriptions()), 1)
        self.assertEqual(subscriptions.load_subscriptions()[0]["group"], "g2")


class TestFetchRoutes(unittest.TestCase):
    def test_falls_back_to_proxy_when_direct_fails(self):
        calls = []

        def fake_fetch(url, timeout, proxy):
            calls.append(proxy)
            if proxy is None:
                raise OSError("EOF occurred in violation of protocol")
            return b"body"

        with mock.patch.object(subscriptions, "_fetch_once", side_effect=fake_fetch):
            body, route = subscriptions.fetch_subscription(
                "https://example.invalid/s", ["http://127.0.0.1:11015", "http://127.0.0.1:11020"])
        self.assertEqual((body, route), (b"body", "http://127.0.0.1:11015"))
        self.assertEqual(calls, [None, "http://127.0.0.1:11015"])

    def test_http_error_is_not_retried_elsewhere(self):
        err = subscriptions.urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(subscriptions, "_fetch_once", side_effect=err) as f:
            with self.assertRaises(subscriptions.SubscriptionError) as ctx:
                subscriptions.fetch_subscription("https://example.invalid/s", ["http://127.0.0.1:11015"])
        self.assertEqual((ctx.exception.code, ctx.exception.detail), ("http_status", "403"))
        self.assertEqual(f.call_count, 1)

    def test_rejects_non_http_url(self):
        with self.assertRaises(subscriptions.SubscriptionError):
            subscriptions.fetch_subscription("vless://abc@host:1")


class TestStartPortInBackup(unittest.TestCase):
    """The start port lives in settings.json, which backups carry; prove the round trip."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_subs_backup_"))
        self.saved = {name: getattr(backup_manager, name) for name in
                      ("ROOT_DIR", "SETTINGS_FILE", "INSTANCES_FILE", "CONFIG_FILE", "ENV_FILE")}
        self.saved_settings = settings_manager.SETTINGS_FILE
        backup_manager.ROOT_DIR = self.tmp
        backup_manager.SETTINGS_FILE = self.tmp / "settings.json"
        backup_manager.INSTANCES_FILE = self.tmp / "instances.json"
        backup_manager.CONFIG_FILE = self.tmp / "config.json"
        backup_manager.ENV_FILE = self.tmp / ".env"
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"
        (self.tmp / "instances.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(backup_manager, name, value)
        settings_manager.SETTINGS_FILE = self.saved_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_restores_start_port(self):
        subscriptions.set_start_port(1234)
        out_dir = self.tmp / "out"
        out_dir.mkdir()
        hbak = backup_manager.export_encrypted_backup("Pass_123456!", out_dir)

        subscriptions.set_start_port(4321)
        self.assertEqual(subscriptions.get_start_port(), 4321)

        backup_manager.restore_encrypted_backup(hbak, "Pass_123456!")
        self.assertEqual(subscriptions.get_start_port(), 1234)


class _SubsTestApp(_GroupsTestApp):
    """Like VlessApp: after() from worker threads is queued and run by the GUI thread."""

    def __init__(self):
        import queue
        self._thread_calls = queue.SimpleQueue()
        super().__init__()
        self._drain()

    def after(self, ms, func=None, *args):
        if func is not None and threading.current_thread() is not threading.main_thread():
            self._thread_calls.put((func, args))
            return None
        return tk.Tk.after(self, ms, func, *args)

    def _drain(self):
        while not self._thread_calls.empty():
            func, args = self._thread_calls.get_nowait()
            func(*args)
        self._drain_id = tk.Tk.after(self, 20, self._drain)

    def destroy(self):
        try:
            self.after_cancel(self._drain_id)
        except Exception:
            pass
        super().destroy()


for _name in ("add_subscription_proxies", "_next_global_id", "_subscription_fallback_proxies",
              "open_subscription_dialog", "suggest_group_name"):
    setattr(_SubsTestApp, _name, getattr(gui.VlessApp, _name))


class _SubscriptionServer:
    """Serves `body` at /sub over plain HTTP on localhost."""

    def __init__(self, body: bytes):
        payload = body

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/sub" else 404)
                self.end_headers()
                if self.path == "/sub":
                    self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/sub"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestSubscriptionImportGui(_TempSettingsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        subscriptions.set_start_port(TEST_START_PORT)
        self.app = _SubsTestApp()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        _release_tk(self)
        super().tearDown()

    def _add_existing(self, port, gid):
        cfg = {"name": f"old{port}", "url": _vless(port=port), "listen": f"127.0.0.1:{port}", "order": gid}
        inst = gui.ProxyInstance(self.app, cfg, gid)
        self.app.instances.append(inst)
        return inst

    def test_add_proxies_ports_ids_group(self):
        self._add_existing(TEST_START_PORT, 0)
        self._add_existing(TEST_START_PORT + 2, 5)  # ids are gappy after deletions
        links = [_vless(remark=f"n{i}") for i in range(3)]
        with mock.patch.object(gui, "load_base_config", return_value={"url": "", "listen": "x", "backend": "auto"}):
            res = self.app.add_subscription_proxies("http://s/sub", links, None, new_group_name="Imported")
        self.assertEqual(res["ports"], [TEST_START_PORT + 1, TEST_START_PORT + 3, TEST_START_PORT + 4])
        ids = [i.global_id for i in self.app.instances]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(i.global_id for i in res["instances"]), [6, 7, 8])
        group = proxy_groups.get_group(res["group_id"])
        self.assertEqual(group["name"], "Imported")
        for inst, link in zip(res["instances"], links):
            self.assertEqual(inst.cfg["url"], link)
            self.assertEqual(inst.cfg["groups"], [group["id"]])
            self.assertEqual(inst.cfg["subscription"], res["subscription"]["id"])
            self.assertEqual(inst.get_group_ids(), [group["id"]])

    def test_port_shortage_changes_nothing(self):
        with mock.patch.object(subscriptions, "allocate_ports", side_effect=subscriptions.SubscriptionError("no_ports", "2")):
            with self.assertRaises(subscriptions.SubscriptionError):
                self.app.add_subscription_proxies("http://s/sub", [_vless()], None, new_group_name="X")
        self.assertEqual(self.app.instances, [])
        self.assertEqual(proxy_groups.load_groups(), [])

    def _pump_until(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _run_wizard(self, url, group_choice):
        self.app.open_subscription_dialog()
        dlg = self.app._subscription_dialog
        self.app.update()
        self.assertIn("1", dlg.step_lbl.cget("text"))
        dlg.next_btn.invoke()                      # type: VLESS (the only one)
        dlg.url_var.set(url)
        dlg.next_btn.invoke()                      # URL
        self.assertIn("3", dlg.step_lbl.cget("text"))
        group_choice(dlg)
        dlg.next_btn.invoke()                      # Add (async)
        return dlg

    def test_wizard_rejects_bad_url(self):
        self.app.open_subscription_dialog()
        dlg = self.app._subscription_dialog
        dlg.next_btn.invoke()
        dlg.url_var.set("ftp://nope")
        dlg.next_btn.invoke()
        self.assertEqual(dlg.status_lbl.cget("text"), gui.t("err_sub_bad_url"))
        self.assertIn("2", dlg.step_lbl.cget("text"))
        dlg.destroy()

    def test_wizard_imports_into_existing_group_async(self):
        target = proxy_groups.create_group("Target")
        links = [_vless(remark="one"), _vless(remark="two"), "vmess://skip"]
        server = _SubscriptionServer(base64.b64encode("\n".join(links).encode()))
        try:
            def choose_existing(dlg):
                dlg.group_mode_var.set("existing")
                dlg._sync_group_inputs()
                dlg.group_combo.current(0)

            dlg = self._run_wizard(server.url, choose_existing)
            self.assertTrue(dlg._busy)                              # the fetch runs in the background
            self.assertIn(gui.t("sub_loading"), dlg.status_lbl.cget("text"))
            self.assertTrue(self._pump_until(lambda: not dlg._busy))
        finally:
            server.close()
        self.assertEqual([i.get_display_name() for i in self.app.instances], ["one", "two"])
        self.assertTrue(all(i.get_group_ids() == [target["id"]] for i in self.app.instances))
        labels = [w.cget("text") for w in _walk(dlg.body) if isinstance(w, tk.Label)]
        self.assertTrue(any("✅" in s for s in labels), labels)
        dlg.destroy()

    def test_wizard_reports_nothing_new(self):
        link = _vless(remark="dup")
        self.app.instances.append(gui.ProxyInstance(self.app, {"url": link, "listen": "127.0.0.1:21999"}, 0))
        server = _SubscriptionServer(link.encode())
        try:
            dlg = self._run_wizard(server.url, lambda d: d.new_group_var.set("Never created"))
            self.assertTrue(self._pump_until(lambda: not dlg._busy))
        finally:
            server.close()
        self.assertIn("⚠️", dlg.status_lbl.cget("text"))
        self.assertEqual(len(self.app.instances), 1)
        self.assertEqual(proxy_groups.load_groups(), [])  # no empty group left behind
        dlg.destroy()


def _wait_port(port, timeout=15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class TestImportedProxyWorksEndToEnd(_TempSettingsMixin, unittest.TestCase):
    """Subscription → wizard → generated config → main.py → SOCKS5 → VLESS → HTTP target."""

    def setUp(self):
        super().setUp()
        subscriptions.set_start_port(TEST_START_PORT + 100)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.user_id = str(uuid.uuid4())
        self.vless = asyncio.run_coroutine_threadsafe(FakeVlessServer(self.user_id).start(), self.loop).result(10)
        self.target = asyncio.run_coroutine_threadsafe(TinyHttpServer().start(), self.loop).result(10)
        self.proc = None
        self.app = _SubsTestApp()

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self.app.destroy()
        except Exception:
            pass
        # Drop Tk objects here, on the main thread: if a garbage collection ran them down
        # inside the loop thread, Tcl would abort with "async handler deleted by the wrong thread"
        self.app = None
        gc.collect()
        asyncio.run_coroutine_threadsafe(self.target.stop(), self.loop).result(10)
        asyncio.run_coroutine_threadsafe(self.vless.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(10)
        self.loop.close()
        super().tearDown()

    def test_imported_proxy_carries_traffic(self):
        link = f"vless://{self.user_id}@127.0.0.1:{self.vless.port}?type=tcp&security=none&encryption=none#E2E%20node"
        server = _SubscriptionServer(base64.b64encode(link.encode()))
        try:
            self.app.open_subscription_dialog()
            dlg = self.app._subscription_dialog
            dlg.next_btn.invoke()
            dlg.url_var.set(server.url)
            dlg.next_btn.invoke()
            dlg.new_group_var.set("E2E")
            dlg.next_btn.invoke()
            end = time.time() + 15
            while dlg._busy and time.time() < end:
                self.app.update()
                time.sleep(0.02)
        finally:
            server.close()
        self.assertEqual(len(self.app.instances), 1, dlg.status_lbl.cget("text"))
        inst = self.app.instances[0]
        port = int(inst.get_listen()[1])
        self.assertEqual(port, TEST_START_PORT + 100)
        self.assertEqual(inst.get_display_name(), "E2E node")

        # Start it exactly like the GUI does (proxy_command + the instance config file)
        cfg_path = self.tmp_dir / "instance.json"
        cfg_path.write_text(json.dumps(inst.get_config(), ensure_ascii=False), encoding="utf-8")
        self.proc = subprocess.Popen(
            gui.proxy_command(cfg_path), cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.assertTrue(_wait_port(port), "imported proxy did not open its SOCKS5 port")

        async def roundtrip():
            reader, writer, rep = await socks5_connect("127.0.0.1", port, "127.0.0.1", self.target.port)
            self.assertEqual(rep, 0)
            writer.write(b"GET /through-subscription HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            return data

        data = asyncio.run_coroutine_threadsafe(roundtrip(), self.loop).result(20)
        self.assertIn(b"200 OK", data)
        self.assertIn(b"echo:/through-subscription", data)
        self.assertIn("/through-subscription", self.target.hits)


if __name__ == "__main__":
    unittest.main()
