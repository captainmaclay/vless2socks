"""The five window layouts: nothing clipped, overlapping or squeezed at any of them.

Runs the real VlessApp in a sandbox (temp settings, no services) at each preset size and
at the edges of every width range, in every Overview state and on the Options tab.
"""

import ctypes
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:   # same DPI mode as the real GUI: sizes are checked in physical pixels at the real scale
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import responsive
from tests.layout_audit import audit, audit_overview
from tests.ui_sandbox import Sandbox

GROUPS = [{"id": "g_all", "name": "ALL"}, {"id": "g_fin", "name": "Finland"},
          {"id": "g_world", "name": "World_vless"}, {"id": "g_long", "name": "Резервные серверы Европы"},
          {"id": "g_x", "name": "Telegram"}, {"id": "g_y", "name": "Работа и офис VPN"}]

# (layout, sizes in DIP): the preset and the edges of the layout's width range
CASES = [
    (responsive.MINI, [(520, 560), (560, 920), (699, 900)]),
    (responsive.SNAP, [(700, 900), (768, 920), (899, 940)]),
    (responsive.COMPACT, [(760, 600), (900, 680), (1099, 760)]),
    (responsive.STANDARD, [(1100, 700), (1280, 800), (1439, 900)]),
    (responsive.WIDE, [(1440, 800), (1536, 864)]),
]


class TestClassify(unittest.TestCase):
    def test_presets_land_in_their_own_layout(self):
        for lay in responsive.LAYOUTS:
            self.assertEqual(responsive.classify(*lay.preset), lay.key, lay)

    def test_presets_inside_the_allowed_proportions_and_minimum(self):
        lo, hi = responsive.ASPECT_RANGE
        for lay in responsive.LAYOUTS:
            w, h = lay.preset
            self.assertTrue(lo <= w / h <= hi, lay)
            self.assertGreaterEqual(w, responsive.MIN_SIZE[0])
            self.assertGreaterEqual(h, responsive.MIN_SIZE[1])

    def test_snap_halves_and_maximized_screens_allowed(self):
        lo, hi = responsive.ASPECT_RANGE
        for w, h in [(768, 1032), (960, 1040), (1536, 960), (1920, 1080), (2560, 1080), (3440, 1440)]:
            self.assertTrue(lo <= w / h <= hi, (w, h))

    def test_preset_shrinks_to_small_screens(self):
        self.assertEqual(responsive.fit_preset(responsive.WIDE, 1280, 720), (1240, 680))


class TestLayouts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(language="ru", settings={"proxy_groups": GROUPS})
        cls.app = cls.sb.app
        cls.scale = float(cls.app.winfo_fpixels("1i")) / 96.0

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        # GUI tests run on a real desktop: make sure the window is shown and not minimized
        if self.app.state() != "normal":
            self.app.state("normal")
        self.app.lift()

    def resize(self, w, h):
        s = self.scale
        self.app.geometry(f"{int(w * s)}x{int(h * s)}+0+0")
        self.sb.settle(25)

    def issues(self):
        self.sb.settle(25)
        return audit(self.app) + audit_overview(self.app.overview_view)

    def check_overview_states(self, label):
        app = self.app
        app.nav_notebook.select(app.overview_frame)
        try:
            app.set_overview_view_mode("list")
            self.assertEqual(self.issues(), [], f"{label}: list")
            app.set_overview_view_mode("groups")
            self.assertEqual(self.issues(), [], f"{label}: by groups")
            app.open_group_tabs([g["id"] for g in GROUPS])
            self.assertEqual(self.issues(), [], f"{label}: all group tabs open")
            app.toggle_selection_mode()
            app.select_all_proxies()
            self.assertEqual(self.issues(), [], f"{label}: selection mode")
        finally:
            if app._selection_mode:
                app.toggle_selection_mode()
            for g in GROUPS:
                app.close_group_tab(g["id"])
            app.set_overview_view_mode("list")

    def test_every_layout_every_state(self):
        for key, sizes in CASES:
            for w, h in sizes:
                with self.subTest(layout=key, size=f"{w}x{h}"):
                    self.resize(w, h)
                    self.assertEqual(self.app.current_layout()[0], key)
                    self.check_overview_states(f"{key} {w}x{h}")

    def test_both_languages_at_the_narrowest_width(self):
        for lang in ("en", "ru"):
            with self.subTest(lang=lang):
                self.app._apply_language(lang)
                self.resize(*responsive.MIN_SIZE)
                self.check_overview_states(f"{lang} min")

    def test_other_tabs_at_every_preset(self):
        app = self.app
        try:
            for name in ("options_frame", "backup_frame", "wsl_iso_frame", "loc_frame"):
                app.nav_notebook.select(getattr(app, name))
                for lay in responsive.LAYOUTS:
                    with self.subTest(tab=name, layout=lay.key):
                        self.resize(*lay.preset)
                        self.assertEqual(self.issues(), [])
                with self.subTest(tab=name, layout="minimum"):
                    self.resize(*responsive.MIN_SIZE)
                    self.assertEqual(self.issues(), [])
        finally:
            app.nav_notebook.select(app.overview_frame)

    def test_scrolling_tabs_follow_the_window_width(self):
        app = self.app
        try:
            for name in ("options_frame", "backup_frame", "wsl_iso_frame"):
                app.nav_notebook.select(getattr(app, name))
                self.resize(1280, 800)
                canvas = next(c for c in getattr(app, name).winfo_children() if getattr(c, "is_scroll_host", False))
                content = canvas.winfo_children()[0]
                self.assertGreaterEqual(content.winfo_width(), canvas.winfo_width() - 2, name)
        finally:
            app.nav_notebook.select(app.overview_frame)

    def test_cards_rearrange_instead_of_cutting(self):
        self.resize(1536, 864)
        self.assertEqual(self.app.overview_view.density, (False, False))   # full labels beside the text
        self.resize(700, 900)
        self.assertEqual(self.app.overview_view.density, (True, False))    # icon buttons
        self.resize(520, 560)
        self.assertTrue(self.app.overview_view.density[1])                 # buttons on their own line

    def test_hidden_controls_come_back_in_place(self):
        """The app badge hides at the narrowest level; coming back it must not land
        after the counter (pack appends to the end of the row)."""
        top = self.app.top_bar
        order = lambda: [w for w in top.pack_slaves() if w in (self.app.title_badge, self.app.count_label)]
        self.resize(1280, 800)
        self.assertEqual(order(), [self.app.title_badge, self.app.count_label])
        row = self.app._adaptive_rows()["top"]
        row.fit(1)                       # force the icon level: badge hidden
        self.assertFalse(self.app.title_badge.winfo_manager())
        self.app._relayout()
        self.assertEqual(order(), [self.app.title_badge, self.app.count_label])

    def test_presets_resize_the_window(self):
        for lay in responsive.LAYOUTS:
            with self.subTest(layout=lay.key):
                self.app.apply_layout_preset(lay.key)
                sw = round(self.app.winfo_screenwidth() / self.scale)
                sh = round(self.app.winfo_screenheight() / self.scale)
                ew, eh = responsive.fit_preset(lay.key, sw, sh)
                # the window manager applies a new size with a delay that varies on a busy
                # desktop: wait for it (up to 3 s) instead of a fixed number of rounds
                deadline = time.monotonic() + 3
                while True:
                    self.sb.settle(5)
                    key, w, h = self.app.current_layout()
                    if (abs(w - ew) <= 2 and abs(h - eh) <= 2) or time.monotonic() > deadline:
                        break
                self.assertLessEqual(abs(w - ew), 2)
                self.assertLessEqual(abs(h - eh), 2)
                if (ew, eh) == lay.preset:
                    self.assertEqual(key, lay.key)

    def test_sandbox_never_touches_real_processes(self):
        """The real vless2socks on this machine listens on 1015 (the built-in System
        Proxy): the sandboxed app must not see it as its own nor try to kill anything."""
        self.assertFalse(any(inst.running for inst in self.app.instances))
        self.assertEqual(self.sb.kill_attempts, [])

    def test_minimum_size_is_logical(self):
        s = self.scale
        mw, mh = self.app.minsize()
        self.assertEqual((mw, mh), (int(responsive.MIN_SIZE[0] * s), int(responsive.MIN_SIZE[1] * s)))
        self.app.geometry(f"{int(200 * s)}x{int(200 * s)}+0+0")
        self.sb.settle(25)
        self.assertGreaterEqual(self.app.winfo_width(), mw - 1)


if __name__ == "__main__":
    unittest.main()
