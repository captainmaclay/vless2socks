"""Proxy groups: storage/partition logic and the Overview + groups manager GUI.

GUI tests use a light Tk app that borrows the real VlessApp group methods, so no
tray, IPC server, watchers or proxy autostart are touched, and settings go to a
temporary file instead of the real settings.json.
"""

import gc
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui
import proxy_groups
import settings_manager


def _release_tk(test):
    """Free a test's Tk root on the main thread. Left to the cyclic GC, it could be
    collected inside some worker thread, and Tcl aborts the process when an
    interpreter is deleted outside the thread that created it."""
    test.app = None
    gc.collect()


class _TempSettingsMixin:
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="vless_groups_test_"))
        self.orig_settings = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp_dir / "settings.json"

    def tearDown(self):
        settings_manager.SETTINGS_FILE = self.orig_settings
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestProxyGroupsStorage(_TempSettingsMixin, unittest.TestCase):
    def test_create_rename_delete_roundtrip(self):
        a = proxy_groups.create_group("  Work   proxies ")
        b = proxy_groups.create_group("Scraping")
        self.assertEqual(a["name"], "Work proxies")
        self.assertEqual([g["name"] for g in proxy_groups.load_groups()], ["Work proxies", "Scraping"])

        proxy_groups.rename_group(a["id"], "Office")
        self.assertEqual(proxy_groups.get_group(a["id"])["name"], "Office")

        self.assertTrue(proxy_groups.delete_group(a["id"]))
        self.assertFalse(proxy_groups.delete_group(a["id"]))
        self.assertEqual(proxy_groups.load_groups(), [b])

    def test_name_validation(self):
        proxy_groups.create_group("Avito")
        with self.assertRaises(proxy_groups.GroupNameError) as ctx:
            proxy_groups.create_group("   ")
        self.assertEqual(ctx.exception.code, "empty")
        with self.assertRaises(proxy_groups.GroupNameError) as ctx:
            proxy_groups.create_group("avito")
        self.assertEqual(ctx.exception.code, "taken")

        other = proxy_groups.create_group("Other")
        proxy_groups.rename_group(other["id"], "OTHER")  # same group, new case: allowed
        with self.assertRaises(proxy_groups.GroupNameError):
            proxy_groups.rename_group(other["id"], "AVITO")
        self.assertEqual(len(proxy_groups.create_group("x" * 100)["name"]), proxy_groups.MAX_NAME_LEN)

    def test_malformed_settings_are_ignored(self):
        settings_manager.set_setting(proxy_groups.SETTINGS_KEY, [
            {"id": "g1", "name": "One"}, "junk", {"name": "no id"}, {"id": "g1", "name": "dup"}, {"id": "g2"},
        ])
        self.assertEqual(proxy_groups.load_groups(), [{"id": "g1", "name": "One"}, {"id": "g2", "name": "g2"}])

    def test_partition_order_and_unsorted(self):
        groups = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"}]
        items = [("p1", "b"), ("p2", None), ("p3", "a"), ("p4", "gone"), ("p5", "b")]
        sections = proxy_groups.partition(items, groups, lambda it: [it[1]] if it[1] else [])
        self.assertEqual(
            [(g["id"] if g else None, [it[0] for it in members]) for g, members in sections],
            [("a", ["p3"]), ("b", ["p1", "p5"]), ("c", []), (None, ["p2", "p4"])],
        )

    def test_partition_unsorted_presence(self):
        groups = [{"id": "a", "name": "A"}]
        self.assertEqual(proxy_groups.partition([1], groups, lambda _: ["a"])[-1][0], groups[0])
        self.assertEqual(proxy_groups.partition([1], [], lambda _: []), [(None, [1])])

    def test_group_ids_of_reads_list_and_legacy_key(self):
        self.assertEqual(proxy_groups.group_ids_of({}), [])
        self.assertEqual(proxy_groups.group_ids_of({"group": "g1"}), ["g1"])            # old single key
        self.assertEqual(proxy_groups.group_ids_of({"groups": ["g2", "g3", "g2"]}), ["g2", "g3"])
        self.assertEqual(proxy_groups.group_ids_of({"group": "g1", "groups": ["g2"]}), ["g1", "g2"])
        self.assertEqual(proxy_groups.group_ids_of({"groups": ["g1", "g2"]}, known_ids={"g2"}), ["g2"])

    def test_add_remove_refs_migrate_legacy(self):
        cfg = {"group": "g1"}
        self.assertTrue(proxy_groups.add_group_ref(cfg, "g2"))
        self.assertEqual(cfg, {"groups": ["g1", "g2"]})
        self.assertFalse(proxy_groups.add_group_ref(cfg, "g2"))
        self.assertTrue(proxy_groups.remove_group_ref(cfg, "g1"))
        self.assertEqual(cfg, {"groups": ["g2"]})
        self.assertTrue(proxy_groups.remove_group_ref(cfg, "g2"))
        self.assertEqual(cfg, {})

    def test_partition_puts_shared_item_in_each_group(self):
        groups = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        sections = proxy_groups.partition(["x", "y"], groups, lambda it: ["a", "b"] if it == "x" else [])
        self.assertEqual([(g["id"] if g else None, m) for g, m in sections],
                         [("a", ["x"]), ("b", ["x"]), (None, ["y"])])
        self.assertEqual(proxy_groups.member_counts([{"groups": ["a", "b"]}, {"group": "a"}]), {"a": 2, "b": 1})

    def test_paging(self):
        items = list(range(23))
        self.assertEqual(proxy_groups.page_count(0), 1)
        self.assertEqual(proxy_groups.page_count(10), 1)
        self.assertEqual(proxy_groups.page_count(11), 2)
        self.assertEqual(proxy_groups.page_slice(items, 2), (list(range(20, 23)), 2))
        self.assertEqual(proxy_groups.page_slice(items, 99)[1], 2)
        self.assertEqual(proxy_groups.page_slice(items, -1)[1], 0)

    def test_view_mode_persists(self):
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.VIEW_LIST)
        proxy_groups.set_view_mode(proxy_groups.VIEW_GROUPS)
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.VIEW_GROUPS)
        proxy_groups.set_view_mode("bogus")
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.VIEW_LIST)


_BORROWED = [
    "refresh_overview", "_render_overview", "_card_model", "_header_model", "update_overview_card",
    "_style_view_mode_buttons", "set_overview_view_mode", "_sync_selection_bar", "_open_group_menu",
    "_show_group_name_error", "prompt_new_group", "prompt_rename_group", "on_groups_changed",
    "open_groups_manager", "_ask_choice", "delete_group_flow", "_remove_instances",
    "_style_select_mode_button", "toggle_selection_mode", "_on_card_selected", "select_all_proxies",
    "clear_proxy_selection", "toggle_group_selection", "_sync_selection_ui", "selected_proxies",
    "add_selected_to_group", "_open_bulk_group_menu", "add_selected_to_new_group", "suggest_group_name",
    "_flash_selection_info", "_flush_card_updates",
    "_rebuild_group_tabs", "open_group_tabs", "close_group_tab", "open_group_tab_picker",
]


class _GroupsTestApp(tk.Tk):
    """Tk root with the real VlessApp group/overview methods and no background services."""

    def __init__(self):
        super().__init__()
        self.withdraw()
        self.instances = []
        self.saved = 0
        self.from_threads = []
        self.overview_view_mode = proxy_groups.VIEW_LIST
        self.overview_canvas = tk.Canvas(self, width=900, height=600)
        self.overview_canvas.pack()
        self.overview_view = gui.OverviewView(self.overview_canvas, gui.C)
        self.selection_count_lbl = tk.Label(self)
        self.bulk_group_btn = tk.Button(self)

    def after(self, ms, func=None, *args):
        # Same contract as VlessApp.after: worker threads queue, the GUI thread schedules
        if func is not None and gui.threading.current_thread() is not gui.threading.main_thread():
            self.from_threads.append((func, args))
            return None
        return super().after(ms, func, *args)

    def save_all(self):
        self.saved += 1

    def refresh_current_page_tabs(self):
        pass

    def sort_instances(self):
        pass

    def _update_header_stats(self):
        pass

    def update_tray_icon(self):
        pass

    def _update_telegram_dispatcher_pool(self):
        pass

    def _log(self, msg):
        pass


for _name in _BORROWED:
    setattr(_GroupsTestApp, _name, getattr(gui.VlessApp, _name))


def _texts(app):
    """Texts drawn on the Overview, after pending (coalesced) redraws."""
    app.update_idletasks()
    return app.overview_view.texts()


def _buttons(app, name, key="__any__"):
    """Tags of drawn Overview buttons, top to bottom."""
    app.update_idletasks()
    view = app.overview_view
    tags = view.buttons(name, key)
    return sorted(tags, key=lambda tg: view.canvas.coords(view.canvas.find_withtag(tg)[0])[1])


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


class TestProxyGroupsGui(_TempSettingsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = _GroupsTestApp()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        _release_tk(self)
        super().tearDown()

    def _add_proxy(self, port, name, group=None):
        cfg = {"name": name, "url": "", "listen": f"127.0.0.1:{port}", "order": port}
        if group:
            cfg["group"] = group
        inst = gui.ProxyInstance(self.app, cfg, len(self.app.instances))
        self.app.instances.append(inst)
        return inst

    def test_grouped_view_sections_order_and_spacing(self):
        a = proxy_groups.create_group("Alpha")
        proxy_groups.create_group("Beta")
        self._add_proxy(1081, "loose")
        self._add_proxy(1082, "in-alpha", a["id"])
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.refresh_overview()
        flat = _texts(self.app)
        order = [t for t in flat if t in ("Alpha", "Beta", "Unsorted", "in-alpha", "loose")]
        self.assertEqual(order, ["Alpha", "in-alpha", "Beta", "Unsorted", "loose"])
        self.assertIn(gui.t("group_empty_hint"), flat)

        # first header at the top, a gap before every following group
        view = self.app.overview_view
        heads = view.header_slots
        a_y, b_y, u_y = heads[a["id"]][0], heads[[g for g in heads if g not in (a["id"], None)][0]][0], heads[None][0]
        self.assertEqual(a_y, view.FIRST_HEADER_GAP)
        card_end = view.card_slots[self.app.instances[1]][0][0] + view.CARD_H + view.CARD_GAP
        self.assertEqual(b_y, card_end + view.HEADER_GAP)
        # Beta's empty-group hint row sits between Beta and Unsorted
        self.assertEqual(u_y, b_y + view.HEADER_H + view.FIRST_HEADER_GAP + view.HINT_H + view.HEADER_GAP)

    def test_grouped_view_shows_groups_without_any_proxies(self):
        proxy_groups.create_group("Alpha")
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.refresh_overview()
        texts = _texts(self.app)
        self.assertIn("Alpha", texts)
        self.assertNotIn(gui.t("empty_overview"), texts)

    def test_list_view_shows_group_badge(self):
        a = proxy_groups.create_group("Alpha")
        self._add_proxy(1081, "in-alpha", a["id"])
        self.app.refresh_overview()
        self.assertIn(" 📁 Alpha ", _texts(self.app))

    def test_inline_rename_on_main_page(self):
        a = proxy_groups.create_group("Alpha")
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.refresh_overview()
        self.app.deiconify()
        self.app.update()

        self.app.overview_view.invoke(_buttons(self.app, "rename", a["id"])[0])
        entry = self.app.overview_view.editor
        entry.delete(0, tk.END)
        entry.insert(0, "Renamed")
        entry.focus_force()
        self.app.update()
        entry.event_generate("<Return>")
        self.app.update()

        self.assertEqual(proxy_groups.get_group(a["id"])["name"], "Renamed")
        self.assertIn("Renamed", _texts(self.app))
        self.assertIsNone(self.app.overview_view.editor)

    def test_inline_rename_duplicate_shows_error_once(self):
        a = proxy_groups.create_group("Alpha")
        proxy_groups.create_group("Beta")
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.refresh_overview()
        self.app.deiconify()
        self.app.update()

        self.app.overview_view.invoke(_buttons(self.app, "rename", a["id"])[0])
        entry = self.app.overview_view.editor
        entry.delete(0, tk.END)
        entry.insert(0, "beta")
        entry.focus_force()
        self.app.update()
        with mock.patch.object(gui.messagebox, "showerror") as err:
            entry.event_generate("<Return>")
            entry.event_generate("<FocusOut>")
            self.app.update()
        self.assertEqual(err.call_count, 1)  # Enter: error; the following blur just cancels
        self.assertEqual(proxy_groups.get_group(a["id"])["name"], "Alpha")
        self.assertIsNone(self.app.overview_view.editor)

    def _open_dialog(self):
        self.app.open_groups_manager()
        self.app.update()
        return self.app._groups_dialog

    def _row_names(self, dlg):
        # the groups manager is a regular widget dialog
        return [w.cget("text") for w in _walk(dlg.rows_frame)
                if isinstance(w, tk.Label) and w.cget("text").startswith("📁 ")]

    def test_manager_small_list_has_no_pager_and_is_compact(self):
        for i in range(3):
            proxy_groups.create_group(f"G{i}")
        dlg = self._open_dialog()
        self.assertEqual(self._row_names(dlg), ["📁 G0", "📁 G1", "📁 G2"])
        self.assertEqual(dlg.pager.winfo_manager(), "")
        small_h = dlg.winfo_reqheight()
        dlg.destroy()

        for i in range(3, 10):
            proxy_groups.create_group(f"G{i}")
        dlg = self._open_dialog()
        self.assertEqual(len(self._row_names(dlg)), 10)
        self.assertEqual(dlg.pager.winfo_manager(), "")  # 10 rows = still one page
        self.assertLess(small_h, dlg.winfo_reqheight())
        dlg.destroy()

    def test_manager_paginates_by_ten(self):
        for i in range(12):
            proxy_groups.create_group(f"G{i:02d}")
        dlg = self._open_dialog()
        self.assertEqual(len(self._row_names(dlg)), 10)
        self.assertEqual(dlg.pager.winfo_manager(), "pack")
        self.assertEqual(dlg.page_lbl.cget("text"), gui.t("groups_page_info", page=1, pages=2))
        self.assertEqual(str(dlg.prev_btn.cget("state")), tk.DISABLED)

        dlg.next_btn.invoke()
        self.app.update()
        self.assertEqual(self._row_names(dlg), ["📁 G10", "📁 G11"])
        self.assertEqual(str(dlg.next_btn.cget("state")), tk.DISABLED)
        dlg.destroy()

    def test_delete_only_section_moves_proxies_to_unsorted(self):
        a = proxy_groups.create_group("Alpha")
        p = self._add_proxy(1081, "in-alpha", a["id"])
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="only_group"):
            self.assertTrue(self.app.delete_group_flow(a, self.app))
        self.assertEqual(proxy_groups.load_groups(), [])
        self.assertIn(p, self.app.instances)
        self.assertNotIn("group", p.cfg)
        self.assertGreater(self.app.saved, 0)

    def test_delete_with_proxies_needs_second_confirmation(self):
        a = proxy_groups.create_group("Alpha")
        p = self._add_proxy(1081, "in-alpha", a["id"])
        keep = self._add_proxy(1082, "loose")

        # Second "are you sure, proxies will be lost" answered No: nothing changes
        with mock.patch.object(gui.messagebox, "askyesno", side_effect=[True, False]) as ask, \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="with_proxies"):
            self.assertFalse(self.app.delete_group_flow(a, self.app))
        self.assertEqual(ask.call_count, 2)
        self.assertIn("in-alpha", ask.call_args_list[1].args[1])
        self.assertEqual(len(proxy_groups.load_groups()), 1)
        self.assertIn(p, self.app.instances)

        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="with_proxies"), \
                mock.patch.object(gui.ProxyInstance, "destroy") as destroy:
            self.assertTrue(self.app.delete_group_flow(a, self.app))
            for th in list(gui.threading.enumerate()):
                if th.name == "remove-group-proxies":
                    th.join(5)
        self.assertEqual(proxy_groups.load_groups(), [])
        self.assertEqual(self.app.instances, [keep])
        destroy.assert_called_once()
        self.assertEqual(len(self.app.from_threads), 2)  # tray + telegram pool refresh, via GUI thread

    def test_delete_with_proxies_keeps_proxies_shared_with_other_groups(self):
        a = proxy_groups.create_group("Alpha")
        b = proxy_groups.create_group("Beta")
        only_a = self._add_proxy(1081, "only-alpha", a["id"])
        shared = self._add_proxy(1082, "shared", a["id"])
        proxy_groups.add_group_ref(shared.cfg, b["id"])
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True) as ask, \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="with_proxies"), \
                mock.patch.object(gui.ProxyInstance, "destroy") as destroy:
            self.assertTrue(self.app.delete_group_flow(a, self.app))
            for th in list(gui.threading.enumerate()):
                if th.name == "remove-group-proxies":
                    th.join(5)
        warning = ask.call_args_list[1].args[1]
        self.assertIn("only-alpha", warning)
        self.assertNotIn("• shared", warning)
        self.assertIn(gui.t("del_group_shared_kept", count=1), warning)
        self.assertEqual(self.app.instances, [shared])
        self.assertEqual(shared.get_group_ids(), [b["id"]])
        destroy.assert_called_once()

    def test_delete_only_section_keeps_other_memberships(self):
        a = proxy_groups.create_group("Alpha")
        b = proxy_groups.create_group("Beta")
        shared = self._add_proxy(1082, "shared", a["id"])
        proxy_groups.add_group_ref(shared.cfg, b["id"])
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="only_group"):
            self.assertTrue(self.app.delete_group_flow(a, self.app))
        self.assertEqual(shared.get_group_ids(), [b["id"]])

    def test_delete_cancelled_at_first_question(self):
        a = proxy_groups.create_group("Alpha")
        with mock.patch.object(gui.messagebox, "askyesno", return_value=False), \
                mock.patch.object(_GroupsTestApp, "_ask_choice") as choice:
            self.assertFalse(self.app.delete_group_flow(a, self.app))
        choice.assert_not_called()
        self.assertEqual(len(proxy_groups.load_groups()), 1)

    def test_group_membership_persists_in_config(self):
        a = proxy_groups.create_group("Alpha")
        b = proxy_groups.create_group("Beta")
        p = self._add_proxy(1081, "x")
        p.set_group_membership(a["id"], True)
        p.set_group_membership(b["id"], True)
        self.assertEqual(p.get_config()["groups"], [a["id"], b["id"]])
        p.set_group_membership(a["id"], False)
        self.assertEqual(p.get_config()["groups"], [b["id"]])
        p.clear_groups()
        self.assertNotIn("groups", p.get_config())
        self.assertNotIn("group", p.get_config())



class TestOverviewSelectionMode(_TempSettingsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = _GroupsTestApp()
        self.a = proxy_groups.create_group("Alpha")
        self.p1 = self._add(1081, "p1", self.a["id"])
        self.p2 = self._add(1082, "p2", self.a["id"])
        self.p3 = self._add(1083, "p3")
        self.app.refresh_overview()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        _release_tk(self)
        super().tearDown()

    def _add(self, port, name, group=None):
        cfg = {"name": name, "url": "", "listen": f"127.0.0.1:{port}", "order": port}
        if group:
            cfg["group"] = group
        inst = gui.ProxyInstance(self.app, cfg, len(self.app.instances))
        self.app.instances.append(inst)
        return inst

    def _checkboxes(self):
        return _buttons(self.app, "check")

    def _group_buttons(self):
        return _buttons(self.app, "select_group")

    def _checked(self):
        return [all(self.app.overview_view.card_checked(i)) for i in self.app.instances]

    def test_checkboxes_appear_only_in_selection_mode(self):
        self.assertEqual(self._checkboxes(), [])
        self.app.toggle_selection_mode()
        self.assertEqual(len(self._checkboxes()), 3)
        self.app.select_all_proxies()
        self.app.toggle_selection_mode()
        self.assertEqual(self._checkboxes(), [])
        self.assertEqual(self.app._selected, set())  # leaving the mode drops the selection

    def test_select_all_clear_and_counter(self):
        self.app.toggle_selection_mode()
        self.assertEqual(str(self.app.bulk_group_btn.cget("state")), tk.DISABLED)
        self.app.select_all_proxies()
        self.assertEqual(self._checked(), [True, True, True])
        self.assertEqual(self.app.selection_count_lbl.cget("text"), gui.t("lbl_selected", count=3))
        self.assertEqual(str(self.app.bulk_group_btn.cget("state")), tk.NORMAL)
        self.app.clear_proxy_selection()
        self.assertEqual(self._checked(), [False, False, False])
        self.assertEqual(self.app.selection_count_lbl.cget("text"), gui.t("lbl_selected", count=0))
        self.assertEqual(str(self.app.bulk_group_btn.cget("state")), tk.DISABLED)

    def test_clicking_checkbox_toggles_single_proxy(self):
        self.app.toggle_selection_mode()
        view = self.app.overview_view
        view.invoke(_buttons(self.app, "check", self.p3)[0])
        self.assertEqual(self.app.selected_proxies(), [self.p3])
        self.assertEqual(view.card_checked(self.p3), [True])
        view.invoke(_buttons(self.app, "check", self.p3)[0])   # redrawn: look the box up again
        self.assertEqual(self.app.selected_proxies(), [])

    def test_select_group_button_only_in_grouped_selection_mode(self):
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.refresh_overview()
        self.assertEqual(self._group_buttons(), [])

        self.app.toggle_selection_mode()
        view = self.app.overview_view
        self.assertEqual(len(self._group_buttons()), 2)  # Alpha and Unsorted
        view.invoke(self._group_buttons()[0])
        self.assertEqual(self.app.selected_proxies(), [self.p1, self.p2])
        alpha_btn, unsorted_btn = self._group_buttons()
        self.assertEqual(view.button_text(alpha_btn), gui.t("btn_unselect_group"))
        self.assertEqual(view.button_text(unsorted_btn), gui.t("btn_select_group"))
        view.invoke(alpha_btn)
        self.assertEqual(self.app.selected_proxies(), [])

    def test_add_to_group_copies_references_not_proxies(self):
        beta = proxy_groups.create_group("Beta")
        self.app.toggle_selection_mode()
        self.app._selected = {self.p1, self.p3}
        before = [(id(i), i.get_listen()) for i in self.app.instances]
        saves = self.app.saved
        self.assertEqual(self.app.add_selected_to_group(beta["id"]), (2, 0))
        # p1 stays in Alpha and is now also in Beta; nothing new is created, no port changes
        self.assertEqual(self.p1.get_group_ids(), [self.a["id"], beta["id"]])
        self.assertEqual(self.p3.get_group_ids(), [beta["id"]])
        self.assertEqual(self.p2.get_group_ids(), [self.a["id"]])
        self.assertEqual([(id(i), i.get_listen()) for i in self.app.instances], before)
        self.assertEqual(self.app.saved, saves + 1)  # one save for the whole batch
        self.assertEqual(self.app.selected_proxies(), [self.p1, self.p3])  # selection kept
        self.assertEqual(self.app.add_selected_to_group(beta["id"]), (0, 2))  # already there

    def test_shared_proxy_shows_in_each_group_and_stays_one_proxy(self):
        beta = proxy_groups.create_group("Beta")
        self.app.overview_view_mode = proxy_groups.VIEW_GROUPS
        self.app.toggle_selection_mode()
        self.app._selected = {self.p1}
        self.app.add_selected_to_group(beta["id"])
        view = self.app.overview_view
        self.assertEqual(_texts(self.app).count("p1"), 2)          # a card in Alpha and in Beta
        self.assertEqual(len(view.card_slots[self.p1]), 2)
        self.assertEqual(len(self.app.instances), 3)
        # both cards follow the selection and the proxy state
        self.app.clear_proxy_selection()
        self.assertEqual(view.card_checked(self.p1), [False, False])
        self.app._on_card_selected(self.p1, True)
        self.assertEqual(view.card_checked(self.p1), [True, True])
        self.p1.running = True
        self.p1.healthy = True
        self.app.update_overview_card(self.p1)
        self.app.update_idletasks()        # card updates are applied together on the next idle
        self.assertEqual(view.card_dots(self.p1), [gui.C["green"], gui.C["green"]])

    def test_add_selected_to_new_group_proposes_name(self):
        proxy_groups.create_group(gui.t("group_suggest_name", n=1))
        self.app.toggle_selection_mode()
        self.app._selected = {self.p2, self.p3}
        with mock.patch.object(gui.simpledialog, "askstring", return_value="Fresh") as ask:
            group = self.app.add_selected_to_new_group()
        self.assertEqual(ask.call_args.kwargs["initialvalue"], gui.t("group_suggest_name", n=2))
        self.assertIn(gui.t("new_group_for_selected_prompt", count=2), ask.call_args.args)
        self.assertEqual(group["name"], "Fresh")
        self.assertEqual(self.p2.get_group_ids(), [self.a["id"], group["id"]])   # still in Alpha
        self.assertEqual(self.p3.get_group_ids(), [group["id"]])

    def test_new_group_cancel_changes_nothing(self):
        self.app.toggle_selection_mode()
        self.app._selected = {self.p3}
        with mock.patch.object(gui.simpledialog, "askstring", return_value=None):
            self.assertIsNone(self.app.add_selected_to_new_group())
        self.assertNotIn("group", self.p3.cfg)
        self.assertEqual(len(proxy_groups.load_groups()), 1)

    def test_removed_proxy_leaves_selection(self):
        self.app.toggle_selection_mode()
        self.app.select_all_proxies()
        self.app.instances.remove(self.p2)
        self.app.refresh_overview()
        self.app.update_idletasks()   # redraws are coalesced into the next idle moment
        self.assertEqual(self.app.selected_proxies(), [self.p1, self.p3])
        self.assertNotIn(self.p2, self.app._selected)


class TestGroupTabsStorage(_TempSettingsMixin, unittest.TestCase):
    def test_open_close_dedupe_and_order(self):
        a = proxy_groups.create_group("A")
        b = proxy_groups.create_group("B")
        self.assertEqual(proxy_groups.open_group_tabs([b["id"], a["id"], b["id"], "nope"]), [b["id"], a["id"]])
        self.assertEqual(proxy_groups.open_group_tabs([b["id"]]), [b["id"], a["id"]])
        self.assertEqual(proxy_groups.close_group_tab(b["id"]), [a["id"]])

    def test_view_mode_of_tab_falls_back_when_closed_or_deleted(self):
        a = proxy_groups.create_group("A")
        mode = proxy_groups.group_view_mode(a["id"])
        proxy_groups.set_view_mode(mode)                 # no tab open yet -> refused
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.VIEW_LIST)
        proxy_groups.open_group_tabs([a["id"]])
        proxy_groups.set_view_mode(mode)
        self.assertEqual(proxy_groups.get_view_mode(), mode)
        self.assertEqual(proxy_groups.group_id_of_view(mode), a["id"])
        proxy_groups.delete_group(a["id"])
        self.assertEqual(proxy_groups.get_group_tabs(), [])
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.VIEW_LIST)


class TestGroupTabsGui(_TempSettingsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = _GroupsTestApp()
        self.app._group_tabs_frame = tk.Frame(self.app)
        self.a = proxy_groups.create_group("Alpha")
        self.b = proxy_groups.create_group("Beta")
        self.pa = self._add(1081, "in-alpha", self.a["id"])
        self.pb = self._add(1082, "in-beta", self.b["id"])
        self.loose = self._add(1083, "loose")
        self.app.refresh_overview()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        _release_tk(self)
        super().tearDown()

    def _add(self, port, name, group=None):
        cfg = {"name": name, "url": "", "listen": f"127.0.0.1:{port}", "order": port}
        if group:
            cfg["group"] = group
        inst = gui.ProxyInstance(self.app, cfg, len(self.app.instances))
        self.app.instances.append(inst)
        return inst

    def _tab_titles(self):
        return [self.app._group_tab_widgets[g][1].cget("text") for g in proxy_groups.get_group_tabs()]

    def _shown_names(self):
        names = {"in-alpha", "in-beta", "loose"}
        return [t for t in _texts(self.app) if t in names]

    def test_open_tabs_shows_only_that_group(self):
        self.app.open_group_tabs([self.a["id"], self.b["id"]])
        self.assertEqual(self._tab_titles(), ["📁 Alpha", "📁 Beta"])
        self.assertEqual(self.app.overview_view_mode, proxy_groups.group_view_mode(self.b["id"]))
        self.assertEqual(self._shown_names(), ["in-beta"])

        self.app._group_tab_widgets[self.a["id"]][1].invoke()      # click the "Alpha" tab
        self.assertEqual(self._shown_names(), ["in-alpha"])
        self.assertEqual(self.app._group_tab_widgets[self.a["id"]][1].cget("bg"), gui.C["blue"])
        self.assertEqual(self.app._group_tab_widgets[self.b["id"]][1].cget("bg"), gui.C["overlay"])
        self.assertEqual(proxy_groups.get_view_mode(), proxy_groups.group_view_mode(self.a["id"]))

    def test_close_active_tab_falls_back_to_groups_view(self):
        self.app.open_group_tabs([self.a["id"], self.b["id"]])
        self.app._group_tab_widgets[self.b["id"]][2].invoke()      # ✕ on the active "Beta" tab
        self.assertEqual(self._tab_titles(), ["📁 Alpha"])
        self.assertEqual(self.app.overview_view_mode, proxy_groups.VIEW_GROUPS)
        self.assertEqual(sorted(self._shown_names()), ["in-alpha", "in-beta", "loose"])
        self.assertEqual(proxy_groups.get_group_tabs(), [self.a["id"]])

    def test_close_inactive_tab_keeps_view(self):
        self.app.open_group_tabs([self.a["id"], self.b["id"]])
        self.app._group_tab_widgets[self.a["id"]][2].invoke()
        self.assertEqual(self.app.overview_view_mode, proxy_groups.group_view_mode(self.b["id"]))
        self.assertEqual(self._tab_titles(), ["📁 Beta"])

    def test_rename_and_delete_update_tabs(self):
        self.app.open_group_tabs([self.a["id"]])
        proxy_groups.rename_group(self.a["id"], "Renamed")
        self.app.on_groups_changed()
        self.assertEqual(self._tab_titles(), ["📁 Renamed"])
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(_GroupsTestApp, "_ask_choice", return_value="only_group"):
            self.app.delete_group_flow(proxy_groups.get_group(self.a["id"]), self.app)
        self.assertEqual(self.app._group_tab_widgets, {})
        self.assertEqual(self.app.overview_view_mode, proxy_groups.VIEW_GROUPS)

    def test_selection_mode_works_inside_a_tab(self):
        self.app.open_group_tabs([self.a["id"]])
        self.app.toggle_selection_mode()
        self.assertEqual(len(_buttons(self.app, "check")), 1)
        self.app.overview_view.invoke(_buttons(self.app, "select_group")[0])
        self.assertEqual(self.app.selected_proxies(), [self.pa])

    def _picker(self):
        self.app.open_group_tab_picker()
        self.app.update()
        return self.app._group_tab_picker

    def _boxes(self, dlg):
        return [w for w in _walk(dlg.rows_frame) if isinstance(w, tk.Checkbutton)]

    def test_picker_opens_checked_groups(self):
        self.app.open_group_tabs([self.a["id"]])
        dlg = self._picker()
        boxes = self._boxes(dlg)
        self.assertEqual(str(dlg.open_btn.cget("state")), tk.DISABLED)
        self.assertEqual(str(boxes[0].cget("state")), tk.DISABLED)   # Alpha already has a tab
        self.assertEqual(dlg.pager.winfo_manager(), "")
        boxes[1].invoke()                                            # Beta
        self.assertEqual(str(dlg.open_btn.cget("state")), tk.NORMAL)
        dlg.open_btn.invoke()
        self.assertEqual(proxy_groups.get_group_tabs(), [self.a["id"], self.b["id"]])
        self.assertEqual(self.app.overview_view_mode, proxy_groups.group_view_mode(self.b["id"]))

    def test_picker_paginates_and_keeps_choice_across_pages(self):
        extra = [proxy_groups.create_group(f"G{i:02d}") for i in range(10)]   # 12 groups total
        dlg = self._picker()
        self.assertEqual(len(self._boxes(dlg)), 10)
        self.assertEqual(dlg.pager.winfo_manager(), "pack")
        self._boxes(dlg)[0].invoke()                                 # Alpha on page 1
        dlg.next_btn.invoke()
        self.assertEqual(len(self._boxes(dlg)), 2)
        self._boxes(dlg)[-1].invoke()                                # G09 on page 2
        dlg.open_btn.invoke()
        self.assertEqual(proxy_groups.get_group_tabs(), [self.a["id"], extra[-1]["id"]])

    def test_picker_can_create_group(self):
        dlg = self._picker()
        with mock.patch.object(gui.simpledialog, "askstring", return_value="Fresh"):
            dlg._create()
        dlg.open_btn.invoke()
        fresh = next(g for g in proxy_groups.load_groups() if g["name"] == "Fresh")
        self.assertEqual(proxy_groups.get_group_tabs(), [fresh["id"]])


class TestOverviewViewBarOrder(_TempSettingsMixin, unittest.TestCase):
    """On the real Overview tab: List, [group tabs], By groups, + Group — in that order."""

    def test_tabs_sit_left_of_by_groups(self):
        a = proxy_groups.create_group("Alpha")
        proxy_groups.open_group_tabs([a["id"]])
        app = _GroupsTestApp()
        try:
            app.start_all = app.stop_all = app.refresh_all_geo = lambda: None
            app.open_subscription_dialog = lambda: None
            app.zapret_watcher = None
            app.overview_frame = tk.Frame(app)
            with mock.patch.object(gui, "ZapretRecoveryWidget", lambda parent, watcher=None: tk.Frame(parent)):
                gui.VlessApp._build_overview_tab(app)
            bar = app._group_tabs_frame.master
            labels = (gui.t("view_list"), gui.t("view_groups"), gui.t("btn_add_group_tab"))
            order = []
            for w in bar.pack_slaves():
                if w is app._group_tabs_frame:
                    order.append("TABS")
                elif isinstance(w, tk.Button) and w.cget("text") in labels:
                    order.append(w.cget("text"))
            self.assertEqual(order, [gui.t("view_list"), "TABS", gui.t("view_groups"), gui.t("btn_add_group_tab")])
            self.assertEqual(app._group_tab_widgets[a["id"]][1].cget("text"), "📁 Alpha")
        finally:
            app.destroy()
            del app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
