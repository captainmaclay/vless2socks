"""The update feature must not stall the GUI thread: a slow check, a long download with
progress, and showing/hiding the buttons. A 10 ms ticker on the Tk loop records the longest
gap between ticks while each scenario runs (a blocked GUI thread shows up as a big gap).
Also: a blocked GitHub costs at most direct + MAX_PROXY_ROUTES attempts. Sandbox only."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests  # noqa: F401
import updater

MAX_GAP_MS = 250     # generous: a stalled thread would show seconds


class Ticker:
    def __init__(self, app):
        self.app = app
        self.gaps = []
        self.last = None
        self.running = False

    def _tick(self):
        if not self.running:
            return
        now = time.perf_counter()
        if self.last is not None:
            self.gaps.append((now - self.last) * 1000)
        self.last = now
        self.app.after(10, self._tick)

    def run_while(self, seconds, until=None):
        self.running, self.gaps, self.last = True, [], None
        self.app.after(10, self._tick)
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.app.update()
            if until is not None and until():
                break
            time.sleep(0.002)
        self.running = False
        return max(self.gaps) if self.gaps else 0.0


def result(available, version="9.9.9"):
    info = updater.ReleaseInfo(version=version, tag="v" + version, page_url="https://example.com",
                               exe_url="x", exe_size=1)
    return updater.CheckResult(current="1.2.2", latest=info, available=available)


class ResponsivenessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        cls.app.geometry("1400x900+40+40")
        cls.sb.settle(20)
        cls.app.nav_notebook.select(cls.app.update_frame)
        cls.sb.settle(20)
        import gui
        cls.gui = gui

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        self.app._update_downloading = False
        self.app._update_checking = False
        self.app._on_update_result(result(False, version="1.2.2"))
        self.sb.settle(5)

    def test_slow_check_does_not_block(self):
        def slow_check(*a, **k):
            time.sleep(3.0)                            # GitHub slow / timing out
            return result(True)

        with mock.patch.object(updater, "check", slow_check):
            t0 = time.perf_counter()
            self.app.check_updates_now()
            call_ms = (time.perf_counter() - t0) * 1000
            gap = Ticker(self.app).run_while(5.0, until=lambda: self.app.btn_update_version.winfo_manager())
        self.assertLess(call_ms, 50, "the button handler itself must return at once")
        self.assertLess(gap, MAX_GAP_MS, f"GUI thread stalled {gap:.0f} ms during the check")
        self.assertEqual(self.app.btn_update_version.winfo_manager(), "pack")      # result arrived

    def test_long_download_with_progress_does_not_block(self):
        self.app._on_update_result(result(True))

        def slow_download(info, dest, proxies=(), progress=None, timeout=30.0):
            for i in range(1, 101):                    # 100 progress callbacks over 3 s
                time.sleep(0.03)
                progress(i * 1000, 100 * 1000)
            return dest

        with mock.patch.object(self.gui, "FROZEN", True), \
                mock.patch.object(self.gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(updater, "download", slow_download), \
                mock.patch.object(updater, "launch_swap"), \
                mock.patch.object(self.app, "_exit") as exit_:
            self.app.start_update()
            gap = Ticker(self.app).run_while(6.0, until=lambda: exit_.called)
        self.assertLess(gap, MAX_GAP_MS, f"GUI thread stalled {gap:.0f} ms during the download")
        self.assertTrue(exit_.called)

    def test_showing_and_hiding_the_buttons_is_cheap(self):
        times = []
        for available in (True, False) * 5:
            t0 = time.perf_counter()
            self.app._on_update_result(result(available))
            self.app.update_idletasks()
            times.append((time.perf_counter() - t0) * 1000)
        self.assertLess(max(times), 100, f"a button toggle took {max(times):.0f} ms")

    def test_download_does_not_use_the_shared_pool(self):
        self.app._on_update_result(result(True))
        with mock.patch.object(self.gui, "FROZEN", True), \
                mock.patch.object(self.gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.gui, "submit_background") as pool, \
                mock.patch.object(updater, "download"), mock.patch.object(updater, "launch_swap"), \
                mock.patch.object(self.app, "_exit"):
            self.app.start_update()
            self.sb.settle(20)
        pool.assert_not_called()


class RouteCapTest(unittest.TestCase):
    def test_blocked_github_tries_few_routes(self):
        calls = []

        def failing_open(url, proxy, timeout, accept="*/*"):
            calls.append(proxy)
            raise OSError("blocked")

        proxies = [f"http://127.0.0.1:{11000 + i}" for i in range(90)]
        with mock.patch.object(updater, "_open", failing_open):
            r = updater.check(proxies, current="1.2.2")
        self.assertEqual(len(calls), 1 + updater.MAX_PROXY_ROUTES)
        self.assertEqual(calls[1], proxies[0])                 # System Proxy comes first
        self.assertTrue(r.error)


if __name__ == "__main__":
    unittest.main()
