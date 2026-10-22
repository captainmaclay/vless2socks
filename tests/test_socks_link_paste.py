"""Adding a SOCKS5 upstream by pasting a link: strict link parsing and the "Configure
SOCKS5" choice window (1. paste a link with a grey example, 2. the field editor)."""

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests  # noqa: F401  (isolated data paths)
from vless2socks.url import ConfigError, parse_socks_link

# documentation-range addresses only, never a real server
LINK = "socks5://WxUser:Pa55w0rd@203.0.113.59:8000"


class ParseSocksLinkTest(unittest.TestCase):
    def test_full_link(self):
        srv = parse_socks_link(LINK)
        self.assertEqual((srv.address, srv.port, srv.username, srv.password),
                         ("203.0.113.59", 8000, "WxUser", "Pa55w0rd"))
        self.assertEqual(srv.to_url(), LINK)

    def test_spaces_quotes_and_socks5h(self):
        srv = parse_socks_link('  "socks5h://u:p@198.51.100.7:1080"  ')
        self.assertEqual((srv.address, srv.port, srv.username), ("198.51.100.7", 1080, "u"))

    def test_without_credentials_and_with_remark(self):
        srv = parse_socks_link("socks5://198.51.100.7:1080#Latvia")
        self.assertEqual((srv.username, srv.password, srv.remark), ("", "", "Latvia"))

    def test_bad_links(self):
        for bad in ("", "   ", "vless://x@h:1", "socks5://user:pass@203.0.113.1",
                    "socks5://user:pass@:8000", "socks5://u:p@203.0.113.1:99999",
                    "socks5://u:p@203.0.113.1:port"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_socks_link(bad)


class SocksChoiceWindowTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception:
            self.skipTest("Tkinter display not available")
        import gui
        self.gui = gui
        app = self.root
        app.instances = []
        app.save_all = mock.Mock()
        app.refresh_current_page_tabs = lambda: None
        app.refresh_overview = lambda: None
        app.update_tray_icon = lambda: None
        self.inst = gui.ProxyInstance(app, {"url": "", "listen": "127.0.0.1:1081"}, 0)
        self.inst.build_tab_ui(ttk.Notebook(self.root))
        self.inst.proto_var.set("socks5")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def open(self, clipboard=None):
        side = (lambda: clipboard) if clipboard is not None else mock.Mock(side_effect=tk.TclError("empty"))
        with mock.patch.object(self.root, "clipboard_get", side_effect=side):
            self.inst.open_socks_dialog()
        dlg = self.inst._socks_choice_dlg
        self.root.deiconify()                  # Tk does not deliver mouse events to hidden windows
        dlg.deiconify()
        dlg.update()
        return dlg

    def test_empty_field_shows_grey_example(self):
        self.open()
        ent = self.inst._socks_link_entry
        self.assertEqual(ent.get(), self.gui.SOCKS_LINK_PLACEHOLDER)
        self.assertEqual(ent.cget("fg"), self.gui.C["muted"])
        self.assertEqual(self.inst._socks_link_text(), "")        # the example is not a value

    def test_pasting_a_link_applies_it(self):
        dlg = self.open()
        ent = self.inst._socks_link_entry
        ent.event_generate("<Button-1>", x=5, y=5)                # the user clicks into the field
        self.assertEqual(ent.get(), "")                           # the example is gone
        ent.insert(0, LINK)
        ent.event_generate("<Return>")
        self.root.update_idletasks()
        self.assertFalse(dlg.winfo_exists())
        self.assertEqual(self.inst.cfg["url"], LINK)
        self.assertEqual(self.inst.proto_var.get(), "socks5")
        self.assertIn("203.0.113.59:8000", self.inst._format_socks_summary())
        self.root.save_all.assert_called()

    def test_bad_link_keeps_the_window_with_an_error(self):
        dlg = self.open()
        ent = self.inst._socks_link_entry
        ent.event_generate("<Button-1>", x=5, y=5)
        ent.insert(0, "socks5://user:pass@203.0.113.1")           # no port
        ent.event_generate("<Return>")
        self.assertTrue(dlg.winfo_exists())
        self.assertEqual(self.inst._socks_link_text(), "socks5://user:pass@203.0.113.1")
        self.assertIn("не указан порт", self.inst._socks_link_error.cget("text"))
        self.assertEqual(self.inst.cfg["url"], "")

    def test_text_added_next_to_the_example_still_reads(self):
        self.open()
        ent = self.inst._socks_link_entry
        ent.insert(tk.END, LINK)                                  # e.g. a paste that skipped the clear
        self.assertEqual(self.inst._socks_link_text(), LINK)

    def test_link_on_the_clipboard_is_offered(self):
        self.open(clipboard=LINK)
        self.assertEqual(self.inst._socks_link_text(), LINK)

    def test_second_choice_opens_the_field_editor(self):
        dlg = self.open()
        with mock.patch.object(self.inst, "open_socks_manual_dialog") as manual:
            button = next(w for w in _walk(dlg) if isinstance(w, tk.Button)
                          and w.cget("text") == self.gui.t("btn_socks_manual"))
            button.invoke()
        manual.assert_called_once()
        self.assertFalse(dlg.winfo_exists())


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


if __name__ == "__main__":
    unittest.main()
