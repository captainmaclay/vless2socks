"""TelegramProxy dispatcher health: end-to-end probe (req_pq through the port), dead-tunnel
(stall) detection, client-closed relays not counted as failures, self-check rotation and
relay drop, file log. Local fake SOCKS5 upstreams only - no real network, no GUI."""

import asyncio
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests  # noqa: F401  (isolated data paths)
from vless2socks import telegram_proxy as tp

RES_PQ = 0x05162463


class FakeUpstream:
    """SOCKS5 server on 127.0.0.1:<free port>. mode "dc": answers an abridged req_pq with a
    resPQ-shaped packet; "silent": accepts CONNECT, then never answers."""

    def __init__(self, mode: str):
        self.mode = mode
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        try:
            c.settimeout(10)
            c.recv(3)
            c.sendall(b"\x05\x00")
            head = c.recv(4)
            if len(head) < 4:                           # the dispatcher's quick port probe
                return
            if head[3] == 1:
                c.recv(6)
            elif head[3] == 3:
                c.recv(c.recv(1)[0] + 2)
            c.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            if self.mode == "silent":
                while c.recv(4096):
                    pass
                return
            c.recv(1)                                   # 0xef abridged marker
            n = c.recv(1)[0] * 4
            got = b""
            while len(got) < n:
                got += c.recv(n - len(got))
            body = struct.pack("<qqi", 0, 0, 24) + struct.pack("<I", RES_PQ) + b"\x00" * 20
            c.sendall(bytes([len(body) // 4]) + body)
            while c.recv(4096):
                pass
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        self.sock.close()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestProbe(unittest.TestCase):
    def test_probe_ok_through_working_path(self):
        up = FakeUpstream("dc")
        try:
            ok, detail = tp.probe_mtproto("127.0.0.1", up.port, timeout=3)
            self.assertTrue(ok, detail)
            self.assertIn("resPQ", detail)
        finally:
            up.close()

    def test_probe_fails_on_dead_tunnel_behind_a_live_port(self):
        up = FakeUpstream("silent")          # the port answers, the server behind it does not
        try:
            ok, detail = tp.probe_mtproto("127.0.0.1", up.port, timeout=1.0)
            self.assertFalse(ok)
            self.assertIn("timed out", detail.lower())
        finally:
            up.close()

    def test_probe_fails_on_closed_port(self):
        ok, _ = tp.probe_mtproto("127.0.0.1", free_port(), timeout=1.0)
        self.assertFalse(ok)


class TestVerdict(unittest.TestCase):
    def setUp(self):
        self.d = tp.TelegramProxyDispatcher()

    def test_rules(self):
        v = self.d.relay_verdict
        self.assertTrue(v({"sent": 10, "received": 5}))
        self.assertIsNone(v({"sent": 0, "received": 0}))
        self.assertFalse(v({"sent": 10, "received": 0, "stalled": True}))
        # Telegram dropping one of its parallel connections at once: no verdict
        self.assertIsNone(v({"sent": 10, "received": 0, "client_closed": True, "duration": 0.2}))
        # the client gave up after a long wait without an answer: the upstream is dead
        self.assertFalse(v({"sent": 10, "received": 0, "client_closed": True, "duration": 30.0}))
        # upstream closed without answering
        self.assertFalse(v({"sent": 10, "received": 0, "client_closed": False, "duration": 0.5}))
        self.assertIsNone(v({"sent": 10, "received": 0, "idle": True}))

    def test_none_verdict_does_not_count(self):
        proxy = {"host": "127.0.0.1", "port": 1, "name": "p"}
        for _ in range(5):
            self.d.report_result(proxy, None)
        self.assertTrue(self.d._usable(proxy))


class DispatcherCase(unittest.TestCase):
    def start(self, modes):
        self.ups = [FakeUpstream(m) for m in modes]
        self.d = tp.TelegramProxyDispatcher(listen_port=free_port())
        self.d.RELAY_STALL_TIMEOUT_S = 0.6
        self.d.MONITOR_STEP_S = 0.1
        self.d.set_pool([{"host": "127.0.0.1", "port": u.port, "name": f"up{i}", "telegram_proxy": True}
                         for i, u in enumerate(self.ups)])
        self.d.start()
        self.assertTrue(self.d.is_running())

    def tearDown(self):
        d = getattr(self, "d", None)
        if d is not None:
            d.stop()
        for u in getattr(self, "ups", []):
            u.close()

    def socks_connect(self):
        s = socket.create_connection(("127.0.0.1", self.d.listen_port), timeout=5)
        s.sendall(b"\x05\x01\x00")
        s.recv(2)
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("149.154.167.51") + struct.pack(">H", 443))
        s.recv(10)
        return s


class TestRelay(DispatcherCase):
    def test_dead_tunnel_is_closed_and_counted(self):
        self.start(["silent"])
        s = self.socks_connect()
        s.sendall(b"hello")
        s.settimeout(5)
        t = time.time()
        self.assertEqual(s.recv(10), b"")               # closed by the stall monitor
        self.assertLess(time.time() - t, 3)
        s.close()
        time.sleep(0.2)
        self.assertEqual(self.d._failures[("127.0.0.1", self.ups[0].port)][0], 1)

    def test_client_dropping_a_fresh_connection_is_not_a_failure(self):
        self.start(["silent"])
        for _ in range(3):
            s = self.socks_connect()
            s.sendall(b"x")
            s.close()                                   # like Telegram dropping an extra one
        time.sleep(0.4)
        self.assertNotIn(("127.0.0.1", self.ups[0].port), self.d._failures)

    def test_working_path_relays_and_counts_ok(self):
        self.start(["dc"])
        ok, detail = tp.probe_mtproto("127.0.0.1", self.d.listen_port, timeout=3)
        self.assertTrue(ok, detail)


class TestSelfCheck(DispatcherCase):
    def test_failed_check_rotates_and_drops_relays(self):
        self.start(["silent", "dc"])
        self.d.RELAY_STALL_TIMEOUT_S = 30.0             # keep the hanging relay open
        hung = self.socks_connect()                     # a relay Telegram would sit on
        time.sleep(0.2)
        self.assertEqual(len(self.d._relays), 1)
        self.assertFalse(self.d.self_check(timeout=1.0))
        self.assertEqual(self.d.peek_active()["name"], "up1")
        hung.settimeout(3)
        self.assertEqual(hung.recv(10), b"")            # dropped: Telegram reconnects
        hung.close()
        self.assertTrue(self.d.self_check(timeout=3.0))  # next one goes through up1

    def test_check_is_skipped_when_not_running(self):
        d = tp.TelegramProxyDispatcher(listen_port=free_port())
        self.assertTrue(d.self_check(timeout=0.5))


class TestFileLog(unittest.TestCase):
    def test_log_goes_to_the_file(self):
        old = tp._file_log_path
        tp._file_log_path = None
        path = os.path.join(tempfile.mkdtemp(prefix="v2s_tglog_"), "runtime", "telegram_proxy.log")
        try:
            self.assertEqual(tp.enable_file_log(path), path)
            self.assertEqual(tp.enable_file_log(), path)          # idempotent
            tp.logger.warning("probe line")
            for h in tp.logger.handlers:
                h.flush()
            with open(path, encoding="utf-8") as f:
                self.assertIn("probe line", f.read())
        finally:
            for h in list(tp.logger.handlers):
                if getattr(h, "baseFilename", "") == os.path.abspath(path):
                    tp.logger.removeHandler(h)
                    h.close()
            tp._file_log_path = old


if __name__ == "__main__":
    unittest.main()
