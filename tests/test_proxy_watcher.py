"""Watcher model: selection rules, who is watched, startup set, revive pauses, pills."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests  # noqa: F401  (isolated settings)
import proxy_watcher as pw
import settings_manager
from proxy_watcher import ALL, FLAG_SYSTEM, FLAG_TELEGRAM, FLAG_WORK, group_key


class FakeInst:
    def __init__(self, port, groups=(), tg=False, sysp=False, url=True, running=False, work=False):
        self.cfg = {"listen": f"127.0.0.1:{port}", "groups": list(groups),
                    "url": "vless://x@h:1#n" if url else ""}
        self.tg, self.sysp, self.work = tg, sysp, work
        self.running = running
        self.is_transitioning = False
        self._manual_stop = False

    def is_telegram_proxy(self):
        return self.tg

    def is_system_proxy(self):
        return self.sysp

    def is_work_proxy(self):
        return self.work


class TestSelection(unittest.TestCase):
    def test_picking_a_group_or_flag_drops_all(self):
        self.assertEqual(pw.toggle([ALL], group_key("g1")), [group_key("g1")])
        self.assertEqual(pw.toggle([ALL], FLAG_TELEGRAM), [FLAG_TELEGRAM])

    def test_picking_all_drops_the_rest(self):
        self.assertEqual(pw.toggle([FLAG_TELEGRAM, group_key("g1")], ALL), [ALL])

    def test_unchecking_the_last_one_falls_back_to_all(self):
        self.assertEqual(pw.toggle([group_key("g1")], group_key("g1")), [ALL])

    def test_multi_select_and_unknown_groups(self):
        t = pw.toggle([FLAG_TELEGRAM], group_key("g1"))
        self.assertEqual(t, [FLAG_TELEGRAM, group_key("g1")])
        self.assertEqual(pw.normalize(t, known_group_ids=["g2"]), [FLAG_TELEGRAM])   # deleted group
        self.assertEqual(pw.normalize(["bogus"]), [ALL])

    def test_persistence_and_first_run_default(self):
        settings_manager.set_setting(pw.ENABLED_KEY, None)
        settings_manager.set_setting("autostart_proxies", False)
        settings_manager.set_setting("auto_reconnect", False)
        self.assertFalse(pw.is_enabled())          # both old switches off: watcher off
        settings_manager.set_setting("auto_reconnect", True)
        self.assertTrue(pw.is_enabled())
        pw.set_enabled(False)
        self.assertFalse(pw.is_enabled())
        pw.set_targets([FLAG_SYSTEM, group_key("g1")])
        self.assertEqual(pw.get_targets(), [FLAG_SYSTEM, group_key("g1")])
        settings_manager.set_setting(pw.TARGETS_KEY, None)
        self.assertEqual(pw.get_targets(), [ALL])


class TestMembership(unittest.TestCase):
    def setUp(self):
        self.a = FakeInst(1, groups=["g1"])
        self.b = FakeInst(2, groups=["g2"], tg=True)
        self.c = FakeInst(3, sysp=True)
        self.d = FakeInst(4, groups=["g1"], url=False)
        self.all = [self.a, self.b, self.c, self.d]

    def test_watched(self):
        self.assertEqual(pw.watched(self.all, [ALL]), self.all)
        # c is a SystemProxy: watched whatever the selection
        self.assertEqual(pw.watched(self.all, [group_key("g1")]), [self.a, self.c, self.d])
        self.assertEqual(pw.watched(self.all, [FLAG_TELEGRAM]), [self.b, self.c])
        self.assertEqual(pw.watched(self.all, [FLAG_SYSTEM, group_key("g2")]), [self.b, self.c])

    def test_startup_starts_only_watched_with_a_url(self):
        self.assertEqual(pw.startup_set(self.all, [group_key("g1")]), [self.a, self.c])
        self.b.running = True
        self.assertEqual(pw.startup_set(self.all, [ALL]), [self.a, self.c])

    def test_pills(self):
        self.b.running = True
        groups = [{"id": "g1", "name": "Finland"}, {"id": "g2", "name": "World"}]
        p = pw.pills(self.all, [group_key("g1"), FLAG_TELEGRAM], groups)
        self.assertEqual([(x.label, x.up, x.total, x.state) for x in p],
                         [("TelegramProxy", 1, 1, "ok"), ("SystemProxy", 0, 1, "down"),
                          ("WorkProxy", 0, 0, "none"), ("Finland", 0, 1, "down")])
        p = pw.pills(self.all, [ALL], groups)
        self.assertEqual((p[-1].label, p[-1].up, p[-1].total, p[-1].state), ("All", 1, 3, "partial"))


class TestSystemCategoriesAlwaysWatched(unittest.TestCase):
    """Watcher limited to a couple of groups: SystemProxy and WorkProxy must still start
    with the program and come back up (they were left down after a restart)."""

    def setUp(self):
        self.sys1 = FakeInst(1015, sysp=True)
        self.sys2 = FakeInst(1016, sysp=True)
        self.work = FakeInst(1030, work=True)
        self.grouped = FakeInst(1132, groups=["g1"])
        self.loose = FakeInst(1020)                 # no group, no flag
        self.all = [self.sys1, self.sys2, self.work, self.grouped, self.loose]

    def test_startup_with_only_groups_selected(self):
        self.assertEqual(pw.startup_set(self.all, [group_key("g1"), group_key("g2")]),
                         [self.sys1, self.sys2, self.work, self.grouped])

    def test_revived_but_manual_stop_respected(self):
        now = [0.0]
        r = pw.Reviver(clock=lambda: now[0])
        settings_manager.set_setting("reconnect_intervals", "10")
        targets = [group_key("g1")]
        self.work._manual_stop = True
        r.due(self.all, targets, True)
        now[0] += 10
        self.assertEqual(r.due(self.all, targets, True), [self.sys1, self.sys2, self.grouped])

    def test_work_flag_is_a_known_target(self):
        self.assertEqual(pw.normalize([FLAG_WORK]), [FLAG_WORK])


class TestReviver(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.r = pw.Reviver(clock=lambda: self.now[0])
        settings_manager.set_setting("reconnect_intervals", "10, 20")
        self.p = FakeInst(1, groups=["g1"])

    def test_waits_a_pause_then_retries_with_growing_pauses(self):
        t = [group_key("g1")]
        self.assertEqual(self.r.due([self.p], t, True), [])        # just went down: wait 10 s
        self.now[0] += 9
        self.assertEqual(self.r.due([self.p], t, True), [])
        self.now[0] += 1
        self.assertEqual(self.r.due([self.p], t, True), [self.p])  # attempt 1
        self.now[0] += 10
        self.assertEqual(self.r.due([self.p], t, True), [])        # next pause is 20 s
        self.now[0] += 10
        self.assertEqual(self.r.due([self.p], t, True), [self.p])  # attempt 2

    def test_not_watched_disabled_manual_stop_no_url(self):
        self.now[0] += 1000
        for targets, enabled in (([FLAG_TELEGRAM], True), ([ALL], False)):
            self.r.due([self.p], targets, enabled)
            self.now[0] += 1000
            self.assertEqual(self.r.due([self.p], targets, enabled), [])
        self.p._manual_stop = True
        self.r.due([self.p], [ALL], True)
        self.now[0] += 1000
        self.assertEqual(self.r.due([self.p], [ALL], True), [])

    def test_back_up_resets_the_pauses(self):
        t = [ALL]
        self.r.due([self.p], t, True)
        self.now[0] += 10
        self.assertEqual(self.r.due([self.p], t, True), [self.p])
        self.p.running = True
        self.r.due([self.p], t, True)
        self.assertEqual(self.r.attempts(self.p), 0)


if __name__ == "__main__":
    unittest.main()
