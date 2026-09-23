"""Unit tests for proxy custom naming functionality and UI propagation."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import gui


class MockApp:
    def __init__(self):
        self.instances = []
        self.saved_data = None
        self.overview_refreshed = False
        self.tabs_refreshed = False

    def save_all(self):
        self.saved_data = [inst.get_config() for inst in self.instances]

    def refresh_overview(self):
        self.overview_refreshed = True

    def refresh_current_page_tabs(self):
        self.tabs_refreshed = True

    def sort_instances(self):
        self.instances.sort(key=lambda x: (x.get_order(), x.global_id))

    def prompt_rename_proxy(self, inst):
        pass

    def prompt_reorder_proxy(self, inst):
        pass


class TestProxyNaming(unittest.TestCase):
    def setUp(self):
        self.app = MockApp()

    def test_default_display_names(self):
        """Instances return appropriate default names when 'name' is not set."""
        inst_1015 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015", "url": ""}, 0)
        self.assertEqual(inst_1015.get_display_name(), "System Proxy")

        inst_vless = gui.ProxyInstance(self.app, {
            "listen": "127.0.0.1:1081",
            "url": "vless://00000000-0000-0000-0000-000000000000@nl.example.com:443#Netherlands"
        }, 1)
        self.assertEqual(inst_vless.get_display_name(), "Netherlands")

    def test_custom_name_override(self):
        """Setting a custom name overrides defaults and propagates to config."""
        inst = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1081", "url": ""}, 0)
        self.app.instances = [inst]

        inst.set_name("My Work Proxy")
        self.assertEqual(inst.get_display_name(), "My Work Proxy")
        self.assertEqual(inst.cfg["name"], "My Work Proxy")

        cfg = inst.get_config()
        self.assertEqual(cfg.get("name"), "My Work Proxy")
        self.assertTrue(self.app.overview_refreshed)
        self.assertTrue(self.app.tabs_refreshed)

    def test_rename_system_proxy(self):
        """System proxy can also be renamed by user if desired."""
        inst_1015 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015", "url": ""}, 0)
        self.app.instances = [inst_1015]

        inst_1015.set_name("Primary Antigravity Tunnel")
        self.assertEqual(inst_1015.get_display_name(), "Primary Antigravity Tunnel")
        self.assertEqual(inst_1015.get_config()["name"], "Primary Antigravity Tunnel")


if __name__ == "__main__":
    unittest.main()
