"""Модульные тесты для утилиты изоляции WSL2."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import wsl_detector
import firewall_isolate
import isolation_tester


class TestWslDetector(unittest.TestCase):
    @patch("shutil.which")
    def test_is_wsl_installed_true(self, mock_which):
        mock_which.return_value = "C:\\Windows\\System32\\wsl.exe"
        self.assertTrue(wsl_detector.is_wsl_installed())

    @patch("shutil.which")
    def test_is_wsl_installed_false(self, mock_which):
        mock_which.return_value = None
        self.assertFalse(wsl_detector.is_wsl_installed())

    @patch("wsl_detector.subprocess.run")
    def test_get_wsl_distributions(self, mock_run):
        mock_res = MagicMock()
        mock_res.stdout = (
            "  NAME            STATE           VERSION\n"
            "* Ubuntu          Running         2\n"
            "  Debian          Stopped         2\n"
        )
        mock_run.return_value = mock_res
        distros = wsl_detector.get_wsl_distributions()
        self.assertEqual(len(distros), 2)
        self.assertEqual(distros[0]["name"], "Ubuntu")
        self.assertTrue(distros[0]["is_default"])
        self.assertEqual(distros[1]["name"], "Debian")
        self.assertFalse(distros[1]["is_default"])


class TestFirewallIsolate(unittest.TestCase):
    @patch("firewall_isolate.run_wsl_root_cmd")
    def test_check_wsl_isolation_active(self, mock_wsl):
        mock_wsl.return_value = (0, "table inet herdr_filter {\n chain output {\n } }")
        self.assertTrue(firewall_isolate.check_wsl_isolation_active())

        mock_wsl.return_value = (1, "no such table")
        self.assertFalse(firewall_isolate.check_wsl_isolation_active())

    @patch("firewall_isolate.run_wsl_root_cmd")
    def test_apply_wsl_isolation(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        ok = firewall_isolate.apply_wsl_isolation(port=1015, http_port=11015, make_persistent=False)
        self.assertTrue(ok)
        self.assertGreaterEqual(mock_wsl.call_count, 1)

    @patch("firewall_isolate.run_wsl_root_cmd")
    def test_remove_wsl_isolation(self, mock_wsl):
        mock_wsl.return_value = (0, "")
        ok = firewall_isolate.remove_wsl_isolation(full_clean=False)
        self.assertTrue(ok)
        cmd = mock_wsl.call_args[0][0]
        self.assertIn("nft delete table inet herdr_filter", cmd)


class TestIsolationTester(unittest.TestCase):
    @patch("isolation_tester.check_port_accessible")
    @patch("firewall_isolate.check_wsl_isolation_active")
    @patch("isolation_tester.exec_wsl_user")
    def test_run_isolation_audit_success(self, mock_exec, mock_iso, mock_port):
        mock_port.side_effect = [(True, 2), (True, 1)]
        mock_iso.return_value = True

        def fake_exec(cmd, distro=None):
            if "profile.d" in cmd:
                return 0, "ALL_PROXY='socks5h://127.0.0.1:1015'"
            if "--noproxy" in cmd:
                return 7, "Failed to connect"  # direct blocked
            if "--socks5-hostname" in cmd:
                return 0, "1.2.3.4"  # socks proxy egress ok
            if "-x http://" in cmd and ":2080" in cmd:
                return 7, "Failed to connect"  # bypass blocked
            if "api.anthropic.com" in cmd:
                return 0, "200"
            return 0, ""

        mock_exec.side_effect = fake_exec

        res = isolation_tester.run_isolation_audit(host="127.0.0.1", port=1015, http_port=11015)
        self.assertEqual(res["status"], "isolated")
        self.assertFalse(res["direct_leak_detected"])
        self.assertTrue(res["dns_leak_protected"])
        self.assertEqual(res["proxy_egress_ip"], "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
