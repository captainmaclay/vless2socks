"""Unit tests for WSL Proxy Isolation GUI Integration in vless2socks."""

import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import gui
import i18n


class TestWslIsolationGuiIntegration(unittest.TestCase):
    def test_has_wsl_iso_flag(self):
        """Verify wsl-proxy-isolation is imported and HAS_WSL_ISO is True."""
        self.assertTrue(gui.HAS_WSL_ISO)

    def test_i18n_wsl_keys(self):
        """Verify all WSL isolation keys are present in both EN and RU."""
        required_keys = [
            "nav_wsl_isolation",
            "wsl_title",
            "wsl_subtitle",
            "wsl_lbl_status",
            "wsl_lbl_kernel",
            "wsl_lbl_port",
            "wsl_lbl_port_status",
            "wsl_lbl_dns",
            "wsl_lbl_leak",
            "btn_wsl_apply",
            "btn_wsl_test",
            "btn_wsl_remove",
            "btn_wsl_refresh",
            "btn_wsl_clear_log",
            "wsl_msg_applied",
            "wsl_msg_removed",
            "wsl_msg_confirm_remove",
        ]
        for lang in ("en", "ru"):
            i18n.set_language(lang)
            for k in required_keys:
                val = i18n.t(k)
                self.assertNotEqual(val, k, f"Missing {lang} translation for '{k}'")
                self.assertTrue(len(val) > 0)


if __name__ == "__main__":
    unittest.main()
