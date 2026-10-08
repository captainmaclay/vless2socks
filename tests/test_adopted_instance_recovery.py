"""Self-healing of proxies the GUI did not start itself.

After a GUI crash the proxy processes keep running; the next GUI session "adopts" them from the port
probe and has no process handle. When such a process later dies it must be brought back like any other.
No real ports, processes or config files are touched here.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import ProxyInstance


def make_instance(**cfg):
    base = {"url": "vless://00000000-0000-0000-0000-000000000000@198.51.100.1:443?security=none", "listen": "127.0.0.1:19990"}
    base.update(cfg)
    return ProxyInstance(MagicMock(), base, 999)


class AdoptedInstanceRecoveryTest(unittest.TestCase):

    def test_adopted_process_that_dies_is_reconnected(self):
        inst = make_instance()
        with patch.object(inst, "_schedule_reconnect") as schedule, \
                patch.object(inst, "is_auto_reconnect_enabled", return_value=True):
            # Port already answers at GUI start: the running process is adopted without a handle
            self.assertTrue(inst.apply_health_status(True))
            self.assertTrue(inst.running)
            self.assertIsNone(inst.process)
            schedule.assert_not_called()

            # The adopted process dies
            self.assertTrue(inst.apply_health_status(False))
            self.assertFalse(inst.running, "stale 'running' flag blocks _schedule_reconnect()")
            self.assertFalse(inst.healthy)
            schedule.assert_called_once()

    def test_reconnect_is_really_scheduled_for_adopted_instance(self):
        """End to end through the real _schedule_reconnect: a timer must be armed."""
        inst = make_instance()
        inst.app.after.return_value = "timer-1"
        with patch.object(inst, "is_auto_reconnect_enabled", return_value=True), \
                patch("gui.settings_manager.get_setting", return_value="10, 15"):
            inst.apply_health_status(True)
            inst.apply_health_status(False)
        self.assertEqual(inst._reconnect_timer_id, "timer-1")
        delay_ms, callback = inst.app.after.call_args[0][:2]
        self.assertEqual(delay_ms, 10_000)
        self.assertEqual(callback, inst._do_reconnect)

    def test_own_live_process_is_left_to_its_supervisor(self):
        inst = make_instance()
        inst.running = True
        inst.healthy = True
        inst.process = MagicMock()
        inst.process.poll.return_value = None  # main.py alive, xray restarting underneath
        with patch.object(inst, "_schedule_reconnect") as schedule:
            inst.apply_health_status(False)
            self.assertTrue(inst.running)
            schedule.assert_not_called()

    def test_down_proxy_that_should_run_gets_retry_but_stopped_one_does_not(self):
        with patch("gui.settings_manager.get_setting", return_value=True):
            wanted = make_instance()
            wanted._should_run = True
            with patch.object(wanted, "_schedule_reconnect") as schedule:
                wanted.apply_health_status(False)
                schedule.assert_called_once()

            never_started = make_instance()
            with patch.object(never_started, "_schedule_reconnect") as schedule:
                never_started.apply_health_status(False)
                schedule.assert_not_called()

            stopped_by_user = make_instance()
            stopped_by_user._should_run = True
            stopped_by_user._manual_stop = True
            with patch.object(stopped_by_user, "_schedule_reconnect") as schedule:
                stopped_by_user.apply_health_status(False)
                schedule.assert_not_called()

            retry_pending = make_instance()
            retry_pending._should_run = True
            retry_pending._reconnect_timer_id = "timer-7"
            with patch.object(retry_pending, "_schedule_reconnect") as schedule:
                retry_pending.apply_health_status(False)
                schedule.assert_not_called()


class PortTakeoverKillsWholeProxyTreeTest(unittest.TestCase):
    """Killing xray alone is useless: its launcher restarts it. The takeover must target the launcher."""

    GUI, LAUNCHER, WORKER, XRAY = 100, 200, 300, 400

    def table(self, **overrides):
        table = {
            self.GUI: (1, "pythonw.exe"),
            self.LAUNCHER: (self.GUI, "python.exe"),   # .venv launcher: python main.py
            self.WORKER: (self.LAUNCHER, "python.exe"),  # base interpreter
            self.XRAY: (self.WORKER, "xray.exe"),
        }
        table.update(overrides)
        return table

    @staticmethod
    def ages(pid):
        return pid  # older process = smaller pid in these fixtures

    def expand(self, pids, current_pid, table, start_time=None):
        from gui import _expand_to_proxy_supervisors
        return _expand_to_proxy_supervisors(set(pids), current_pid, table=table, start_time=start_time or self.ages)

    def test_orphaned_xray_is_replaced_by_its_top_launcher(self):
        # The GUI that started the chain is gone (pid 100 no longer exists); we are a new GUI, pid 900
        table = self.table()
        del table[self.GUI]
        table[900] = (1, "pythonw.exe")
        self.assertEqual(self.expand({self.XRAY}, 900, table), {self.LAUNCHER})

    def test_never_climbs_into_the_current_gui(self):
        # Same shape, but the GUI is us and happens to be python.exe (started from a console)
        table = self.table()
        table[self.GUI] = (1, "python.exe")
        self.assertEqual(self.expand({self.XRAY}, self.GUI, table), {self.LAUNCHER})

    def test_foreign_port_holder_is_not_expanded(self):
        table = self.table()
        table[500] = (self.WORKER, "someapp.exe")
        self.assertEqual(self.expand({500}, 900, table), {500})

    def test_reused_parent_pid_is_not_trusted(self):
        # The launcher's recorded parent pid was reused by an unrelated, younger python.exe
        table = self.table()
        table[self.GUI] = (1, "python.exe")
        younger_parent = {self.GUI: 10_000}
        start = lambda pid: younger_parent.get(pid, pid)
        self.assertEqual(self.expand({self.XRAY}, 900, table, start_time=start), {self.LAUNCHER})


if __name__ == "__main__":
    unittest.main()
