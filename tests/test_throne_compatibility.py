"""Tests for Throne SOCKS/TUN compatibility with vless2socks proxies."""

import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json

from vless2socks.ipcheck import is_system_tun_active, check_ip_leak, check_ip_leak_async, IpReport


class ThroneCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    def test_tun_detection_with_sing_tun(self):
        mock_route_out = (
            "===========================================================================\n"
            "Interface List\n"
            " 90...........................sing-tun Tunnel #2\n"
            "Active Routes:\n"
            "Network Destination        Netmask          Gateway       Interface  Metric\n"
            "          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.157     50\n"
            "          0.0.0.0        248.0.0.0       172.19.0.2       172.19.0.1      0\n"
        )
        with patch("subprocess.check_output", return_value=mock_route_out):
            self.assertTrue(is_system_tun_active())

    def test_tun_detection_without_tun(self):
        mock_route_out = (
            "===========================================================================\n"
            "Interface List\n"
            " 20...9c 6b 00 22 46 1b ......Realtek Gaming 2.5GbE Family Controller\n"
            "Active Routes:\n"
            "Network Destination        Netmask          Gateway       Interface  Metric\n"
            "          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.157     50\n"
        )
        with patch("subprocess.check_output", return_value=mock_route_out):
            self.assertFalse(is_system_tun_active())

    async def test_shared_proxy_with_throne_is_safe(self):
        """Когда Throne и локальный прокси используют один и тот же сервер, это не утечка."""
        mock_report = IpReport(
            direct="62.60.234.188",
            tunnel="62.60.234.188",
            service="api.ipify.org",
            listen="127.0.0.1:1085",
        )
        with patch("vless2socks.ipcheck.compare_ip", return_value=mock_report):
            with patch("vless2socks.ipcheck.is_system_tun_active", return_value=True):
                is_leak, direct, tunnel, msg = await check_ip_leak_async("127.0.0.1", 1085)
                self.assertFalse(is_leak)
                self.assertEqual(direct, "62.60.234.188")
                self.assertEqual(tunnel, "62.60.234.188")
                self.assertIn("SAFE", msg)
                self.assertIn("Throne", msg)

    def test_gui_verify_killswitch_preserves_running_proxy_on_throne(self):
        """Интерфейс vless2socks не выключает прокси, если обнаружен Throne."""
        import gui

        mock_app = MagicMock()
        cfg = {
            "name": "Proxy 1085",
            "url": "socks5://bynbez:pass@lv1.example.com:20039#latvia",
            "listen": "127.0.0.1:1085",
            "killswitch": True,
        }
        inst = gui.ProxyInstance(mock_app, cfg, 0)
        inst.running = True
        inst.stop = MagicMock()

        with patch("vless2socks.ipcheck.is_system_tun_active", return_value=True):
            with patch("vless2socks.ipcheck.check_ip_leak", return_value=(True, "62.60.234.188", "62.60.234.188", "Leak")):
                inst.verify_killswitch(manual=False, sync=True)
                inst.stop.assert_not_called()
                self.assertTrue(inst.running)


if __name__ == "__main__":
    unittest.main()
