"""Unit tests verifying SOCKS5 process termination, port releasing, and status indication accuracy."""

import os
import sys
import time
import socket
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import geo_ip
from gui import ProxyInstance, is_port_alive, is_port_free, kill_processes_on_port


class StopAndIndicationTest(unittest.TestCase):
    def setUp(self):
        # Create a mock App and ProxyInstance
        self.mock_app = MagicMock()
        self.mock_app.instances = []
        self.mock_app.save_all = MagicMock()
        self.mock_app.update_tray_icon = MagicMock()
        self.mock_app.refresh_overview = MagicMock()
        self.mock_app._update_header_stats = MagicMock()

        self.cfg = {
            "name": "TestInstance",
            "listen": "127.0.0.1:19876",
            "url": "socks5://user:pass@127.0.0.1:20039#test",
            "killswitch": False,
        }

        # Instantiate ProxyInstance
        with patch.object(ProxyInstance, "_init_geo_from_url"):
            self.inst = ProxyInstance(
                app=self.mock_app,
                cfg=self.cfg,
                global_id=999,
            )

    def test_stop_releases_port_and_invalidates_cache(self):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        self.inst.process = mock_proc
        self.inst.running = True
        self.inst.healthy = True

        # Pre-populate GeoIP cache
        geo_ip._GEO_CACHE["127.0.0.1:19876"] = (time.time(), {"ip": "1.2.3.4", "verified": True})
        self.inst.geo_info["verified"] = True
        self.inst.geo_info["ip"] = "1.2.3.4"

        with patch("gui.subprocess.run") as mock_run, \
             patch("gui.kill_processes_on_port") as mock_kill_port:
            mock_kill_port.return_value = (True, "freed")
            self.inst.stop()

            # Process tree kill attempted
            if sys.platform == "win32":
                self.assertTrue(any("taskkill" in str(c) for c in mock_run.call_args_list))

            # Both SOCKS5 and HTTP ports cleaned up
            called_ports = [c[0][0] for c in mock_kill_port.call_args_list]
            self.assertIn(19876, called_ports)
            self.assertIn(29876, called_ports)

            # State updated to stopped
            self.assertFalse(self.inst.running)
            self.assertFalse(self.inst.healthy)
            self.assertIsNone(self.inst.process)
            self.assertTrue(self.inst._manual_stop)

            # Geo cache invalidated
            self.assertNotIn("127.0.0.1:19876", geo_ip._GEO_CACHE)
            self.assertFalse(self.inst.geo_info["verified"])
            self.assertEqual(self.inst.geo_info["ip"], "")

            # UI indicators refreshed
            self.mock_app.update_tray_icon.assert_called()
            self.mock_app.refresh_overview.assert_called()
            self.mock_app._update_header_stats.assert_called()

    def test_check_geo_now_when_stopped(self):
        self.inst.running = False
        with patch("gui.is_port_alive", return_value=False), \
             patch("geo_ip.fetch_geo_async") as mock_fetch:
            self.inst.check_geo_now()

            # Should early return without probing external network
            mock_fetch.assert_not_called()
            self.assertFalse(self.inst.geo_info["verified"])
            self.assertEqual(self.inst.geo_info["ip"], "")

    def test_check_geo_now_when_running(self):
        self.inst.running = True
        with patch("gui.is_port_alive", return_value=True), \
             patch("geo_ip.fetch_geo_async") as mock_fetch:
            self.inst.check_geo_now()
            mock_fetch.assert_called_once()

    def test_poll_health_detects_dead_port_when_running(self):
        self.inst.running = True
        self.inst.healthy = True
        with patch("gui.is_port_alive", return_value=False), \
             patch.object(self.inst, "_set_state") as mock_set_state:
            self.inst.poll_health()

            self.assertFalse(self.inst.healthy)
            mock_set_state.assert_called_with("error")
            self.mock_app.refresh_overview.assert_called()

    def test_poll_health_cleans_up_orphan_when_manually_stopped(self):
        self.inst.running = False
        self.inst._manual_stop = True
        with patch("gui.is_port_alive", return_value=True), \
             patch("gui.kill_processes_on_port") as mock_kill, \
             patch("geo_ip.invalidate_cache") as mock_inval:
            self.inst.poll_health()

            # Should kill the lingering orphan process
            called_ports = [c[0][0] for c in mock_kill.call_args_list]
            self.assertIn(19876, called_ports)
            mock_inval.assert_called_with("127.0.0.1", 19876)

    def test_poll_health_syncs_running_if_preexisting_active(self):
        self.inst.running = False
        self.inst._manual_stop = False
        with patch("gui.is_port_alive", return_value=True), \
             patch.object(self.inst, "_set_state") as mock_set_state:
            self.inst.poll_health()

            self.assertTrue(self.inst.running)
            self.assertTrue(self.inst.healthy)
            mock_set_state.assert_called_with("running")
            self.mock_app.refresh_overview.assert_called()

    def test_autostart_configured_proxies_includes_socks5(self):
        from gui import VlessApp

        # Create dummy instances
        inst_vless = MagicMock()
        inst_vless.cfg = {"url": "vless://uuid@host:443?security=tls#vless"}
        inst_vless.running = False
        inst_vless.start = MagicMock()

        inst_socks5 = MagicMock()
        inst_socks5.cfg = {"url": "socks5://user:pass@1.2.3.4:1080#socks5"}
        inst_socks5.running = False
        inst_socks5.start = MagicMock()

        inst_socks = MagicMock()
        inst_socks.cfg = {"url": "socks://user:pass@5.6.7.8:1080#socks"}
        inst_socks.running = False
        inst_socks.start = MagicMock()

        inst_empty = MagicMock()
        inst_empty.cfg = {"url": ""}
        inst_empty.running = False
        inst_empty.start = MagicMock()

        inst_already_running = MagicMock()
        inst_already_running.cfg = {"url": "socks5://user:pass@9.9.9.9:1080#running"}
        inst_already_running.running = True
        inst_already_running.start = MagicMock()

        mock_vless_app = MagicMock(spec=VlessApp)
        mock_vless_app.instances = [
            inst_vless,
            inst_socks5,
            inst_socks,
            inst_empty,
            inst_already_running,
        ]
        mock_vless_app.refresh_overview = MagicMock()
        mock_vless_app.update_tray_icon = MagicMock()
        mock_vless_app._update_header_stats = MagicMock()

        # Call the actual autostart_configured_proxies method
        VlessApp.autostart_configured_proxies(mock_vless_app)

        # Verify that both VLESS and SOCKS5 instances were started
        inst_vless.start.assert_called_once()
        inst_socks5.start.assert_called_once()
        inst_socks.start.assert_called_once()

        # Empty and already running should NOT be started
        inst_empty.start.assert_not_called()
        inst_already_running.start.assert_not_called()

        # UI updates triggered
        mock_vless_app.refresh_overview.assert_called_once()
        mock_vless_app.update_tray_icon.assert_called_once()
        mock_vless_app._update_header_stats.assert_called_once()


if __name__ == "__main__":
    unittest.main()
