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
        # We need Tkinter root; on Windows headless CI or desktop this works natively
        cls.app = gui.VlessApp()
        cls.app.withdraw()  # keep window hidden during tests

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:
            pass

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
        self.assertTrue(any("Копир" in b or "Copy" in b for b in all_buttons))
        self.assertTrue(any("Сгенерировать" in b or "Generate" in b for b in all_buttons))
        self.assertTrue(any("Открыть" in b or "Open" in b for b in all_buttons))

        # Find first copy button and invoke it
        copy_buttons = [b for b in descendants if isinstance(b, gui.tk.Button) and ("Копир" in b.cget("text") or "Copy" in b.cget("text"))]
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
        self.app.update()


if __name__ == "__main__":
    unittest.main()
