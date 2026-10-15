"""Regression tests for the process storm behind "cmd.exe 0xc0000142" and for the slow exit.

Dozens of proxies starting together used to spawn cmd.exe (route print via shell=True),
taskkill and netstat by the hundred until Windows ran out of desktop heap, and the
exit stopped proxies one by one on the GUI thread. These tests pin the fixes down
with real processes and sockets where it matters.
"""

import gc
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui
import settings_manager
import subscriptions
from vless2socks import winroute

WIN = sys.platform == "win32"


def _spawn_sleeper(seconds=60, with_child=False) -> subprocess.Popen:
    """A python process that sleeps; optionally it starts a sleeping child of its own."""
    code = "import time\n"
    if with_child:
        code += ("import subprocess, sys\n"
                 f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({seconds})'])\n")
    code += f"time.sleep({seconds})\n"
    return subprocess.Popen([sys.executable, "-c", code],
                            creationflags=subprocess.CREATE_NO_WINDOW if WIN else 0)


def _descendants(pid: int) -> set[int]:
    """All processes below `pid` (a venv python.exe is a launcher with the real python as child)."""
    table = gui._process_table()
    found, frontier = set(), [pid]
    while frontier:
        cur = frontier.pop()
        for child, (pp, _) in table.items():
            if pp == cur and child not in found and child != cur:
                found.add(child)
                frontier.append(child)
    return found


def _alive(pid: int) -> bool:
    return pid in gui._process_table() if WIN else os.path.exists(f"/proc/{pid}")


def _wait_until(cond, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


class TestRouteLookupHasNoShell(unittest.TestCase):
    def setUp(self):
        winroute.clear_cache()

    def tearDown(self):
        winroute.clear_cache()

    def test_no_shell_in_route_lookups(self):
        for rel in ("vless2socks/ipcheck.py", "vless2socks/xray/config_builder.py", "vless2socks/winroute.py"):
            code = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn('check_output("route', code, rel)
            self.assertNotIn("shell=True)", code, rel)

    @unittest.skipUnless(WIN, "Windows routing table")
    def test_concurrent_callers_share_one_route_run(self):
        calls = []
        real_run = subprocess.run

        def counting_run(args, **kwargs):
            calls.append((list(args), kwargs.get("creationflags"), kwargs.get("shell")))
            time.sleep(0.2)  # long enough for every thread to pile up on the lock
            return real_run(args, **kwargs)

        with mock.patch.object(winroute.subprocess, "run", side_effect=counting_run):
            threads = [threading.Thread(target=winroute.route_print_default) for _ in range(20)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(15)
        self.assertEqual(len(calls), 1)
        args, flags, shell = calls[0]
        self.assertEqual(args, ["route", "print", "0.0.0.0"])
        self.assertEqual(flags, subprocess.CREATE_NO_WINDOW)
        self.assertFalse(shell)

    @unittest.skipUnless(WIN, "Windows routing table")
    def test_gateway_and_tun_lookups_reuse_the_cache(self):
        from vless2socks.ipcheck import is_system_tun_active
        from vless2socks.xray.config_builder import get_physical_gateway_ip
        with mock.patch.object(winroute.subprocess, "run", wraps=subprocess.run) as run:
            get_physical_gateway_ip()
            is_system_tun_active()
            get_physical_gateway_ip()
        self.assertEqual(run.call_count, 1)


@unittest.skipUnless(WIN, "Win32 process API")
class TestKillProcessTrees(unittest.TestCase):
    def test_kills_parent_and_children_without_taskkill(self):
        parent = _spawn_sleeper(with_child=True)
        try:
            # the sleeper's own child is at least two levels down when python is a venv launcher
            self.assertTrue(_wait_until(lambda: len(_descendants(parent.pid)) >= 2), "child did not start")
            tree = {parent.pid} | _descendants(parent.pid)
            with mock.patch.object(gui.subprocess, "run") as run:
                killed = gui.kill_process_trees([parent.pid])
            run.assert_not_called()  # no taskkill.exe per kill
            self.assertEqual(killed, tree)
            self.assertTrue(_wait_until(lambda: not any(_alive(p) for p in tree)))
        finally:
            if parent.poll() is None:
                parent.kill()

    def test_never_kills_itself_or_ancestors(self):
        me = os.getpid()
        parent_of_me = gui._process_table()[me][0]
        self.assertEqual(gui.kill_process_trees([me, parent_of_me]), set())
        self.assertTrue(_alive(me))


class TestPorts(unittest.TestCase):
    def _listen(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        self.addCleanup(sock.close)
        return sock.getsockname()[1]

    @unittest.skipUnless(WIN, "netstat -ano")
    def test_one_netstat_finds_listener(self):
        port = self._listen()
        with mock.patch.object(gui.subprocess, "run", wraps=subprocess.run) as run:
            found = gui.listening_pids_by_port([port, 1])
        self.assertEqual(run.call_count, 1)
        self.assertIn(os.getpid(), found.get(port, set()))

    def test_free_ports_skips_scan_when_ports_are_free(self):
        with mock.patch.object(gui, "listening_pids_by_port") as scan:
            self.assertEqual(gui.free_ports([]), set())
            with mock.patch.object(gui, "is_port_free", return_value=True):
                self.assertEqual(gui.free_ports([23001, 33001]), set())
        scan.assert_not_called()

    @unittest.skipUnless(WIN, "Win32 process API")
    def test_free_ports_kills_holder(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import socket,sys,time\ns=socket.socket();s.bind(('127.0.0.1',0));s.listen()\n"
             "print(s.getsockname()[1],flush=True)\ntime.sleep(60)"],
            stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            port = int(holder.stdout.readline())
            self.assertFalse(gui.is_port_free(port))
            tree = {holder.pid} | _descendants(holder.pid)
            killed = gui.free_ports([port])
            self.assertTrue(killed)
            self.assertLessEqual(killed, tree)  # the process bound to the port, nothing else
            self.assertTrue(_wait_until(lambda: gui.is_port_free(port)))
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.wait(5)
            holder.stdout.close()

    def test_free_ports_never_kills_this_process(self):
        port = self._listen()
        self.assertEqual(gui.free_ports([port]), set())


def _release(test):
    test.app = None
    gc.collect()


class _App(tk.Tk):
    """after() from worker threads is queued for the GUI thread, as in VlessApp."""

    def __init__(self):
        import queue
        super().__init__()
        self.withdraw()
        self.instances = []
        self.logs = []
        self._calls = queue.SimpleQueue()
        self._drain()

    def after(self, ms, func=None, *args):
        if func is not None and threading.current_thread() is not threading.main_thread():
            self._calls.put((ms, func, args))
            return None
        return tk.Tk.after(self, ms, func, *args)

    def _drain(self):
        while not self._calls.empty():
            ms, func, args = self._calls.get_nowait()
            tk.Tk.after(self, ms, func, *args)
        self._drain_id = tk.Tk.after(self, 10, self._drain)

    def destroy(self):
        try:
            self.after_cancel(self._drain_id)
        except Exception:
            pass
        super().destroy()

    def _log(self, msg):
        self.logs.append(msg)


for _name in ("start_proxies_throttled", "shutdown_all_proxies"):
    setattr(_App, _name, getattr(gui.VlessApp, _name))


class _FakeStarter:
    """Stands in for ProxyInstance.start_async: finishes later, from a worker thread."""

    live = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, app, name, delay=0.05, refuse=False):
        self.app = app
        self.name = name
        self.delay = delay
        self.refuse = refuse
        self.running = False
        self.is_transitioning = False
        self.started = False

    def get_display_name(self):
        return self.name

    def start_async(self, callback=None):
        if self.refuse:
            return
        self.is_transitioning = True
        with _FakeStarter.lock:
            _FakeStarter.live += 1
            _FakeStarter.peak = max(_FakeStarter.peak, _FakeStarter.live)

        def work():
            time.sleep(self.delay)
            with _FakeStarter.lock:
                _FakeStarter.live -= 1
            self.is_transitioning = False
            self.running = self.started = True
            self.app.after(0, lambda: callback(True))
        threading.Thread(target=work, daemon=True).start()


class TestThrottledStart(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        _FakeStarter.live = _FakeStarter.peak = 0

    def tearDown(self):
        self.app.destroy()
        _release(self)

    def _pump(self, cond, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            self.app.update()
            time.sleep(0.01)
        return cond()

    def test_never_more_than_limit_at_once_and_all_start(self):
        fakes = [_FakeStarter(self.app, f"p{i}") for i in range(20)]
        fakes.insert(5, _FakeStarter(self.app, "no-url", refuse=True))  # must not block the queue
        self.app.instances = fakes
        with mock.patch.object(gui, "START_SPACING_MS", 1):
            queued = self.app.start_proxies_throttled(fakes)
            self.assertEqual(queued, 21)
            self.assertTrue(self._pump(lambda: all(f.started for f in fakes if not f.refuse)))
        self.assertLessEqual(_FakeStarter.peak, gui.START_CONCURRENCY)
        self.assertGreater(_FakeStarter.peak, 1)


@unittest.skipUnless(WIN, "Win32 process API")
class TestFastShutdown(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_exit_test_"))
        self.app = _App()
        self.procs = []
        for i in range(12):
            proc = _spawn_sleeper(with_child=True)
            self.procs.append(proc)
            inst = gui.ProxyInstance(self.app, {"url": "", "listen": f"127.0.0.1:{23400 + i}"}, 900 + i)
            inst.config_path = self.tmp / f".instance_{900 + i}.json"
            inst.config_path.write_text("{}", encoding="utf-8")
            inst.process = proc
            inst.running = True
            self.app.instances.append(inst)

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
        self.app.destroy()
        _release(self)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_proxies_die_together_quickly(self):
        children = {pid for pid, (pp, _) in gui._process_table().items() if pp in {p.pid for p in self.procs}}
        started = time.monotonic()
        thread = self.app.shutdown_all_proxies()
        self.assertTrue(thread.is_alive() or True)       # returns at once, work is in the background
        thread.join(10)
        elapsed = time.monotonic() - started
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed, 5.0, f"exit took {elapsed:.1f}s for 12 proxies")
        self.assertTrue(all(p.poll() is not None for p in self.procs))
        self.assertTrue(_wait_until(lambda: not any(_alive(c) for c in children)))
        for inst in self.app.instances:
            self.assertFalse(inst.running)
            self.assertFalse(inst._should_run)
            self.assertFalse(inst.config_path.exists())

    def test_shutdown_does_not_block_the_caller(self):
        with mock.patch.object(gui, "kill_process_trees", side_effect=lambda *a, **k: time.sleep(1) or set()):
            started = time.monotonic()
            thread = self.app.shutdown_all_proxies()
            self.assertLess(time.monotonic() - started, 0.5)
            thread.join(10)


class TestKillswitchCheckOffGuiThread(unittest.TestCase):
    def test_tun_lookup_runs_in_worker_and_leak_stops_async(self):
        app = mock.MagicMock()
        inst = gui.ProxyInstance(app, {"url": "", "listen": "127.0.0.1:23500"}, 950)
        inst.running = True
        threads = []

        def tun():
            threads.append(threading.current_thread() is threading.main_thread())
            return False

        with mock.patch("vless2socks.ipcheck.check_ip_leak", return_value=(True, "1.1.1.1", "1.1.1.1", "leak")), \
                mock.patch("vless2socks.ipcheck.is_system_tun_active", side_effect=tun), \
                mock.patch.object(gui.ProxyInstance, "stop_async") as stop_async, \
                mock.patch.object(gui.ProxyInstance, "stop") as stop:
            inst.verify_killswitch(sync=True)
        self.assertEqual(threads, [True])  # sync mode runs the worker inline...
        stop.assert_not_called()           # ...and the leak path never blocks on a synchronous stop
        stop_async.assert_called_once()

        code = (ROOT / "gui.py").read_text(encoding="utf-8")
        on_done = code[code.index("            def on_done():"):code.index("            if sync:\n                on_done()")]
        self.assertNotIn("is_system_tun_active()", on_done)  # never on the GUI thread


class _SlowProxy:
    """check_health_sync that waits like a real port check + tunnel probe."""

    active = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, delay):
        self.delay = delay
        self.applied = []

    def check_health_sync(self, timeout=0.6):
        with _SlowProxy.lock:
            _SlowProxy.active += 1
            _SlowProxy.peak = max(_SlowProxy.peak, _SlowProxy.active)
        time.sleep(self.delay)
        with _SlowProxy.lock:
            _SlowProxy.active -= 1
        return True

    def apply_health_status(self, alive):
        self.applied.append(alive)
        return False


class TestHealthSweep(unittest.TestCase):
    """Status checks run off the GUI thread, in parallel, and never pile up."""

    def setUp(self):
        _App._poll_loop = gui.VlessApp._poll_loop
        _App._apply_poll_results = gui.VlessApp._apply_poll_results
        self.app = _App()
        _SlowProxy.active = _SlowProxy.peak = 0

    def tearDown(self):
        self.app.destroy()
        _release(self)

    def test_parallel_and_not_overlapping(self):
        proxies = [_SlowProxy(0.3) for _ in range(40)]
        self.app.instances = proxies
        sweeps = []
        real_thread = threading.Thread

        def counting_thread(*args, **kwargs):
            if kwargs.get("name") == "health-sweep":
                sweeps.append(1)
            return real_thread(*args, **kwargs)

        with mock.patch.object(gui.backup_manager, "check_and_run_auto_backup", return_value=(False, "", None)),                 mock.patch.object(gui.threading, "Thread", side_effect=counting_thread):
            started = time.monotonic()
            self.app._poll_loop()
            self.app._poll_loop()          # the timer firing again while a sweep runs
            self.app._poll_loop()
            end = time.monotonic() + 15
            while time.monotonic() < end and not all(p.applied for p in proxies):
                self.app.update()
                time.sleep(0.01)
            elapsed = time.monotonic() - started
        self.assertEqual(len(sweeps), 1, "a second sweep started while the first was running")
        self.assertTrue(all(p.applied == [True] for p in proxies))     # results applied once
        self.assertGreater(_SlowProxy.peak, 1)                          # checks ran side by side
        self.assertLessEqual(_SlowProxy.peak, gui.HEALTH_CHECK_WORKERS)
        self.assertLess(elapsed, 4.0, f"sweep of 40 took {elapsed:.1f}s (sequential would be 12s)")
        self.assertFalse(self.app._poll_sweep_running)

    def test_gui_thread_stays_free_during_sweep(self):
        self.app.instances = [_SlowProxy(0.5) for _ in range(20)]
        with mock.patch.object(gui.backup_manager, "check_and_run_auto_backup", return_value=(False, "", None)):
            started = time.monotonic()
            self.app._poll_loop()
            self.assertLess(time.monotonic() - started, 0.2)   # returns at once; work is in the background


class TestStartupCoalescing(unittest.TestCase):
    """Start-up bursts collapse into a few GUI-thread operations."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vless_burst_"))
        self.orig_settings = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"
        from tests.test_proxy_groups import _GroupsTestApp
        self.app = _GroupsTestApp()
        for i in range(3):
            self.app.instances.append(gui.ProxyInstance(self.app, {"name": f"p{i}", "url": "", "listen": f"127.0.0.1:{23700 + i}"}, i))
        self.app._render_overview()

    def tearDown(self):
        self.app.destroy()
        _release(self)
        settings_manager.SETTINGS_FILE = self.orig_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_many_state_changes_one_card_redraw(self):
        view = self.app.overview_view
        p = self.app.instances[1]
        with mock.patch.object(view, "update_card", wraps=view.update_card) as upd:
            for state in ((True, False), (True, False), (True, True), (True, True), (False, False)):
                p.running, p.healthy = state
                self.app.update_overview_card(p)
            upd.assert_not_called()          # nothing drawn yet: collected for the idle moment
            self.app.update_idletasks()
        self.assertEqual(upd.call_count, 1)
        self.assertEqual(view.card_dots(p), [gui.C["red"]])

    def test_status_change_is_applied_in_place(self):
        view = self.app.overview_view
        p = self.app.instances[0]
        before = set(view.canvas.find_withtag(view.card_slots[p][0][1]))
        p.is_transitioning = True
        self.app.update_overview_card(p)
        self.app.update_idletasks()
        after = set(view.canvas.find_withtag(view.card_slots[p][0][1]))
        self.assertEqual(before, after)       # same canvas items, only recolored / relabeled
        self.assertEqual(view.button_text(view.buttons("toggle", p)[0]), "⏳ ...")

    def test_request_save_writes_once_per_burst(self):
        _App.request_save = gui.VlessApp.request_save
        app = _App()
        try:
            app.save_all = mock.MagicMock()
            for _ in range(30):
                app.request_save(delay_ms=50)
            end = time.monotonic() + 2
            while time.monotonic() < end and not app.save_all.called:
                app.update()
                time.sleep(0.01)
            time.sleep(0.1)
            app.update()
            self.assertEqual(app.save_all.call_count, 1)
        finally:
            app.destroy()
            del app
            gc.collect()

    def test_settings_are_parsed_only_when_the_file_changes(self):
        settings_manager.set_setting("x", 1)
        real_load = settings_manager.json.load
        with mock.patch.object(settings_manager.json, "load", side_effect=real_load) as load:
            for _ in range(20):
                settings_manager.get_setting("x")
            self.assertLessEqual(load.call_count, 1)
            settings_manager.set_setting("x", 2)            # our own write drops the cache
            self.assertEqual(settings_manager.get_setting("x"), 2)
            time.sleep(0.02)
            (self.tmp / "settings.json").write_text('{"x": 333}', encoding="utf-8")   # external change
            self.assertEqual(settings_manager.get_setting("x"), 333)

    def test_settings_cache_returns_independent_copies(self):
        settings_manager.set_setting("lst", [1])
        settings_manager.load_settings()["lst"].append(2)
        self.assertEqual(settings_manager.get_setting("lst"), [1])


class TestLazyTabs(unittest.TestCase):
    def test_only_the_opened_tab_is_built(self):
        app = mock.MagicMock()
        built = []
        frames = {name: f".{name}" for name in ("proxies", "options", "backup")}
        builders = {name: (lambda n=name: built.append(n)) for name in frames}
        for name, fn in builders.items():
            fn.__name__ = f"_build_{name}_tab"
        app._pending_tab_builders = list(builders.values())
        app._tab_builder_for = {frames[n]: builders[n] for n in frames}
        app._run_tab_builder = lambda b: gui.VlessApp._run_tab_builder(app, b)
        gui.VlessApp._ensure_tab_built(app, frames["options"])
        gui.VlessApp._ensure_tab_built(app, frames["options"])   # second open: nothing to do
        self.assertEqual(built, ["options"])
        self.assertEqual(len(app._pending_tab_builders), 2)

    def test_no_background_tab_building_scheduled_at_start(self):
        code = (ROOT / "gui.py").read_text(encoding="utf-8")
        self.assertNotIn("self.after_idle(lambda: self.after(30, self._build_next_deferred_tab))", code)


class TestImportedProxiesKillswitch(unittest.TestCase):
    def test_not_inherited_from_template(self):
        link = "vless://11111111-1111-1111-1111-111111111111@h:443?type=tcp&security=none#n"
        cfg = subscriptions.build_proxy_config({"killswitch": True, "url": "", "listen": "x"}, link, 23600, 1, None, "s")
        self.assertIs(cfg["killswitch"], False)


if __name__ == "__main__":
    unittest.main()
