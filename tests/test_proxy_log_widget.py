"""The per-proxy log must stay cheap: lines arrive in batches and the widget keeps only the tail.

An unbounded Text widget that scrolls to the end on every single line froze the whole window after
an hour of xray output.
"""

import os
import sys
import tkinter as tk
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gui
from gui import ProxyInstance


class ProxyLogWidgetTest(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.scheduled = []
        app = MagicMock()
        app.after.side_effect = lambda ms, fn=None, *args: self.scheduled.append((ms, fn))
        self.inst = ProxyInstance(app, {"url": "", "listen": "127.0.0.1:19991"}, 998)
        self.inst.frame = MagicMock()
        self.inst.log_text = tk.Text(self.root, state=tk.DISABLED)

    def tearDown(self):
        self.root.destroy()

    def widget_lines(self):
        return self.inst.log_text.get("1.0", "end-1c").splitlines()

    def test_burst_of_lines_is_one_widget_update(self):
        for i in range(300):
            self.inst._log(f"line {i}")
        self.assertEqual(len(self.scheduled), 1, "one flush for the whole burst, not one redraw per line")
        self.assertEqual(self.scheduled[0][0], gui.LOG_FLUSH_MS)
        self.scheduled.pop()[1]()
        self.assertEqual(len(self.widget_lines()), 300)

        self.inst._log("after the flush")
        self.assertEqual(len(self.scheduled), 1, "the next line schedules a new flush")

    def test_widget_keeps_only_the_tail(self):
        for batch in range(4):
            for i in range(400):
                self.inst._log(f"batch {batch} line {i}")
            self.scheduled.pop()[1]()
        lines = self.widget_lines()
        self.assertEqual(len(lines), gui.LOG_WIDGET_MAX_LINES)
        self.assertTrue(lines[-1].endswith("batch 3 line 399"))
        self.assertTrue(lines[0].endswith("batch 2 line 300"))

    def test_lines_logged_before_the_tab_is_built_are_not_lost(self):
        self.inst.log_text = None
        self.inst._log("early line")
        self.assertEqual(self.scheduled, [])
        self.assertEqual(len(self.inst.log_lines), 1)


if __name__ == "__main__":
    unittest.main()
