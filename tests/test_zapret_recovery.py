"""
Comprehensive automated tests for ZapretRecovery module in vless2socks.
Tests:
1. Settings persistence and backup/restore integration
2. Widget UI, ON/OFF switcher, dim-green/dim-gray styles
3. Dropdown slide-down menu and OrangeCheckbox toggling
4. Fix for minimize bug (no orphan floating window when app minimized)
5. Watcher retry logic and backoff schedule
"""

import io
import json
import os
import sys
import time
import tkinter as tk
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backup_manager
import settings_manager
import ZapretRecovery
from ZapretRecovery.widget import ZapretRecoveryWidget, OrangeCheckbox, THEME
from ZapretRecovery.watcher import Zapret2Watcher


class TestZapretRecovery(unittest.TestCase):

    def setUp(self):
        # Reset settings to default before each test
        settings_manager.set_zapret2_settings(enabled=True, watch_main=True, watch_tg=True)

    def tearDown(self):
        settings_manager.set_zapret2_settings(enabled=True, watch_main=True, watch_tg=True)

    def test_settings_and_backup_integration(self):
        """Проверка попадания состояния свитчера и чекбоксов в настройки и бэкапы."""
        # 1. Значения по умолчанию
        cfg = settings_manager.get_zapret2_settings()
        self.assertTrue(cfg["enabled"])
        self.assertTrue(cfg["watch_main"])
        self.assertTrue(cfg["watch_tg"])

        # 2. Изменение настроек
        settings_manager.set_zapret2_settings(enabled=False, watch_main=True, watch_tg=False)
        cfg_updated = settings_manager.get_zapret2_settings()
        self.assertFalse(cfg_updated["enabled"])
        self.assertTrue(cfg_updated["watch_main"])
        self.assertFalse(cfg_updated["watch_tg"])

        # 3. Проверка включения в архив бэкапа
        archive_bytes = backup_manager.create_backup_archive()
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
        self.assertIn("settings.json", zf.namelist())

        settings_in_backup = json.loads(zf.read("settings.json").decode("utf-8"))
        self.assertFalse(settings_in_backup["zapret2_watcher_enabled"])
        self.assertTrue(settings_in_backup["zapret2_watch_main"])
        self.assertFalse(settings_in_backup["zapret2_watch_tg"])

    def test_widget_switcher_and_orange_checkboxes(self):
        """Проверка работы свитчера ON/OFF, стилей и оранжевых чекбоксов."""
        root = tk.Tk()
        root.withdraw()

        try:
            widget = ZapretRecoveryWidget(root)
            self.assertEqual(widget.btn_switcher.cget("text"), "ON")
            self.assertEqual(widget.btn_switcher.cget("bg"), THEME["dim_green"])

            # Переключение в OFF
            widget._toggle_switcher()
            self.assertFalse(widget.enabled_var.get())
            self.assertEqual(widget.btn_switcher.cget("text"), "OFF")
            self.assertEqual(widget.btn_switcher.cget("bg"), THEME["off_bg"])

            # Переключение обратно в ON
            widget._toggle_switcher()
            self.assertTrue(widget.enabled_var.get())
            self.assertEqual(widget.btn_switcher.cget("text"), "ON")
            self.assertEqual(widget.btn_switcher.cget("bg"), THEME["dim_green"])

            # Тест OrangeCheckbox
            bool_var = tk.BooleanVar(value=True)
            cb = OrangeCheckbox(root, variable=bool_var, orange_color="#ff7700")
            self.assertTrue(bool_var.get())
            cb._toggle()
            self.assertFalse(bool_var.get())
            cb._toggle()
            self.assertTrue(bool_var.get())
        finally:
            root.destroy()

    def test_minimize_bug_fix(self):
        """Проверка исправления бага: меню закрывается при сворачивании приложения."""
        root = tk.Tk()
        root.withdraw()

        try:
            widget = ZapretRecoveryWidget(root)
            root.deiconify()
            root.update()

            # Открываем контекстное меню
            widget.open_menu()
            self.assertTrue(widget._menu_open)
            self.assertIsNotNone(widget.menu_win)
            self.assertTrue(widget.menu_win.winfo_exists())

            # Симулируем сворачивание окна (генерация события <Unmap> на root)
            root.iconify()
            root.event_generate("<Unmap>")
            root.update()

            # Проверяем, что выплывающее меню ЗАКРЫЛОСЬ и уничтожилось
            self.assertFalse(widget._menu_open)
            self.assertIsNone(widget.menu_win)
        finally:
            root.destroy()

    def test_watcher_retry_backoff(self):
        """Проверка логики retry восстановления с backoff расписанием."""
        status_updates = []

        def on_update(st):
            status_updates.append(st)

        # Мокаем core: winws2 упал, telegram proxy упал
        with patch("ZapretRecovery.watcher.core.is_winws2_running", return_value=False), \
             patch("ZapretRecovery.watcher.core.is_tg_proxy_running", return_value=False), \
             patch("ZapretRecovery.watcher.core.start_winws2", return_value=True), \
             patch("ZapretRecovery.watcher.core.start_tg_proxy", return_value=True):

            watcher = Zapret2Watcher(
                enabled=True,
                watch_main=True,
                watch_tg=True,
                status_callback=on_update,
            )

            # Проверяем расписание (короткие циклы: 1с, 5с, максимум 8с)
            self.assertEqual(watcher.RETRY_SCHEDULE[0], 1.0)
            self.assertEqual(watcher.RETRY_SCHEDULE[1], 5.0)
            self.assertEqual(watcher.RETRY_SCHEDULE[-1], 8.0)
            self.assertEqual(watcher.NORMAL_POLL_INTERVAL, 8.0)

            state = watcher.get_state()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["retry_count"], 0)

    def test_telegram_proxy_display_target_undefined_and_selected(self):
        """Проверка генерации строки цели Telegram Proxy (undefined vs хост:порт)."""
        import ZapretRecovery.core as core

        # 1. Когда нет зафлаганных прокси
        with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=[]):
            self.assertEqual(core.get_telegram_proxy_display_target("ru"), "не выбрано")
            self.assertEqual(core.get_telegram_proxy_display_target("en"), "undefined")

        # 2. Когда зафлаган 1 прокси
        mock_proxies = [{"host": "127.0.0.1", "port": 1085, "name": "TG-Main"}]
        with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=mock_proxies):
            self.assertEqual(core.get_telegram_proxy_display_target("ru"), "127.0.0.1:1085")
            self.assertEqual(core.get_telegram_proxy_display_target("en"), "127.0.0.1:1085")

        # 3. Когда зафлагано несколько прокси
        mock_multiple = [
            {"host": "127.0.0.1", "port": 1081},
            {"host": "127.0.0.1", "port": 1082},
        ]
        with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=mock_multiple):
            self.assertEqual(core.get_telegram_proxy_display_target("ru"), "127.0.0.1:1081 (+1)")

    def test_widget_dropdown_shows_undefined_or_selected_port(self):
        """Проверка надписи в выпадающем меню Telegram Proxy: (не выбрано) / (127.0.0.1:XXXX)."""
        root = tk.Tk()
        root.deiconify()
        root.update()

        try:
            # Сценарий A: ни один прокси не выбран (русская локаль)
            with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=[]), \
                 patch("i18n.get_current_language", return_value="ru"):
                widget = ZapretRecoveryWidget(root)
                widget.pack()
                root.update()
                widget.open_menu()
                self.assertTrue(widget._menu_open)
                # Ищем метку Telegram Proxy внутри открытого меню
                found_text = None
                for child in widget.menu_win.winfo_children():
                    for sub in child.winfo_children():
                        for row in sub.winfo_children():
                            if isinstance(row, tk.Frame):
                                for lbl in row.winfo_children():
                                    if isinstance(lbl, tk.Label) and "Telegram Proxy" in lbl.cget("text"):
                                        found_text = lbl.cget("text")
                self.assertIsNotNone(found_text)
                self.assertIn("Telegram Proxy (не выбрано)", found_text)
                widget.close_menu()

            # Сценарий B: ни один прокси не выбран (английская локаль)
            with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=[]), \
                 patch("i18n.get_current_language", return_value="en"):
                widget2 = ZapretRecoveryWidget(root)
                widget2.pack()
                root.update()
                widget2.open_menu()
                self.assertTrue(widget2._menu_open)
                found_text_en = None
                for child in widget2.menu_win.winfo_children():
                    for sub in child.winfo_children():
                        for row in sub.winfo_children():
                            if isinstance(row, tk.Frame):
                                for lbl in row.winfo_children():
                                    if isinstance(lbl, tk.Label) and "Telegram Proxy" in lbl.cget("text"):
                                        found_text_en = lbl.cget("text")
                self.assertIsNotNone(found_text_en)
                self.assertIn("Telegram Proxy (undefined)", found_text_en)
                widget2.close_menu()

            # Сценарий C: выбран прокси с портом 1099
            mock_proxies = [{"host": "127.0.0.1", "port": 1099}]
            with patch("ZapretRecovery.core.get_configured_telegram_proxies", return_value=mock_proxies):
                widget3 = ZapretRecoveryWidget(root)
                widget3.pack()
                root.update()
                widget3.open_menu()
                self.assertTrue(widget3._menu_open)
                found_text3 = None
                for child in widget3.menu_win.winfo_children():
                    for sub in child.winfo_children():
                        for row in sub.winfo_children():
                            if isinstance(row, tk.Frame):
                                for lbl in row.winfo_children():
                                    if isinstance(lbl, tk.Label) and "Telegram Proxy" in lbl.cget("text"):
                                        found_text3 = lbl.cget("text")
                self.assertIsNotNone(found_text3)
                self.assertIn("Telegram Proxy (127.0.0.1:1099)", found_text3)
                widget3.close_menu()
        finally:
            root.destroy()

    def test_watcher_does_not_recover_tg_when_none_selected(self):
        """Если ни один TG прокси не выбран, Watcher НЕ пытается его восстанавливать."""
        mock_restart_handler = MagicMock()
        watcher = Zapret2Watcher(
            enabled=True,
            watch_main=False,
            watch_tg=True,
            tg_proxy_provider=lambda: [],
            tg_restart_handler=mock_restart_handler,
        )

        with patch("ZapretRecovery.watcher.core.is_winws2_running", return_value=True), \
             patch("i18n.get_current_language", return_value="ru"):
            watcher.start()
            time.sleep(0.15)
            watcher.stop()

        state = watcher.get_state()
        self.assertFalse(state["has_tg_target"])
        self.assertEqual(state["tg_target_display"], "не выбрано")
        self.assertFalse(state["recovering"])
        self.assertEqual(state["retry_count"], 0)
        mock_restart_handler.assert_not_called()

    def test_watcher_recovers_configured_vless2socks_telegram_proxy(self):
        """Если TG прокси выбран и упал, Watcher вызывает кастомный tg_restart_handler."""
        mock_restart_handler = MagicMock()
        mock_proxy = {"host": "127.0.0.1", "port": 1081, "running": False}

        watcher = Zapret2Watcher(
            enabled=True,
            watch_main=False,
            watch_tg=True,
            tg_proxy_provider=lambda: [mock_proxy],
            tg_restart_handler=mock_restart_handler,
        )

        with patch("ZapretRecovery.watcher.core.is_endpoint_reachable", return_value=False), \
             patch("ZapretRecovery.watcher.core.is_winws2_running", return_value=True):
            watcher.start()
            time.sleep(0.2)
            watcher.stop()

        # Рестарт должен был быть вызван
        self.assertTrue(mock_restart_handler.called)
        args, _ = mock_restart_handler.call_args
        self.assertEqual(args[0], [mock_proxy])


if __name__ == "__main__":
    unittest.main()

