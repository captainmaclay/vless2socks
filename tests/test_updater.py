"""Updates from GitHub Releases: version compare, release parsing, download with size /
SHA-256 checks, the swap batch file (run for real on a harmless .cmd), and the UI: the
violet "Update version" button in the header (left of RestartServices, hidden while up to
date) and on the Updates tab (grey "Check for updates" otherwise), the 15-minute check.

A local HTTP server plays GitHub; the UI part runs in the sandbox."""

import hashlib
import http.server
import json
import os
import socketserver
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

import tests  # noqa: F401  (isolated data paths)
import updater

EXE = b"MZ fake vless2socks exe " * 1000


def release_json(port, tag="v9.9.9", sha=None, **extra):
    data = {
        "tag_name": tag, "html_url": "https://example.com/rel", "draft": False, "prerelease": False,
        "body": f"notes\nSHA-256 exe: {sha or hashlib.sha256(EXE).hexdigest().upper()}\n",
        "assets": [{"name": "vless2socks.exe", "size": len(EXE),
                    "browser_download_url": f"http://127.0.0.1:{port}/vless2socks.exe"},
                   {"name": "vless2socks-win64.zip", "size": 1, "browser_download_url": "x"}],
    }
    data.update(extra)
    return data


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.status = 200
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/latest"):
                    if owner.status != 200:
                        self.send_response(owner.status)
                        self.end_headers()
                        return
                    body = json.dumps(owner.release).encode()
                elif self.path == "/vless2socks.exe":
                    body = EXE
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.api = f"http://127.0.0.1:{self.port}/latest"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class VersionTest(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.3"), (1, 3, 0))
        self.assertEqual(updater.parse_version("latest"), ())
        self.assertTrue(updater.is_newer("v1.2.10", "1.2.9"))
        self.assertFalse(updater.is_newer("v1.2.2", "1.2.2"))
        self.assertFalse(updater.is_newer("v1.2.1", "1.2.2"))
        self.assertFalse(updater.is_newer("garbage", "1.2.2"))

    def test_release_parsing(self):
        info = updater.parse_release(release_json(1))
        self.assertEqual((info.version, info.tag, info.exe_size), ("9.9.9", "v9.9.9", len(EXE)))
        self.assertEqual(info.sha256, hashlib.sha256(EXE).hexdigest())
        self.assertIsNone(updater.parse_release(release_json(1, draft=True)))
        self.assertIsNone(updater.parse_release(release_json(1, prerelease=True)))
        self.assertIsNone(updater.parse_release(release_json(1, assets=[])))


class CheckAndDownloadTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.gh.release = release_json(self.gh.port)
        self.tmp = tempfile.mkdtemp(prefix="v2s_upd_")

    def tearDown(self):
        self.gh.close()

    def test_newer_release_is_available(self):
        r = updater.check(current="1.2.2", api_url=self.gh.api)
        self.assertTrue(r.available)
        self.assertEqual((r.error, r.route, r.latest.version), ("", "direct", "9.9.9"))

    def test_same_version_is_not(self):
        self.gh.release = release_json(self.gh.port, tag="v1.2.2")
        self.assertFalse(updater.check(current="1.2.2", api_url=self.gh.api).available)

    def test_no_release_yet_and_server_errors(self):
        self.gh.status = 404
        r = updater.check(current="1.2.2", api_url=self.gh.api)
        self.assertEqual((r.available, r.error, r.latest), (False, "", None))
        self.gh.status = 500
        r = updater.check(current="1.2.2", api_url=self.gh.api)
        self.assertFalse(r.available)
        self.assertIn("HTTP 500", r.error)

    def test_download_verifies_size_and_hash(self):
        info = updater.check(current="1.0.0", api_url=self.gh.api).latest
        dest = os.path.join(self.tmp, "new.exe")
        seen = []
        updater.download(info, dest, progress=lambda a, b: seen.append((a, b)))
        self.assertEqual(Path(dest).read_bytes(), EXE)
        self.assertEqual(seen[-1], (len(EXE), len(EXE)))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_bad_hash_is_refused_and_nothing_is_left(self):
        self.gh.release = release_json(self.gh.port, sha="0" * 64)
        info = updater.check(current="1.0.0", api_url=self.gh.api).latest
        dest = os.path.join(self.tmp, "new.exe")
        with self.assertRaises(updater.UpdateError):
            updater.download(info, dest)
        self.assertFalse(os.path.exists(dest) or os.path.exists(dest + ".part"))

    def test_falls_back_to_a_proxy_route(self):
        calls = []
        real_open = updater._open

        def fake_open(url, proxy, timeout, accept="*/*"):
            calls.append(proxy)
            if proxy is None:
                raise OSError("blocked")
            return real_open(url, None, timeout, accept)

        with mock.patch.object(updater, "_open", fake_open):
            r = updater.check(["http://127.0.0.1:11015"], current="1.0.0", api_url=self.gh.api)
        self.assertEqual(calls, [None, "http://127.0.0.1:11015"])
        self.assertEqual(r.route, "http://127.0.0.1:11015")


class SwapScriptTest(unittest.TestCase):
    def test_swap_runs_for_real(self):
        """The batch file waits for the PID, keeps .old, moves the new file in, starts it."""
        d = tempfile.mkdtemp(prefix="v2s_swap_")
        target = os.path.join(d, "app.cmd")
        new = os.path.join(d, "new.cmd")
        marker = os.path.join(d, "started.txt")
        Path(target).write_text("@echo old> \"%~dp0started.txt\"\r\n", encoding="ascii")
        Path(new).write_text("@echo new> \"%~dp0started.txt\"\r\n", encoding="ascii")
        waiter = subprocess.Popen(["cmd.exe", "/c", "ping -n 3 127.0.0.1 >nul"],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        script = updater.launch_swap(new, target, pid=waiter.pid)
        time.sleep(0.8)
        self.assertFalse(os.path.exists(marker), "swapped before the program exited")
        waiter.wait(10)
        for _ in range(60):
            if os.path.exists(marker):
                break
            time.sleep(0.25)
        self.assertEqual(Path(marker).read_text().strip(), "new")
        self.assertTrue(os.path.exists(target + ".old"))
        self.assertFalse(os.path.exists(new))
        for _ in range(20):
            if not os.path.exists(script):
                break
            time.sleep(0.25)
        self.assertFalse(os.path.exists(script), "the script did not delete itself")


class UpdateUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.ui_sandbox import Sandbox
        cls.sb = Sandbox(language="ru")
        cls.app = cls.sb.app
        cls.app.deiconify()
        cls.app.geometry("1500x900+40+40")
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

    def result(self, available, version="9.9.9", error=""):
        info = updater.ReleaseInfo(version=version, tag="v" + version, page_url="https://example.com",
                                   exe_url="x", exe_size=1)
        return updater.CheckResult(current="1.2.2", latest=info, available=available, error=error)

    def test_tab_comes_after_localization(self):
        tabs = [str(t) for t in self.app.nav_notebook.tabs()]
        self.assertEqual(tabs.index(str(self.app.update_frame)), tabs.index(str(self.app.loc_frame)) + 1)

    def test_up_to_date_hides_header_button_and_tab_button_is_grey(self):
        self.app._on_update_result(self.result(False, version="1.2.2"))
        self.sb.settle(5)
        self.assertEqual(self.app.btn_update_version.winfo_manager(), "")
        self.assertEqual(self.app.upd_tab_btn.cget("variant"), "secondary")
        self.assertEqual(self.app.upd_tab_btn.cget("text"), self.gui.t("btn_check_updates"))

    def test_new_release_shows_violet_buttons(self):
        self.app._on_update_result(self.result(True))
        self.sb.settle(10)
        hdr = self.app.btn_update_version
        self.assertEqual(hdr.winfo_manager(), "pack")
        self.assertEqual(hdr.cget("variant"), "violet")
        self.assertIn("9.9.9", hdr.cget("text"))
        restart = self.app.restart_container
        self.assertLess(hdr.winfo_rootx() + hdr.winfo_width(), restart.winfo_rootx() + 1)   # left of it
        self.assertEqual(self.app.upd_tab_btn.cget("variant"), "violet")
        self.assertIn(self.gui.t("btn_update_version"), self.app.upd_tab_btn.cget("text"))
        # and back to grey once up to date again
        self.app._on_update_result(self.result(False, version="9.9.9"))
        self.sb.settle(5)
        self.assertEqual(hdr.winfo_manager(), "")
        self.assertEqual(self.app.upd_tab_btn.cget("variant"), "secondary")

    def test_header_still_fits_at_a_narrow_width_with_the_button(self):
        from tests.layout_audit import audit
        self.app._on_update_result(self.result(True))
        try:
            for w in (1100, 900):
                self.app.geometry(f"{w}x800")
                self.sb.settle(25)
                self.assertEqual(audit(self.app), [], f"width {w}")
        finally:
            self.app.geometry("1500x900")
            self.app._on_update_result(self.result(False, version="1.2.2"))
            self.sb.settle(10)

    def test_failed_check_does_not_show_the_button(self):
        self.app._on_update_result(self.result(True, error="GitHub недоступен"))
        self.sb.settle(5)
        self.assertEqual(self.app.btn_update_version.winfo_manager(), "")
        self.assertIn("GitHub", self.app.upd_status_lbl.cget("text"))

    def test_manual_check_runs_in_background_and_updates_the_ui(self):
        with mock.patch.object(updater, "check", return_value=self.result(True, version="9.9.8")) as chk:
            self.app.upd_tab_btn.invoke()
            for _ in range(40):
                self.sb.settle(2)
                if self.app.btn_update_version.winfo_manager():
                    break
        chk.assert_called_once()
        self.assertIn("9.9.8", self.app.btn_update_version.cget("text"))
        self.app._on_update_result(self.result(False, version="1.2.2"))

    def test_periodic_check_every_15_minutes(self):
        self.assertEqual(self.gui.UPDATE_CHECK_INTERVAL_MS, 15 * 60 * 1000)
        with mock.patch.object(self.app, "check_updates_now") as now, \
                mock.patch.object(self.app, "after") as after:
            self.app._update_check_tick()
        now.assert_called_once_with(manual=False)
        after.assert_called_once_with(self.gui.UPDATE_CHECK_INTERVAL_MS, self.app._update_check_tick)

    def test_update_from_source_tree_offers_the_release_page(self):
        self.app._on_update_result(self.result(True))
        with mock.patch.object(self.gui, "FROZEN", False), \
                mock.patch.object(self.gui.messagebox, "askyesno", return_value=False) as ask, \
                mock.patch.object(updater, "download") as dl:
            self.app.start_update()
        ask.assert_called_once()
        dl.assert_not_called()
        self.app._on_update_result(self.result(False, version="1.2.2"))

    def test_update_from_exe_downloads_swaps_and_exits(self):
        self.app._on_update_result(self.result(True))
        with mock.patch.object(self.gui, "FROZEN", True), \
                mock.patch.object(self.gui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(updater, "download") as dl, \
                mock.patch.object(updater, "launch_swap") as swap, \
                mock.patch.object(self.app, "_exit") as exit_:
            self.app.start_update()
            for _ in range(60):
                self.sb.settle(2)
                if exit_.called:
                    break
        dl.assert_called_once()
        swap.assert_called_once()
        self.assertEqual(swap.call_args[0][1], sys.executable)
        exit_.assert_called_once()
        self.app._update_downloading = False
        self.app._on_update_result(self.result(False, version="1.2.2"))


if __name__ == "__main__":
    unittest.main()
