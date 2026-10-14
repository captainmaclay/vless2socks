"""WSL watchdog decisions. wsl.exe is never started here: every command goes to a scripted fake."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vless2socks import wsl_watchdog as ww
from vless2socks.wsl_watchdog import CommandResult, WslWatchdog

LIST_RUNNING = "  NAME      STATE           VERSION\r\n* Ubuntu    Running         2\r\n  Debian    Stopped         2\r\n"
LIST_STOPPED = "  NAME      STATE           VERSION\r\n* Ubuntu    Stopped         2\r\n"


class FakeWsl:
    """Scripted wsl.exe. ``hung`` / ``gui`` describe the distro, ``service_hung`` the WSL service."""

    def __init__(self, listing=LIST_RUNNING):
        self.listing = listing
        self.service_hung = False
        self.hung = False
        self.gui = True
        self.shutdown_hangs = False
        self.fixed_by = None  # "terminate" / "shutdown": the command that cures the hang
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(args)
        verb = args[1] if len(args) > 1 else ""
        if args[0] == "taskkill.exe":
            return CommandResult(1)
        if verb == "--list":
            return CommandResult(None) if self.service_hung else CommandResult(0, self.listing.encode("utf-16le"))
        if verb in ("--terminate", "--shutdown"):
            if verb == "--shutdown" and self.shutdown_hangs:
                return CommandResult(None)
            if self.fixed_by == verb.lstrip("-"):
                self.hung = self.service_hung = False
                self.gui = True
            return CommandResult(0)
        if "/bin/true" in args:
            return CommandResult(None) if self.hung else CommandResult(0)
        # health probe inside the distro
        if self.hung:
            return CommandResult(None)
        marks = ww._ALIVE_MARK + "\n" + (ww._GUI_OK_MARK if self.gui else ww._GUI_DOWN_MARK) + "\n"
        return CommandResult(0, marks.encode())

    def verbs(self):
        return [c[1] for c in self.calls if c[0] == "wsl.exe" and c[1].startswith("--") and c[1] != "--list"]


class WslWatchdogTest(unittest.TestCase):

    def make(self, wsl, **kwargs):
        self.now = 1000.0
        self.logs, self.alerts = [], []
        self.free_commit = 40 * 1024 ** 3
        return WslWatchdog(runner=wsl, clock=lambda: self.now, on_log=self.logs.append, on_alert=self.alerts.append,
                           free_commit=lambda: self.free_commit, **kwargs)

    def run_ticks(self, dog, count, step=10.0):
        for _ in range(count):
            dog.tick()
            self.now += step

    def test_parses_running_distros(self):
        self.assertEqual(ww.parse_running_distros(LIST_RUNNING), ["Ubuntu"])
        self.assertEqual(ww.parse_running_distros(LIST_STOPPED), [])

    def test_healthy_wsl_is_only_observed(self):
        wsl = FakeWsl()
        dog = self.make(wsl)
        self.run_ticks(dog, 5)
        self.assertEqual(wsl.verbs(), [])
        self.assertEqual(dog.get_state()["status"], ww.STATUS_OK)
        self.assertEqual(self.alerts, [])

    def test_stopped_wsl_is_not_started_and_not_a_failure(self):
        wsl = FakeWsl(LIST_STOPPED)
        dog = self.make(wsl)
        self.run_ticks(dog, 5)
        self.assertEqual([c[1] for c in wsl.calls], ["--list"] * 5, "a stopped distro must not be woken by the probe")
        self.assertEqual(dog.get_state()["status"], ww.STATUS_IDLE)

    def test_single_slow_answer_does_not_restart(self):
        wsl = FakeWsl()
        dog = self.make(wsl)
        dog.tick()
        wsl.hung = True
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER - 1)
        wsl.hung = False
        self.run_ticks(dog, 3)
        self.assertEqual(wsl.verbs(), [])

    def test_hung_distro_gets_the_mildest_fix_first(self):
        wsl = FakeWsl()
        wsl.hung = True
        wsl.fixed_by = "terminate"
        dog = self.make(wsl)
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        self.assertEqual(wsl.verbs(), ["--terminate"])
        self.assertIn(["wsl.exe", "-d", "Ubuntu", "-e", "/bin/true"], wsl.calls, "the distro that was running is woken again")
        self.assertEqual(dog.get_state()["status"], ww.STATUS_OK)

    def test_escalates_to_shutdown_when_terminate_is_not_enough(self):
        wsl = FakeWsl()
        wsl.hung = True
        wsl.fixed_by = "shutdown"
        dog = self.make(wsl)
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        self.assertEqual(wsl.verbs(), ["--terminate", "--shutdown"])
        self.assertEqual(dog.get_state()["status"], ww.STATUS_OK)

    def test_hung_service_is_shut_down_and_last_known_distro_woken(self):
        wsl = FakeWsl()
        dog = self.make(wsl)
        dog.tick()  # learns that Ubuntu is running
        self.now += 30
        wsl.service_hung = True
        wsl.fixed_by = "shutdown"
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        self.assertEqual(wsl.verbs(), ["--shutdown"])
        self.assertIn(["wsl.exe", "-d", "Ubuntu", "-e", "/bin/true"], wsl.calls)

    def test_no_recovery_right_after_wake_from_sleep(self):
        wsl = FakeWsl()
        wsl.hung = True
        dog = self.make(wsl)
        dog.note_resume()
        self.run_ticks(dog, 4)  # 40 s, inside the grace period
        self.assertEqual(wsl.calls, [], "WSL is not even probed while it thaws")
        wsl.hung = False
        self.run_ticks(dog, 3)
        self.assertEqual(wsl.verbs(), [])

    def test_deliberate_restart_is_not_treated_as_a_hang(self):
        wsl = FakeWsl()
        wsl.hung = True
        dog = self.make(wsl)
        with ww.WSL_OPERATION_LOCK:
            self.run_ticks(dog, 5, step=1.0)
        self.assertEqual(wsl.calls, [])

    def test_broken_wslg_alone_does_not_kill_working_terminals(self):
        wsl = FakeWsl()
        wsl.gui = False
        dog = self.make(wsl)
        self.run_ticks(dog, 8)
        self.assertEqual(wsl.verbs(), [])
        self.assertEqual(dog.get_state()["status"], ww.STATUS_GUI_DOWN)
        self.assertEqual(len(self.alerts), 1, "the user is told once, not on every poll")

    def test_broken_wslg_is_restarted_when_opted_in(self):
        wsl = FakeWsl()
        wsl.gui = False
        wsl.fixed_by = "shutdown"
        dog = self.make(wsl, recover_gui=True)
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        self.assertEqual(wsl.verbs(), ["--shutdown"])

    def test_auto_recover_off_only_reports(self):
        wsl = FakeWsl()
        wsl.hung = True
        dog = self.make(wsl, auto_recover=False)
        self.run_ticks(dog, 8)
        self.assertEqual(wsl.verbs(), [])
        self.assertEqual(len(self.alerts), 1)

    def test_unfixable_hang_does_not_cause_a_restart_loop(self):
        wsl = FakeWsl()
        wsl.hung = True  # nothing cures it
        dog = self.make(wsl)
        self.run_ticks(dog, 400, step=30.0)  # more than three hours
        shutdowns = wsl.verbs().count("--shutdown")
        self.assertGreaterEqual(shutdowns, 1)
        self.assertLessEqual(shutdowns, WslWatchdog.MAX_RECOVERIES_PER_HOUR * 4)

    def test_cooldown_between_recoveries(self):
        wsl = FakeWsl()
        wsl.hung = True
        dog = self.make(wsl)
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        first = wsl.verbs().count("--shutdown")
        self.run_ticks(dog, 10)  # 100 s later: still inside the cooldown
        self.assertEqual(wsl.verbs().count("--shutdown"), first)

    def test_stuck_shutdown_reports_missing_admin_rights(self):
        wsl = FakeWsl()
        wsl.service_hung = True
        wsl.shutdown_hangs = True
        dog = self.make(wsl)
        self.run_ticks(dog, WslWatchdog.FAILURES_TO_RECOVER)
        self.assertTrue(any(c[0] == "taskkill.exe" for c in wsl.calls))
        self.assertTrue(any("administrator" in m for m in self.logs))

    def test_out_of_memory_is_not_mistaken_for_a_hung_wsl(self):
        """Windows out of virtual memory: wsl.exe cannot even start. No probes (no error boxes), no restart."""
        wsl = FakeWsl()
        wsl.hung = True  # what a probe would look like if it were launched
        dog = self.make(wsl)
        self.free_commit = 300 * 1024 ** 2
        self.run_ticks(dog, 8)
        self.assertEqual(wsl.calls, [])
        self.assertEqual(dog.get_state()["status"], ww.STATUS_LOW_MEMORY)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("memory", self.alerts[0])

        self.free_commit = 40 * 1024 ** 3
        wsl.hung = False
        dog.tick()
        self.assertEqual(dog.get_state()["status"], ww.STATUS_OK)

    def test_wsl_exe_that_fails_to_start_does_not_trigger_recovery(self):
        def loader_failure(args, timeout):
            if args[1] == "--list":
                return CommandResult(0, LIST_RUNNING.encode("utf-16le"))
            return CommandResult(0xC0000142)
        dog = self.make(loader_failure)
        self.run_ticks(dog, 8)
        self.assertEqual(dog.get_state()["status"], ww.STATUS_UNAVAILABLE)
        self.assertEqual(self.alerts, [])

    def test_missing_wsl_is_quietly_ignored(self):
        def no_wsl(args, timeout):
            raise FileNotFoundError("wsl.exe")
        dog = self.make(no_wsl)
        self.run_ticks(dog, 5)
        self.assertEqual(dog.get_state()["status"], ww.STATUS_UNAVAILABLE)
        self.assertEqual(self.alerts, [])


if __name__ == "__main__":
    unittest.main()
