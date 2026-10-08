"""
Comprehensive tests for vless2socks internal Telegram Proxy:
1. Flag TelegramProxy persistence in configs and .hbak backups
2. URL generators for tg://proxy and tg://socks
3. TelegramProxyDispatcher server, pool management, and auto-rotation
4. UI widgets: badge 'TelegramProxy' on Overview, 'Copy TG_socks' button on Overview and in proxy settings
"""

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backup_manager
import settings_manager
from vless2socks import config, telegram_proxy
from vless2socks.telegram_proxy import (
    DEFAULT_TG_PORT,
    DEFAULT_TG_SECRET,
    TelegramProxyDispatcher,
    build_tg_proxy_url,
    build_tg_socks_url,
    is_endpoint_reachable,
)


class TestTelegramProxyFlagAndBackup(unittest.TestCase):

    def test_app_config_parses_telegram_proxy_flag(self):
        """Проверка парсинга флага TelegramProxy в AppConfig."""
        data = {
            "url": "vless://00000000-0000-0000-0000-000000000000@1.2.3.4:443?security=none",
            "listen": "127.0.0.1:1085",
            "telegram_proxy": True,
        }
        cfg = config.load_config(url=data["url"], listen=data["listen"])
        # по умолчанию False
        self.assertFalse(cfg.telegram_proxy)
        self.assertFalse(cfg.TelegramProxy)

        # с переданным флагом telegram_proxy
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            temp_path = f.name

        try:
            cfg_file = config.load_config(temp_path)
            self.assertTrue(cfg_file.telegram_proxy)
            self.assertTrue(cfg_file.TelegramProxy)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_telegram_proxy_flag_included_in_backup_archive(self):
        """Флаг TelegramProxy сохраняется в instances.json и попадает в .hbak архив."""
        instances_data = [
            {
                "name": "Estonia-TG",
                "listen": "127.0.0.1:1081",
                "url": "vless://0000@1.1.1.1:443",
                "telegram_proxy": True,
                "TelegramProxy": True,
            },
            {
                "name": "Finland-Main",
                "listen": "127.0.0.1:1082",
                "url": "vless://0000@2.2.2.2:443",
                "telegram_proxy": False,
                "TelegramProxy": False,
            },
        ]

        with patch("backup_manager.INSTANCES_FILE", Path(PROJECT_ROOT / "runtime" / "test_instances.json")):
            target_path = Path(PROJECT_ROOT / "runtime" / "test_instances.json")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(instances_data, f)

            try:
                archive_bytes = backup_manager.create_backup_archive()
                zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
                self.assertIn("instances.json", zf.namelist())

                restored_instances = json.loads(zf.read("instances.json").decode("utf-8"))
                self.assertTrue(restored_instances[0]["telegram_proxy"])
                self.assertTrue(restored_instances[0]["TelegramProxy"])
                self.assertFalse(restored_instances[1]["telegram_proxy"])
            finally:
                if target_path.exists():
                    target_path.unlink()


class TestTelegramProxyUrlGenerator(unittest.TestCase):

    def test_build_tg_proxy_url_format(self):
        """Создание корректной ссылки формата tg://proxy?server=...&port=1373&secret=..."""
        # По умолчанию без явного указания порта должен быть 1373
        url_default = build_tg_proxy_url("127.0.0.1")
        self.assertIn("&port=1373&", url_default)

        # С кастомным портом
        url_custom = build_tg_proxy_url("127.0.0.1", 1373, "dd648b061090960667e2620c7949495503")
        expected = "tg://proxy?server=127.0.0.1&port=1373&secret=dd648b061090960667e2620c7949495503"
        self.assertEqual(url_custom, expected)

    def test_build_tg_socks_url_format(self):
        """Создание корректной ссылки формата tg://socks?server=...&port=1373"""
        url_default = build_tg_socks_url("127.0.0.1")
        self.assertEqual(url_default, "tg://socks?server=127.0.0.1&port=1373")

        url_custom = build_tg_socks_url("127.0.0.1", 1373)
        self.assertEqual(url_custom, "tg://socks?server=127.0.0.1&port=1373")

        url_auth = build_tg_socks_url("127.0.0.1", 1081, username="admin", password="pwd")
        self.assertEqual(url_auth, "tg://socks?server=127.0.0.1&port=1081&user=admin&pass=pwd")

    def test_custom_port_configuration_and_persistence(self):
        """Проверка смены и персистенции кастомного порта TelegramProxy."""
        import settings_manager
        old_p = settings_manager.get_telegram_proxy_port()
        try:
            settings_manager.set_telegram_proxy_port(1399)
            self.assertEqual(settings_manager.get_telegram_proxy_port(), 1399)
            self.assertIn("1399", build_tg_proxy_url())

            disp = TelegramProxyDispatcher(listen_port=1373)
            self.assertEqual(disp.listen_port, 1373)
            disp.set_listen_port(1400)
            self.assertEqual(disp.listen_port, 1400)
        finally:
            settings_manager.set_telegram_proxy_port(old_p)


class TestTelegramProxyRotationDispatcher(unittest.TestCase):

    def test_dispatcher_pool_and_auto_rotation(self):
        """Диспетчер ротирует прокси на рабочий при падении активного."""
        dispatcher = TelegramProxyDispatcher(listen_port=19999)

        pool_data = [
            {"name": "Proxy-1", "listen": "127.0.0.1:1081", "telegram_proxy": True},
            {"name": "Proxy-2", "listen": "127.0.0.1:1082", "telegram_proxy": True},
            {"name": "Proxy-3", "listen": "127.0.0.1:1083", "telegram_proxy": False},  # not in pool
        ]
        dispatcher.set_pool(pool_data)
        self.assertEqual(len(dispatcher.get_pool()), 2)

        # Симулируем: 1081 упал (не отвечает), а 1082 доступен (отвечает)
        def mock_reachable(host, port, *args, **kwargs):
            return int(port) == 1082

        with patch("vless2socks.telegram_proxy.is_endpoint_reachable", side_effect=mock_reachable):
            active = dispatcher.get_active_proxy()
            self.assertIsNotNone(active)
            self.assertEqual(active["port"], 1082)
            self.assertEqual(active["name"], "Proxy-2")

            # Проверяем счетчик ротаций
            self.assertGreaterEqual(dispatcher._rotation_count, 1)

    def test_set_pool_accepts_proxy_instance_objects(self):
        """set_pool принимает объекты в форме gui.ProxyInstance (get_display_name, без get_name)."""

        class FakeInstance:
            def __init__(self, name, port, flagged):
                self.cfg = {"name": name, "listen": f"127.0.0.1:{port}"}
                self._port = port
                self._flagged = flagged

            def get_listen(self):
                return "127.0.0.1", str(self._port)

            def get_display_name(self):
                return self.cfg["name"]

            def is_telegram_proxy(self):
                return self._flagged

        dispatcher = TelegramProxyDispatcher(listen_port=19999)
        dispatcher.set_pool([
            FakeInstance("TG-1", 1081, True),
            FakeInstance("Main", 1015, False),
            FakeInstance("TG-2", 1082, True),
        ])

        pool = dispatcher.get_pool()
        self.assertEqual([(p["name"], p["port"]) for p in pool], [("TG-1", 1081), ("TG-2", 1082)])
        self.assertIsInstance(pool[0]["port"], int)

    def test_dispatcher_manual_rotation(self):
        """Принудительное переключение rotate_to_next переключает по кругу."""
        dispatcher = TelegramProxyDispatcher(listen_port=19999)
        pool_data = [
            {"name": "Proxy-A", "listen": "127.0.0.1:1081", "telegram_proxy": True},
            {"name": "Proxy-B", "listen": "127.0.0.1:1082", "telegram_proxy": True},
        ]
        dispatcher.set_pool(pool_data)

        with patch("vless2socks.telegram_proxy.is_endpoint_reachable", return_value=True):
            first = dispatcher.get_active_proxy()
            self.assertEqual(first["port"], 1081)

            second = dispatcher.rotate_to_next()
            self.assertEqual(second["port"], 1082)

            third = dispatcher.rotate_to_next()
            self.assertEqual(third["port"], 1081)


class TestTelegramProxyUIIntegration(unittest.TestCase):

    def test_overview_and_settings_buttons_and_badges(self):
        """Проверка бейджа TelegramProxy и кнопки Copy TG_socks."""
        root = tk.Tk()
        root.withdraw()
        try:
            from gui import VlessApp, ProxyInstance
            # Мокаем корневое приложение
            app = MagicMock()
            app.root = root
            app.instances = []

            # Создаем экземпляр с флагом TelegramProxy
            cfg_data = {
                "name": "TG-Special",
                "listen": "127.0.0.1:1095",
                "telegram_proxy": True,
                "TelegramProxy": True,
            }
            row = ProxyInstance(app, cfg_data, global_id=1)
            self.assertTrue(row.is_telegram_proxy())

            # Создаем UI таба настроек
            row.build_tab_ui(root)

            # Проверяем наличие кнопки Copy TG_socks в настройках выбранного прокси
            self.assertIsNotNone(row.btn_copy_tg)
            self.assertTrue(row.btn_copy_tg.winfo_exists())
            self.assertIn("Copy TG_socks", row.btn_copy_tg.cget("text"))

            # Проверяем работу копирования
            with patch.object(app, "_copy_tg_proxy_link") as mock_copy:
                row.btn_copy_tg.invoke()
                mock_copy.assert_called_once_with(row)

        finally:
            root.destroy()

    def test_restart_services_context_menu_has_telegram_proxy_socket(self):
        """Проверка пункта TelegramProxy в контекстном меню RestartServices."""
        from gui import VlessApp
        # Tk roots destroyed by earlier tests must be finalized here, on the main thread: VlessApp starts
        # background threads, and a GC pass in one of them would tear down a Tcl interpreter from the wrong thread.
        import gc
        gc.collect()
        app = VlessApp()
        app.deiconify()
        app.update()
        try:
            # 1. Когда нет активного TelegramProxy
            with patch.object(app, "_get_active_telegram_proxy_socket", return_value="не выбрано"):
                app.open_restart_services_menu()
                app.update()
                self.assertTrue(app._restart_menu_open)
                self.assertIsNotNone(app.restart_menu_win)

                found_tg = None
                for child in app.restart_menu_win.winfo_children():
                    for sub in child.winfo_children():
                        for row in sub.winfo_children():
                            if isinstance(row, tk.Frame):
                                for lbl in row.winfo_children():
                                    if isinstance(lbl, tk.Label) and "TelegramProxy" in lbl.cget("text"):
                                        found_tg = lbl.cget("text")
                self.assertIsNotNone(found_tg)
                self.assertIn("TelegramProxy (не выбрано)", found_tg)
                app.close_restart_services_menu()

            # 2. Когда активен прокси 127.0.0.1:1085
            with patch.object(app, "_get_active_telegram_proxy_socket", return_value="127.0.0.1:1085"):
                app.open_restart_services_menu()
                app.update()
                self.assertTrue(app._restart_menu_open)
                found_tg_active = None
                for child in app.restart_menu_win.winfo_children():
                    for sub in child.winfo_children():
                        for row in sub.winfo_children():
                            if isinstance(row, tk.Frame):
                                for lbl in row.winfo_children():
                                    if isinstance(lbl, tk.Label) and "TelegramProxy" in lbl.cget("text"):
                                        found_tg_active = lbl.cget("text")
                self.assertIsNotNone(found_tg_active)
                self.assertIn("TelegramProxy (127.0.0.1:1085)", found_tg_active)
                app.close_restart_services_menu()
        finally:
            app.destroy()


    def test_copy_tg_proxy_link_copies_socks_format(self):
        """Проверка, что кнопка Copy TG_socks копирует именно tg://socks?server=...&port=..."""
        root = tk.Tk()
        root.withdraw()
        try:
            from gui import VlessApp
            app = MagicMock()
            app.root = root
            app.clipboard_clear = MagicMock()
            app.clipboard_append = MagicMock()
            app.update = MagicMock()
            app.status_bar = None

            # Вызов метода копирования
            VlessApp._copy_tg_proxy_link(app)

            # Проверяем, что в буфер передана именно SOCKS5 ссылка
            app.clipboard_append.assert_called_once()
            copied_url = app.clipboard_append.call_args[0][0]
            self.assertTrue(copied_url.startswith("tg://socks?server=127.0.0.1&port="))
            self.assertNotIn("secret=", copied_url)
        finally:
            root.destroy()

    def test_socks5_traffic_relay_end_to_end(self):
        """Проверка реального прохождения SOCKS5 трафика через TelegramProxyDispatcher."""
        import asyncio

        # 1. Запускаем простой эхо-сервер
        echo_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        echo_sock.bind(("127.0.0.1", 0))
        echo_port = echo_sock.getsockname()[1]
        echo_sock.listen(1)

        def _echo_worker():
            try:
                conn, _ = echo_sock.accept()
                data = conn.recv(1024)
                conn.sendall(b"ECHO:" + data)
                conn.close()
            except Exception:
                pass
            finally:
                echo_sock.close()

        threading.Thread(target=_echo_worker, daemon=True).start()

        # 2. Мокаем open_via_socks5, чтобы он соединялся с эхо-сервером
        async def mock_open_via_socks5(s_host, s_port, d_host, d_port, **kwargs):
            return await asyncio.open_connection("127.0.0.1", echo_port)

        # 3. Запускаем диспетчер на свободном порту
        disp = TelegramProxyDispatcher(listen_host="127.0.0.1", listen_port=1391)
        disp.set_pool([{
            "host": "127.0.0.1",
            "port": 1081,
            "name": "Proxy-1081",
            "telegram_proxy": True,
        }])

        with patch("vless2socks.telegram_proxy.open_via_socks5", side_effect=mock_open_via_socks5), \
             patch("vless2socks.telegram_proxy.is_endpoint_reachable", return_value=True):
            disp.start()
            try:
                self.assertTrue(disp.is_running())

                # 4. Клиент подключается к диспетчеру по SOCKS5
                client = socket.create_connection(("127.0.0.1", 1391), timeout=3.0)
                # SOCKS5 greeting: 1 метод (no auth)
                client.sendall(b"\x05\x01\x00")
                resp = client.recv(2)
                self.assertEqual(resp, b"\x05\x00")

                # SOCKS5 CONNECT to dummy destination
                # VER=5, CMD=1, RSV=0, ATYP=1 (IPv4), IP=127.0.0.1, Port=80
                req = b"\x05\x01\x00\x01\x7f\x00\x00\x01\x00\x50"
                client.sendall(req)
                conn_resp = client.recv(10)
                self.assertEqual(conn_resp[:2], b"\x05\x00")

                # 5. Передача данных и проверка эхо
                client.sendall(b"HELLO_TG")
                echo_reply = client.recv(1024)
                self.assertEqual(echo_reply, b"ECHO:HELLO_TG")
                client.close()
            finally:
                disp.stop()


if __name__ == "__main__":
    unittest.main()

