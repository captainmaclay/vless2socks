import unittest
from unittest.mock import MagicMock, patch, ANY
import threading
import time
import tkinter as tk

from gui import ProxyInstance, VlessApp, C, is_port_alive


class AsyncGuiOperationsTest(unittest.TestCase):
    def setUp(self):
        self.mock_app = MagicMock(spec=VlessApp)
        self.mock_app.after = MagicMock(side_effect=lambda delay, fn=None, *args: fn(*args) if fn else None)
        self.mock_app.instances = []
        self.mock_app._overview_cards = {}
        self.mock_app._cached_instances_signature = None

        self.cfg = {
            "name": "Async Test Proxy",
            "url": "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=tls#Test",
            "listen": "127.0.0.1:21080",
            "http_port": 31080,
            "order": 1.0,
        }
        self.inst = ProxyInstance(self.mock_app, self.cfg, 0)
        self.inst.status_label = MagicMock()
        self.inst.toggle_btn = MagicMock()

    def test_start_async_non_blocking_and_callback(self):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = lambda: time.sleep(5)

        callback_called = threading.Event()
        callback_result = []

        def on_started(success):
            callback_result.append(success)
            callback_called.set()

        with patch("gui.subprocess.Popen", return_value=mock_proc), \
             patch("gui.kill_processes_on_port"), \
             patch("gui.is_port_free", return_value=True), \
             patch("gui.xray_required_but_missing", return_value=False), \
             patch("builtins.open", unittest.mock.mock_open()):

            start_time = time.time()
            self.inst.start_async(callback=on_started)
            # Must return immediately (non-blocking)
            self.assertLess(time.time() - start_time, 0.2)

            self.assertTrue(callback_called.wait(timeout=2.0))
            self.assertEqual(callback_result, [True])
            self.assertTrue(self.inst.running)
            self.assertFalse(self.inst.is_transitioning)
            self.assertEqual(self.inst.process, mock_proc)
            self.mock_app.refresh_overview.assert_called()

    def test_stop_async_non_blocking_and_callback(self):
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None
        self.inst.process = mock_proc
        self.inst.running = True
        self.inst.healthy = True

        callback_called = threading.Event()

        with patch("gui.subprocess.run"), \
             patch("gui.kill_processes_on_port"), \
             patch("geo_ip.invalidate_cache"):

            start_time = time.time()
            self.inst.stop_async(callback=lambda: callback_called.set())
            # Must return immediately (non-blocking)
            self.assertLess(time.time() - start_time, 0.2)

            self.assertTrue(callback_called.wait(timeout=2.0))
            self.assertFalse(self.inst.running)
            self.assertFalse(self.inst.healthy)
            self.assertFalse(self.inst.is_transitioning)
            self.assertIsNone(self.inst.process)
            self.mock_app.refresh_overview.assert_called()

    def test_toggle_async_behavior(self):
        with patch.object(self.inst, "start_async") as mock_start_async, \
             patch.object(self.inst, "stop_async") as mock_stop_async:

            # Case 1: Stopped -> calls start_async
            self.inst.running = False
            self.inst.process = None
            self.inst.toggle_async()
            mock_start_async.assert_called_once()
            mock_stop_async.assert_not_called()

            # Case 2: In transition -> ignores click
            self.inst.is_transitioning = True
            mock_start_async.reset_mock()
            self.inst.toggle_async()
            mock_start_async.assert_not_called()
            mock_stop_async.assert_not_called()
            self.inst.is_transitioning = False

            # Case 3: Running -> calls stop_async
            self.inst.running = True
            self.inst.toggle_async()
            mock_stop_async.assert_called_once()

    def test_start_all_and_stop_all_are_async(self):
        inst1 = MagicMock(spec=ProxyInstance)
        inst1.running = False
        inst1.process = None
        inst1.is_transitioning = False
        inst2 = MagicMock(spec=ProxyInstance)
        inst2.running = True
        inst2.process = MagicMock()
        inst2.is_transitioning = False

        app = MagicMock(spec=VlessApp)
        app.instances = [inst1, inst2]

        VlessApp.start_all(app)
        inst1.start_async.assert_called_once()
        inst2.start_async.assert_not_called()

        inst1.reset_mock()
        inst2.reset_mock()

        VlessApp.stop_all(app)
        inst1.stop_async.assert_not_called()
        inst2.stop_async.assert_called_once()

    def test_in_place_differential_card_updates(self):
        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError:
            self.skipTest("Tkinter display not available")

        try:
            content_frame = tk.Frame(root)
            real_app = MagicMock(spec=VlessApp)
            real_app.overview_content = content_frame
            real_app._overview_cards = {}
            real_app.instances = [self.inst]
            real_app._update_overview_card_widgets = lambda inst, data: VlessApp._update_overview_card_widgets(real_app, inst, data)
            real_app.prompt_reorder_proxy = MagicMock()
            real_app.prompt_rename_proxy = MagicMock()

            # First build: widgets should be created
            VlessApp.refresh_overview(real_app)
            self.assertIn(self.inst, real_app._overview_cards)
            card_dict = real_app._overview_cards[self.inst]
            card_widget = card_dict["card"]
            dot_widget = card_dict["dot"]
            self.assertTrue(card_widget.winfo_exists())

            # Verify initial stopped color (red)
            self.assertEqual(dot_widget.cget("fg"), C["red"])

            # Mutate state without recreating DOM
            self.inst.running = True
            self.inst.healthy = True
            initial_card_id = id(card_widget)

            # Second refresh: signature is identical, so it must update in-place without destroying
            VlessApp.refresh_overview(real_app)

            # Ensure the SAME widget instance exists and was updated
            self.assertEqual(id(real_app._overview_cards[self.inst]["card"]), initial_card_id)
            self.assertTrue(card_widget.winfo_exists())
            self.assertEqual(dot_widget.cget("fg"), C["green"])
        finally:
            root.destroy()

    def test_check_health_sync_and_apply_health_status(self):
        with patch("gui.is_port_alive", return_value=True):
            alive = self.inst.check_health_sync()
            self.assertTrue(alive)

        # Transition to healthy running
        self.inst.running = True
        self.inst.healthy = False
        changed = self.inst.apply_health_status(True)
        self.assertTrue(changed)
        self.assertTrue(self.inst.healthy)

        # No change on second call
        changed = self.inst.apply_health_status(True)
        self.assertFalse(changed)

        # Transition to dead
        changed = self.inst.apply_health_status(False)
        self.assertTrue(changed)
        self.assertFalse(self.inst.healthy)


if __name__ == "__main__":
    unittest.main()
