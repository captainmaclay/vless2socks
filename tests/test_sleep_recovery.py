"""Unit tests for sleep recovery, single-instance IPC, and tunnel probing."""

import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vless2socks.ipc_server import (
    SingleInstanceIpcServer,
    request_show_running_instance,
    send_ipc_command,
)
from vless2socks.power_watcher import PowerBroadcastWatcher
from vless2socks.tunnel_probe import probe_socks5_tunnel


class TestTunnelProbe(unittest.TestCase):
    """Tests for SOCKS5 upstream tunnel probe."""

    def test_probe_fails_on_closed_port(self):
        # Using a port that is guaranteed closed
        ok, msg = probe_socks5_tunnel("127.0.0.1", 49151, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("unreachable", msg.lower())

    def test_probe_handles_invalid_greeting(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _serve():
            try:
                conn, _ = server.accept()
                with conn:
                    # Send non-socks5 response
                    conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            except Exception:
                pass
            finally:
                server.close()

        threading.Thread(target=_serve, daemon=True).start()
        ok, msg = probe_socks5_tunnel("127.0.0.1", port, timeout=0.5)
        self.assertFalse(ok)
        self.assertTrue("handshake" in msg.lower() or "rejected" in msg.lower())

    def test_probe_success_on_mock_socks5(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _serve():
            try:
                conn, _ = server.accept()
                with conn:
                    # 1. Greeting
                    g = conn.recv(3)
                    if g == b"\x05\x01\x00":
                        conn.sendall(b"\x05\x00")
                    # 2. CONNECT
                    req = conn.recv(10)
                    if len(req) >= 4 and req[0] == 5 and req[1] == 1:
                        # Reply success: VER=5, REP=0, RSV=0, ATYP=1, BND.ADDR=0.0.0.0, BND.PORT=0
                        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            except Exception:
                pass
            finally:
                server.close()

        threading.Thread(target=_serve, daemon=True).start()
        ok, msg = probe_socks5_tunnel("127.0.0.1", port, timeout=0.5)
        self.assertTrue(ok)
        self.assertEqual(msg, "Tunnel active")


class TestIpcServer(unittest.TestCase):
    """Tests for SingleInstanceIpcServer."""

    def setUp(self):
        self.show_called = False
        self.restart_called = False
        self.test_port = 10198  # Use dedicated port for testing
        self.server = SingleInstanceIpcServer(
            port=self.test_port,
            on_show=self._on_show,
            on_restart=self._on_restart,
        )
        self.assertTrue(self.server.start())

    def tearDown(self):
        self.server.stop()

    def _on_show(self):
        self.show_called = True

    def _on_restart(self):
        self.restart_called = True

    def test_ipc_ping(self):
        resp = send_ipc_command("PING", port=self.test_port, timeout=1.0)
        self.assertEqual(resp, "PONG")

    def test_ipc_show(self):
        resp = send_ipc_command("SHOW", port=self.test_port, timeout=1.0)
        self.assertEqual(resp, "OK")
        time.sleep(0.05)
        self.assertTrue(self.show_called)

    def test_ipc_restart(self):
        resp = send_ipc_command("RESTART", port=self.test_port, timeout=1.0)
        self.assertEqual(resp, "OK")
        time.sleep(0.05)
        self.assertTrue(self.restart_called)

    def test_ipc_unknown_command(self):
        resp = send_ipc_command("FOOBAR", port=self.test_port, timeout=1.0)
        self.assertEqual(resp, "UNKNOWN")

    def test_request_show_running_instance_helper(self):
        ok = request_show_running_instance(port=self.test_port)
        self.assertTrue(ok)
        self.assertTrue(self.show_called)


class TestPowerWatcherLifecycle(unittest.TestCase):
    """Lifecycle test for PowerBroadcastWatcher."""

    def test_start_and_stop_cleanly(self):
        watcher = PowerBroadcastWatcher(on_resume=lambda: None)
        watcher.start()
        time.sleep(0.1)
        watcher.stop()
        self.assertFalse(watcher._running)


if __name__ == "__main__":
    unittest.main()
