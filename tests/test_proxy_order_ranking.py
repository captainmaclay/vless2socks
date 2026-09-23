"""Unit tests for proxy numeric order ranking (integers and floats >= 0)."""

import json
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import gui


class MockApp:
    def __init__(self):
        self.instances = []
        self.saved = False

    def save_all(self):
        self.saved = True

    def refresh_overview(self):
        pass

    def refresh_current_page_tabs(self):
        pass

    def sort_instances(self):
        self.instances.sort(key=lambda x: (x.get_order(), x.global_id))

    def prompt_rename_proxy(self, inst):
        pass

    def prompt_reorder_proxy(self, inst):
        pass


class TestProxyOrderRanking(unittest.TestCase):
    def setUp(self):
        self.app = MockApp()

    def test_format_order(self):
        """format_order formats whole numbers as ints and decimals as clean floats."""
        self.assertEqual(gui.format_order(0), "0")
        self.assertEqual(gui.format_order(0.0), "0")
        self.assertEqual(gui.format_order(1), "1")
        self.assertEqual(gui.format_order(1.0), "1")
        self.assertEqual(gui.format_order(1.5), "1.5")
        self.assertEqual(gui.format_order(2.25), "2.25")
        self.assertEqual(gui.format_order("3.75"), "3.75")
        self.assertEqual(gui.format_order(10.0), "10")

    def test_order_defaults(self):
        """Port 1015 defaults to order 0.0, others default to global_id."""
        inst_sys = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015"}, 0)
        self.assertEqual(inst_sys.get_order(), 0.0)

        inst_1 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1081"}, 1)
        self.assertEqual(inst_1.get_order(), 1.0)

    def test_sorting_ascending_ranking(self):
        """Instances are sorted ascending by order: smaller values appear first."""
        i_third = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1083", "order": 3.0}, 2)
        i_first = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015", "order": 0.0}, 0)
        i_middle = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1082", "order": 1.5}, 1)

        self.app.instances = [i_third, i_first, i_middle]
        self.app.sort_instances()

        # Expected order: i_first (0.0), i_middle (1.5), i_third (3.0)
        self.assertEqual(self.app.instances[0].get_order(), 0.0)
        self.assertEqual(self.app.instances[1].get_order(), 1.5)
        self.assertEqual(self.app.instances[2].get_order(), 3.0)

    def test_float_order_assignment(self):
        """Can assign float order (e.g. 0.5) to position an instance between 0 and 1."""
        i0 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1015", "order": 0}, 0)
        i1 = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1081", "order": 1}, 1)
        i_inserted = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1082", "order": 2}, 2)

        self.app.instances = [i0, i1, i_inserted]
        self.app.sort_instances()
        self.assertEqual(self.app.instances[-1], i_inserted)

        # Set order to 0.5 -> should jump ahead of i1 (order 1)
        i_inserted.set_order(0.5)
        self.assertEqual(i_inserted.get_order(), 0.5)
        self.assertEqual(self.app.instances[1], i_inserted)
        self.assertEqual(self.app.instances[2], i1)

    def test_negative_order_clamped_to_zero(self):
        """Negative values are clamped to lower bound 0.0."""
        inst = gui.ProxyInstance(self.app, {"listen": "127.0.0.1:1081", "order": 5}, 1)
        self.app.instances = [inst]
        inst.set_order(-2.5)
        self.assertEqual(inst.get_order(), 0.0)


if __name__ == "__main__":
    unittest.main()
