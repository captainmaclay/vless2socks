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
        server.listen(5)
        port = server.getsockname()[1]
        ready = threading.Event()

        def _serve():
            ready.set()
            try:
                while True:
                    conn, _ = server.accept()
                    with conn:
                        conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            except Exception:
                pass
            finally:
                try:
                    server.close()
                except Exception:
                    pass

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        ready.wait(timeout=1.0)
        try:
            ok, msg = probe_socks5_tunnel("127.0.0.1", port, timeout=0.5)
            self.assertFalse(ok)
            self.assertTrue("handshake" in msg.lower() or "rejected" in msg.lower())
        finally:
            server.close()

    def test_probe_success_on_mock_socks5(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        port = server.getsockname()[1]
        ready = threading.Event()

        def _serve():
            ready.set()
            try:
                while True:
                    conn, _ = server.accept()
                    with conn:
                        # 1. Greeting
                        g = conn.recv(3)
                        if g == b"\x05\x01\x00":
                            conn.sendall(b"\x05\x00")
                        # 2. CONNECT
                        req = conn.recv(10)
                        if len(req) >= 4 and req[0] == 5 and req[1] == 1:
                            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            except Exception:
                pass
            finally:
                try:
                    server.close()
                except Exception:
                    pass

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        ready.wait(timeout=1.0)
        try:
            ok, msg = probe_socks5_tunnel("127.0.0.1", port, timeout=0.5)
            self.assertTrue(ok)
            self.assertEqual(msg, "Tunnel active")
        finally:
            server.close()


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


class TestSelfRecoveryPolicy(unittest.TestCase):
    """Verify that self-recovery logic never loops rapidly on startup or during network outages."""

    def _make_instance(self):
        from unittest.mock import MagicMock
        from gui import ProxyInstance
        cfg = {"url": "vless://00000000-0000-0000-0000-000000000000@198.51.100.1:443?security=none", "listen": "127.0.0.1:19991"}
        app = MagicMock()
        return ProxyInstance(app, cfg, 991)

    def test_startup_grace_period_prevents_restart_loop(self):
        from unittest.mock import MagicMock, patch
        inst = self._make_instance()
        inst.running = True
        inst.healthy = False
        inst.process = MagicMock()
        inst.process.poll.return_value = None
        inst._start_time = time.time()  # Just started

        with patch.object(inst, "restart") as mock_restart, \
                patch.object(inst, "is_auto_reconnect_enabled", return_value=True):
            # Port check fails right after launch (xray still initializing)
            changed = inst.apply_health_status(False)
            self.assertFalse(changed)
            mock_restart.assert_not_called()
            self.assertFalse(inst.healthy)
            self.assertTrue(inst.running)

    def test_self_recovery_triggers_after_threshold_outside_grace_period(self):
        from unittest.mock import MagicMock, patch
        inst = self._make_instance()
        inst.running = True
        inst.healthy = True
        inst.process = MagicMock()
        inst.process.poll.return_value = None
        inst._start_time = time.time() - 30.0  # Past warmup window

        with patch.object(inst, "restart") as mock_restart, \
                patch.object(inst, "is_auto_reconnect_enabled", return_value=True):
            # Failure 1
            inst.apply_health_status(False)
            mock_restart.assert_not_called()
            self.assertEqual(inst._consecutive_unhealthy, 1)

            # Failure 2
            inst.apply_health_status(False)
            mock_restart.assert_not_called()
            self.assertEqual(inst._consecutive_unhealthy, 2)

            # Failure 3 -> Threshold reached, triggers self-recovery
            inst.apply_health_status(False)
            mock_restart.assert_called_once_with(blocking=False)
            self.assertEqual(inst._recovery_restart_count, 1)

    def test_self_recovery_respects_cooldown(self):
        from unittest.mock import MagicMock, patch
        inst = self._make_instance()
        inst.running = True
        inst.healthy = True
        inst.process = MagicMock()
        inst.process.poll.return_value = None
        inst._start_time = time.time() - 60.0
        inst._consecutive_unhealthy = 5
        inst._last_recovery_restart = time.time() - 10.0  # In cooldown (45s required)

        with patch.object(inst, "restart") as mock_restart, \
                patch.object(inst, "is_auto_reconnect_enabled", return_value=True):
            inst.apply_health_status(False)
            mock_restart.assert_not_called()

    def test_max_consecutive_recoveries_pauses_restarts(self):
        from unittest.mock import MagicMock, patch
        inst = self._make_instance()
        inst.running = True
        inst.healthy = True
        inst.process = MagicMock()
        inst.process.poll.return_value = None
        inst._start_time = time.time() - 120.0
        inst._consecutive_unhealthy = 5
        inst._last_recovery_restart = time.time() - 50.0  # Cooldown passed
        inst._recovery_restart_count = 3  # Max reached

        with patch.object(inst, "restart") as mock_restart, \
                patch.object(inst, "is_auto_reconnect_enabled", return_value=True):
            inst.apply_health_status(False)
            mock_restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
