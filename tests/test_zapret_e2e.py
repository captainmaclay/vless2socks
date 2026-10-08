"""
End-to-End Simulation Test for Zapret2 Watcher retry and recovery logic.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ZapretRecovery
from ZapretRecovery.watcher import Zapret2Watcher


class TestZapret2WatcherE2E(unittest.TestCase):

    def test_watcher_detects_failure_and_recovers_services(self):
        """Watcher фиксирует сбой, вызывает retry восстановления и сбрасывает счетчик."""
        main_state = [False]  # starts down
        tg_state = [False]    # starts down
        start_main_calls = []
        start_tg_calls = []

        def mock_is_main():
            return main_state[0]

        def mock_is_tg():
            return tg_state[0]

        def mock_start_main():
            start_main_calls.append(time.time())
            main_state[0] = True  # recovered
            return True

        def mock_start_tg():
            start_tg_calls.append(time.time())
            tg_state[0] = True    # recovered
            return True

        with patch("ZapretRecovery.watcher.core.is_winws2_running", side_effect=mock_is_main), \
             patch("ZapretRecovery.watcher.core.is_tg_proxy_running", side_effect=mock_is_tg), \
             patch("ZapretRecovery.watcher.core.get_configured_telegram_proxies", return_value=[{"host": "127.0.0.1", "port": 1081}]), \
             patch("ZapretRecovery.watcher.core.is_endpoint_reachable", side_effect=lambda h, p: mock_is_tg()), \
             patch("ZapretRecovery.watcher.core.detect_tg_proxy_relay_error", return_value=(False, "")), \
             patch("ZapretRecovery.watcher.core.restart_tg_proxy", side_effect=mock_start_tg), \
             patch("ZapretRecovery.watcher.core.start_winws2", side_effect=mock_start_main), \
             patch("ZapretRecovery.watcher.core.start_tg_proxy", side_effect=mock_start_tg):

            events = []
            watcher = Zapret2Watcher(
                enabled=True,
                watch_main=True,
                watch_tg=True,
                status_callback=lambda s: events.append(dict(s)),
            )

            # Override sleep schedule for fast test execution
            watcher.RETRY_SCHEDULE = [0.05, 0.1, 0.2]
            watcher.NORMAL_POLL_INTERVAL = 0.1

            watcher.start()
            time.sleep(0.3)
            watcher.stop()

            # Verify that recovery was triggered
            self.assertGreaterEqual(len(start_main_calls), 1)
            self.assertGreaterEqual(len(start_tg_calls), 1)
            self.assertTrue(main_state[0])
            self.assertTrue(tg_state[0])

            final_state = watcher.get_state()
            self.assertTrue(final_state["main_ok"])
            self.assertTrue(final_state["tg_ok"])
            self.assertFalse(final_state["recovering"])
            self.assertEqual(final_state["retry_count"], 0)

    def test_watcher_disabled_ignores_failures(self):
        """Когда свитчер OFF (enabled=False), восстановление НЕ вызывается."""
        start_main_calls = []
        start_tg_calls = []

        with patch("ZapretRecovery.watcher.core.is_winws2_running", return_value=False), \
             patch("ZapretRecovery.watcher.core.is_tg_proxy_running", return_value=False), \
             patch("ZapretRecovery.watcher.core.start_winws2", side_effect=lambda: start_main_calls.append(1)), \
             patch("ZapretRecovery.watcher.core.start_tg_proxy", side_effect=lambda: start_tg_calls.append(1)):

            watcher = Zapret2Watcher(
                enabled=False,  # OFF
                watch_main=True,
                watch_tg=True,
            )
            watcher.RETRY_SCHEDULE = [0.05]
            watcher.NORMAL_POLL_INTERVAL = 0.05

            watcher.start()
            time.sleep(0.15)
            watcher.stop()

            self.assertEqual(len(start_main_calls), 0)
            self.assertEqual(len(start_tg_calls), 0)


if __name__ == "__main__":
    unittest.main()
