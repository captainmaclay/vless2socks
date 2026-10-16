"""The Watcher on the real Overview (sandboxed app): button, slide-down menu with
All / flags / groups, status pills, startup and revive decisions.

Starting a proxy is only recorded by the sandbox (start_attempts), never launched."""

import ctypes
import sys
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import proxy_watcher as pw
import settings_manager
from proxy_watcher import ALL, FLAG_SYSTEM, FLAG_TELEGRAM, group_key
from tests.layout_audit import audit


class WatcherGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        s = float(cls.app.winfo_fpixels("1i")) / 96.0
        cls.app.geometry(f"{int(1280 * s)}x{int(800 * s)}+0+0")
        cls.sb.settle(20)

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        self.app.close_watcher_menu()
        self.app.watcher_targets = pw.set_targets([ALL])
        self.app.set_watcher_enabled(False)
        self.sb.start_attempts.clear()
        self.sb.settle(5)

    # helpers
    def open_menu(self):
        self.app.open_watcher_menu()
        self.sb.settle(15)
        return self.app._watcher_rows

    def click_row(self, key):
        row = self.app._watcher_rows[key]
        row.label.event_generate("<Button-1>", x=4, y=4)
        self.sb.settle(5)

    def pill_texts(self):
        return [p.cget("text") for p in self.app._watcher_pills.values()]

    # tests
    def test_button_green_when_on_gray_when_off_and_persisted(self):
        self.assertEqual(self.app.watcher_btn.cget("variant") if hasattr(self.app.watcher_btn, "cget") else None,
                         self.app.watcher_btn._variant)
        self.assertEqual(self.app.watcher_btn._variant, "secondary")
        self.app.watcher_btn.invoke()
        self.assertTrue(self.app.watcher_enabled)
        self.assertEqual(self.app.watcher_btn._variant, "success")
        self.assertEqual(self.app.watcher_arrow._variant, "success")     # the attached arrow follows
        self.assertTrue(settings_manager.get_setting(pw.ENABLED_KEY))
        self.app.watcher_btn.invoke()
        self.assertEqual(self.app.watcher_btn._variant, "secondary")
        self.assertFalse(settings_manager.get_setting(pw.ENABLED_KEY))

    def test_arrow_is_attached_to_the_button(self):
        b, a = self.app.watcher_btn, self.app.watcher_arrow
        gap = a.winfo_rootx() - (b.winfo_rootx() + b.winfo_width())
        self.assertLessEqual(gap, 2)
        self.assertEqual(a.winfo_rooty(), b.winfo_rooty())

    def test_menu_lists_all_flags_and_groups(self):
        rows = self.open_menu()
        self.assertEqual(list(rows)[:2], [ALL, FLAG_TELEGRAM])
        self.assertNotIn(FLAG_SYSTEM, rows)             # SystemProxy / WorkProxy are always watched
        for g in ("g_all", "g_fin", "g_world"):
            self.assertIn(group_key(g), rows)
        self.assertTrue(rows[ALL].checked)
        self.assertFalse(any(r.checked for k, r in rows.items() if k != ALL))
        self.assertEqual(self.app.watcher_arrow.cget("text"), "▴")

    def test_picking_groups_unchecks_all_and_back(self):
        rows = self.open_menu()
        self.click_row(group_key("g_fin"))
        self.assertFalse(rows[ALL].checked)
        self.assertTrue(rows[group_key("g_fin")].checked)
        self.click_row(FLAG_TELEGRAM)
        self.assertEqual(self.app.watcher_targets, [group_key("g_fin"), FLAG_TELEGRAM])
        self.assertEqual(pw.get_targets(), [group_key("g_fin"), FLAG_TELEGRAM])          # persisted
        self.click_row(ALL)
        self.assertTrue(rows[ALL].checked)
        self.assertFalse(rows[group_key("g_fin")].checked or rows[FLAG_TELEGRAM].checked)

    def test_menu_closes_on_outside_click_and_escape(self):
        self.open_menu()
        self.app.event_generate("<Escape>")
        self.sb.settle(5)
        self.assertFalse(self.app._watcher_menu_open)
        self.open_menu()
        self.app.watcher_arrow.invoke()                  # arrow toggles it closed
        self.assertFalse(self.app._watcher_menu_open)

    def test_menu_fits_its_rows(self):
        rows = self.open_menu()
        win = self.app.watcher_menu_win
        self.sb.settle(20)
        right = win.winfo_rootx() + win.winfo_width()
        for row in rows.values():
            self.assertLessEqual(row.label.winfo_rootx() + row.label.winfo_reqwidth(), right)
        self.assertGreaterEqual(win.winfo_height(), win.winfo_reqheight() - 2)

    def test_pills_flags_always_then_selected_groups(self):
        self.app.watcher_refresh()
        texts = self.pill_texts()
        self.assertTrue(texts[0].startswith("✈ TelegramProxy"))
        self.assertTrue(texts[1].startswith("⚙ SystemProxy"))
        self.assertTrue(texts[2].startswith("💼 WorkProxy"))
        self.assertTrue(texts[3].startswith("◉ All"))
        self.open_menu()
        self.click_row(group_key("g_fin"))
        self.click_row(group_key("g_world"))
        texts = self.pill_texts()
        self.assertEqual(len(texts), 5)
        self.assertTrue(texts[3].startswith("📁 Finland"))
        self.assertTrue(texts[4].startswith("📁 World_vless"))

    def test_pills_stay_on_the_button_row_after_the_selection_changes(self):
        self.app.watcher_targets = pw.set_targets([FLAG_TELEGRAM, group_key("g_fin")])
        self.app.watcher_refresh()
        self.sb.settle(15)
        pill = next(iter(self.app._watcher_pills.values()))
        self.assertLessEqual(abs(pill.winfo_rooty() - self.app.watcher_btn.winfo_rooty()), 6)
        # pills are packed into Flow line frames (in_=line): a shown line without pills is a leftover
        lines = [w for w in self.app.watcher_pills_frame.winfo_children()
                 if isinstance(w, tk.Frame) and w.winfo_manager() and not w.pack_slaves()]
        self.assertEqual(lines, [])

    def test_pills_are_separated_from_the_button(self):
        bar = self.app.watcher_bar
        kinds = [type(w).__name__ for w in bar.pack_slaves()]
        self.assertEqual(kinds[:2], ["Frame", "MacDivider"])        # [Watcher ▾] | pills
        pill = next(iter(self.app._watcher_pills.values()))
        self.assertGreater(pill.winfo_rootx(), self.app.watcher_arrow.winfo_rootx() + self.app.watcher_arrow.winfo_width())

    def test_startup_starts_only_watched_and_nothing_when_off(self):
        self.app.watcher_initial_start()
        self.sb.settle(5)
        self.assertEqual(self.sb.start_attempts, [])                 # off
        self.app.watcher_targets = pw.set_targets([group_key("g_fin")])
        self.app.watcher_enabled = True
        self.app.watcher_initial_start()
        self.sb.settle(30)
        # the selected group plus SystemProxy and WorkProxy, which are always watched
        fin = sorted(i.get_listen()[1] for i in self.app.instances
                     if pw.has_url(i.cfg) and ("g_fin" in i.cfg.get("groups", []) or pw.always_watched(i)))
        started = sorted(p for kind, p in self.sb.start_attempts if kind == "start_async")
        self.assertEqual(started, fin)

    def test_reconnect_only_for_watched_proxies(self):
        self.app.watcher_targets = pw.set_targets([FLAG_TELEGRAM])
        self.app.watcher_enabled = True
        tg = next(i for i in self.app.instances if i.is_telegram_proxy())
        other = next(i for i in self.app.instances
                     if not i.is_telegram_proxy() and not pw.always_watched(i) and pw.has_url(i.cfg))
        self.assertTrue(self.app.watcher_allows(tg))
        self.assertFalse(self.app.watcher_allows(other))
        for inst, expect in ((tg, True), (other, False)):
            inst._manual_stop = False
            inst._reconnect_timer_id = None
            inst._schedule_reconnect()
            self.assertEqual(inst._reconnect_timer_id is not None, expect, inst.get_display_name())
            inst._cancel_reconnect()
        self.app.watcher_enabled = False
        tg._schedule_reconnect()
        self.assertIsNone(tg._reconnect_timer_id)

    def test_tick_revives_watched_proxies_after_the_pause(self):
        now = [1000.0]
        self.app.watcher_reviver = pw.Reviver(clock=lambda: now[0])
        settings_manager.set_setting("reconnect_intervals", "10")
        self.app.watcher_targets = pw.set_targets([group_key("g_fin")])
        self.app.watcher_enabled = True
        self.app._watcher_tick()
        self.assertEqual(self.sb.start_attempts, [])                 # first sight: wait a pause
        now[0] += 10
        self.app._watcher_tick()
        self.sb.settle(30)
        fin = sorted(i.get_listen()[1] for i in self.app.instances
                     if pw.has_url(i.cfg) and ("g_fin" in i.cfg.get("groups", []) or pw.always_watched(i)))
        self.assertEqual(sorted(p for _k, p in self.sb.start_attempts), fin)

    def test_deleted_group_leaves_the_selection(self):
        import proxy_groups
        g = proxy_groups.create_group("Temp group")
        self.app.on_groups_changed()
        self.open_menu()
        self.click_row(group_key(g["id"]))
        self.assertEqual(self.app.watcher_targets, [group_key(g["id"])])
        proxy_groups.delete_group(g["id"])
        self.app.on_groups_changed()
        self.assertEqual(self.app.watcher_targets, [ALL])
        self.assertFalse(self.app._watcher_menu_open)

    def test_no_layout_problems_with_many_pills_at_narrow_width(self):
        s = float(self.app.winfo_fpixels("1i")) / 96.0
        self.app.watcher_targets = pw.set_targets([group_key("g_all"), group_key("g_fin"), group_key("g_world")])
        self.app.watcher_refresh()
        for w, h in ((1280, 800), (700, 900), (520, 560)):
            self.app.geometry(f"{int(w * s)}x{int(h * s)}+0+0")
            self.sb.settle(25)
            self.assertEqual(audit(self.app), [], f"{w}x{h}")


if __name__ == "__main__":
    unittest.main()
