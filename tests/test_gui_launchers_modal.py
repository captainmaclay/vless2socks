"""Unit & Integration tests for GUI Launchers & Shortcuts guide modal dialog."""

import os
import sys
import unittest
from pathlib import Path

# Ensure application root is in python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui
import i18n


class TestLaunchersGuideModal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never a bare VlessApp(): it loaded the user's real settings / instances and adopted
        # (then killed on exit) the running proxies. The sandbox has temp files, no kill paths.
        from unittest import mock
        from tests.ui_sandbox import Sandbox
        import vless2socks.ink as ink
        # The generator writes launchers into the real C:\MyFiles\Proxy: record the call instead
        cls.generated = []
        cls._gen_patch = mock.patch.object(
            ink, "generate_proxy_workspace",
            side_effect=lambda **kw: cls.generated.append(kw) or {"proxy_dir": kw.get("target_dir")})
        cls._gen_patch.start()
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.withdraw()  # keep window hidden during tests

    @classmethod
    def tearDownClass(cls):
        try:
            cls.sb.close()
        finally:
            cls._gen_patch.stop()

    def test_options_tab_has_launchers_card_as_first_item(self):
        """Verify the launchers guide card exists in options tab."""
        self.assertTrue(hasattr(self.app, "open_launchers_guide_modal"))
        self.assertTrue(hasattr(self.app, "run_quick_launcher_generation"))

    def test_open_modal_and_verify_components(self):
        """Open the modal dialog and verify all sections, prompts, and buttons."""
        self.app.open_launchers_guide_modal()
        self.app.update()

        # Find the open Toplevel dialog
        toplevels = [w for w in self.app.winfo_children() if isinstance(w, gui.tk.Toplevel)]
        self.assertGreaterEqual(len(toplevels), 1, "Expected modal Toplevel dialog to exist")
        modal = toplevels[-1]

        # Verify title
        self.assertTrue(modal.title())

        # Recursively collect all descendant widgets
        def get_all_widgets(w):
            res = [w]
            for c in w.winfo_children():
                res.extend(get_all_widgets(c))
            return res

        descendants = get_all_widgets(modal)
        all_labels = [w.cget("text") for w in descendants if isinstance(w, gui.tk.Label)]
        all_buttons = [w.cget("text") for w in descendants if isinstance(w, gui.tk.Button)]

        # Verify architecture & prompt contents
        all_text_concat = " ".join(all_labels)
        self.assertIn("Proxy", all_text_concat)
        self.assertIn("ink", all_text_concat)
        self.assertIn("ico", all_text_concat)
        self.assertIn("vless2socks.ink", all_text_concat)
        self.assertIn("proxy-launcher-generator", all_text_concat)
        self.assertIn("Xshell", all_text_concat)

        # Verify buttons exist (Copy, Generate, Open folder, Close)
        button_texts = " ".join(all_buttons)
        self.assertTrue(any("копир" in b.lower() or "copy" in b.lower() for b in all_buttons))
        self.assertTrue(any("Сгенерировать" in b or "Generate" in b for b in all_buttons))
        self.assertTrue(any("Открыть" in b or "Open" in b for b in all_buttons))

        # Find first copy button and invoke it
        copy_buttons = [b for b in descendants if isinstance(b, gui.tk.Button) and ("копир" in b.cget("text").lower() or "copy" in b.cget("text").lower())]
        self.assertGreaterEqual(len(copy_buttons), 1)
        copy_buttons[0].invoke()
        self.app.update()

        # Check clipboard content
        clipboard_content = self.app.clipboard_get()
        self.assertIn("vless2socks.ink", clipboard_content)
        self.assertIn("proxy-launcher-generator", clipboard_content)

        # Destroy modal cleanly
        modal.destroy()
        self.app.update()

    def test_run_quick_launcher_generation_direct(self):
        """Verify run_quick_launcher_generation runs without raising unhandled exceptions."""
        import tkinter as tk
        dummy_lbl = tk.Label(self.app, text="ready")
        self.app.run_quick_launcher_generation(status_lbl=dummy_lbl)
        import time
        deadline = time.monotonic() + 5
        while not self.generated and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.02)
        self.assertEqual(len(self.generated), 1)                  # the (stubbed) generator was called
        self.assertEqual(self.sb.kill_attempts, [])


if __name__ == "__main__":
    unittest.main()
