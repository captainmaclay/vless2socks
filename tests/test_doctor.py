"""Тесты диагностики: каждая ветка должна давать верный статус и подсказку."""

from __future__ import annotations

import asyncio
import socket
import unittest
import uuid as uuid_mod

from tests.fake_vless_server import FakeVlessServer
from tests.helpers import TinyHttpServer
from vless2socks.config import AppConfig
from vless2socks.doctor import (
    Status,
    check_dns,
    check_listen_port,
    check_parameters,
    check_tcp,
    check_tls,
    check_vless_handshake,
    format_report,
    run_diagnostics,
)
from vless2socks.url import parse_vless_url

USER_ID = str(uuid_mod.uuid4())

REALITY_URL = (
    f"vless://{USER_ID}@example.com:443?encryption=none&flow=xtls-rprx-vision"
    "&type=tcp&security=reality&sni=example.com&fp=edge&pbk=AAAA&sid=1234"
)


def config_for(url: str, *, strict: bool = False, **kw) -> AppConfig:
    cfg = AppConfig(
        server=parse_vless_url(url, strict=strict),
        listen_host="127.0.0.1",
        listen_port=0,
    )
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


class ParameterCheckTest(unittest.TestCase):
    def test_reality_and_vision_are_reported(self):
        res = check_parameters(config_for(REALITY_URL))
        self.assertIs(res.status, Status.FAIL)
        self.assertIn("security=reality", res.detail)
        self.assertIn("flow=xtls-rprx-vision", res.detail)
        self.assertIn("REALITY", res.hint)
        self.assertIn("xray-core", res.hint)

    def test_supported_link_passes(self):
        res = check_parameters(
            config_for(f"vless://{USER_ID}@h.example:443?security=tls&type=tcp")
        )
        self.assertIs(res.status, Status.OK)

    def test_fingerprint_gives_warning_not_failure(self):
        res = check_parameters(
            config_for(f"vless://{USER_ID}@h.example:443?security=tls&type=tcp&fp=chrome")
        )
        self.assertIs(res.status, Status.WARN)
        self.assertIn("fp=chrome", res.detail)

    def test_allow_insecure_gives_warning(self):
        res = check_parameters(
            config_for(
                f"vless://{USER_ID}@h.example:443?security=tls&type=tcp&allowInsecure=1"
            )
        )
        self.assertIs(res.status, Status.WARN)
        self.assertIn("MITM", res.detail)

    def test_websocket_is_reported(self):
        res = check_parameters(
            config_for(f"vless://{USER_ID}@h.example:443?security=tls&type=ws&path=/x")
        )
        self.assertIs(res.status, Status.FAIL)
        self.assertIn("type=ws", res.detail)


class ListenPortCheckTest(unittest.TestCase):
    def test_free_port_is_ok(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        cfg = config_for(f"vless://{USER_ID}@h.example:443?type=tcp")
        cfg.listen_port = port
        self.assertIs(check_listen_port(cfg).status, Status.OK)

    def test_busy_port_is_failure(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            cfg = config_for(f"vless://{USER_ID}@h.example:443?type=tcp")
            cfg.listen_port = port
            res = check_listen_port(cfg)
            self.assertIs(res.status, Status.FAIL)
            self.assertIn("-l 127.0.0.1:1081", res.hint)
        finally:
            sock.close()


class NetworkCheckTest(unittest.IsolatedAsyncioTestCase):
    async def test_dns_skipped_for_ip_literal(self):
        res = await check_dns(config_for(f"vless://{USER_ID}@127.0.0.1:443?type=tcp"))
        self.assertIs(res.status, Status.SKIP)

    async def test_dns_failure_for_bogus_name(self):
        res = await check_dns(
            config_for(f"vless://{USER_ID}@no-such-host.invalid:443?type=tcp")
        )
        self.assertIs(res.status, Status.FAIL)
        self.assertIn("DNS", res.hint)

    async def test_tcp_failure_on_closed_port(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        res = await check_tcp(
            config_for(f"vless://{USER_ID}@127.0.0.1:{port}?type=tcp",
                       connect_timeout=3)
        )
        self.assertIs(res.status, Status.FAIL)

    async def test_tls_skipped_when_security_none(self):
        res = await check_tls(config_for(f"vless://{USER_ID}@127.0.0.1:443?type=tcp"))
        self.assertIs(res.status, Status.SKIP)


class FullRunTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = await TinyHttpServer().start()
        self.vless = await FakeVlessServer(USER_ID).start()

    async def asyncTearDown(self):
        await self.vless.stop()
        await self.http.stop()

    async def test_working_setup_passes_every_check(self):
        cfg = config_for(
            f"vless://{USER_ID}@127.0.0.1:{self.vless.port}?type=tcp",
            udp_enabled=False,
        )
        results = await run_diagnostics(
            cfg, probe_host="127.0.0.1", probe_port=self.http.port, probe_path="/doc"
        )
        by_name = {r.name: r for r in results}
        self.assertIs(by_name["Параметры ссылки"].status, Status.OK)
        self.assertIs(by_name["TCP"].status, Status.OK)
        self.assertIs(by_name["TLS"].status, Status.SKIP)
        self.assertIs(by_name["Рукопожатие VLESS"].status, Status.OK)
        self.assertIs(by_name["HTTP через туннель"].status, Status.OK)
        self.assertIs(by_name["UDP через туннель"].status, Status.SKIP)
        self.assertIn("всё в порядке", format_report(results))

    async def test_wrong_uuid_fails_at_handshake_not_earlier(self):
        cfg = config_for(
            f"vless://{uuid_mod.uuid4()}@127.0.0.1:{self.vless.port}?type=tcp",
            udp_enabled=False,
        )
        results = await run_diagnostics(
            cfg, probe_host="127.0.0.1", probe_port=self.http.port, probe_path="/doc"
        )
        by_name = {r.name: r for r in results}
        self.assertIs(by_name["TCP"].status, Status.OK)
        self.assertIs(by_name["Рукопожатие VLESS"].status, Status.FAIL)
        self.assertIn("UUID", by_name["Рукопожатие VLESS"].hint)
        self.assertIs(by_name["HTTP через туннель"].status, Status.SKIP)

        report = format_report(results)
        self.assertIn("Рукопожатие VLESS", report)
        self.assertIn("не работает", report)

    async def test_handshake_check_runs_directly(self):
        cfg = config_for(f"vless://{USER_ID}@127.0.0.1:{self.vless.port}?type=tcp")
        res = await check_vless_handshake(cfg, "127.0.0.1", self.http.port)
        self.assertIs(res.status, Status.OK)

    async def test_handshake_ok_even_if_target_stays_silent(self):
        """Заголовок ответа приходит до данных — молчащая цель не должна мешать."""
        async def silent(reader, writer):
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        silent_server = await asyncio.start_server(silent, "127.0.0.1", 0)
        silent_port = silent_server.sockets[0].getsockname()[1]
        try:
            cfg = config_for(f"vless://{USER_ID}@127.0.0.1:{self.vless.port}?type=tcp")
            res = await check_vless_handshake(cfg, "127.0.0.1", silent_port)
            self.assertIs(res.status, Status.OK)
        finally:
            silent_server.close()
            await silent_server.wait_closed()

    async def test_unsupported_params_still_check_network_layers(self):
        """Ключевое свойство: reality не должен глушить нижние проверки."""
        cfg = config_for(
            f"vless://{USER_ID}@127.0.0.1:{self.vless.port}"
            "?security=none&type=tcp&flow=xtls-rprx-vision",
            backend="python",
        )
        results = await run_diagnostics(
            cfg, probe_host="127.0.0.1", probe_port=self.http.port, probe_path="/doc"
        )
        by_name = {r.name: r for r in results}
        self.assertIs(by_name["Параметры ссылки"].status, Status.FAIL)
        self.assertIs(by_name["TCP"].status, Status.OK)  # сеть проверена
        self.assertIs(by_name["Рукопожатие VLESS"].status, Status.SKIP)


class ReportFormatTest(unittest.TestCase):
    def test_report_contains_marks_and_hints(self):
        res = check_parameters(config_for(REALITY_URL))
        text = format_report([res])
        self.assertIn("[FAIL]", text)
        self.assertIn("Параметры ссылки", text)
        self.assertIn("->", text)
        self.assertIn("ИТОГ", text)


if __name__ == "__main__":
    unittest.main()


class XrayBackendDoctorTest(unittest.IsolatedAsyncioTestCase):
    """Диагностика в режиме xray: движок, конфиг, запуск, трафик."""

    async def asyncSetUp(self):
        import os
        import tempfile
        from pathlib import Path

        from tests.helpers import TinyHttpServer
        from tests.stub_launcher import make_stub_xray

        self._tmp = tempfile.TemporaryDirectory()
        self.stub = make_stub_xray(Path(self._tmp.name) / "bin")
        self._saved = os.environ.get("STUB_XRAY_MODE")
        os.environ["STUB_XRAY_MODE"] = "ok"
        self.http = await TinyHttpServer().start()

    async def asyncTearDown(self):
        import os

        await self.http.stop()
        if self._saved is None:
            os.environ.pop("STUB_XRAY_MODE", None)
        else:
            os.environ["STUB_XRAY_MODE"] = self._saved
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def _reality_config(self, **kw):
        cfg = config_for(REALITY_URL)
        cfg.listen_host = "127.0.0.1"
        cfg.listen_port = _free_port()
        cfg.xray_path = str(self.stub)
        for key, value in kw.items():
            setattr(cfg, key, value)
        return cfg

    async def test_reality_profile_passes_through_xray(self):
        from vless2socks.doctor import run_diagnostics

        cfg = self._reality_config()
        # Ссылка указывает на example.com — подменяем на живой локальный порт,
        # чтобы DNS/TCP/TLS-шаги проверяли что-то настоящее.
        cfg.server.address = "127.0.0.1"
        cfg.server.port = self.http.port
        cfg.server.security = "none"
        cfg.server.sni = ""

        results = await run_diagnostics(
            cfg, probe_host="127.0.0.1", probe_port=self.http.port, probe_path="/x"
        )
        by_name = {r.name: r for r in results}
        self.assertIs(by_name["Движок"].status, Status.OK)
        self.assertIn("xray-core", by_name["Движок"].detail)
        self.assertIs(by_name["Параметры ссылки"].status, Status.OK)
        self.assertIn("обслуживает xray", by_name["Параметры ссылки"].detail)
        self.assertIs(by_name["Конфиг xray"].status, Status.OK)
        self.assertIs(by_name["Запуск xray"].status, Status.OK)
        self.assertIs(by_name["HTTP через туннель"].status, Status.OK)

    async def test_config_check_reports_missing_public_key(self):
        from vless2socks.doctor import check_xray_config

        cfg = self._reality_config()
        cfg.server.public_key = ""
        res = check_xray_config(cfg)
        self.assertIs(res.status, Status.FAIL)
        self.assertIn("pbk", res.detail)

    async def test_startup_failure_surfaces_xray_log(self):
        import os

        from vless2socks.doctor import check_xray_run

        os.environ["STUB_XRAY_MODE"] = "fail"
        cfg = self._reality_config()
        results = await check_xray_run(
            cfg, str(self.stub), ("127.0.0.1", self.http.port, "/x")
        )
        self.assertIs(results[0].status, Status.FAIL)
        self.assertIn("invalid user id", results[0].hint)
        self.assertIs(results[1].status, Status.SKIP)

    async def test_missing_xray_reports_installer(self):
        from unittest import mock

        from vless2socks.doctor import check_backend

        cfg = self._reality_config()
        cfg.xray_path = ""
        with mock.patch("vless2socks.xray.binary.search_paths", return_value=[]), \
             mock.patch("shutil.which", return_value=None):
            res, backend, path = check_backend(cfg)
        self.assertIs(res.status, Status.FAIL)
        self.assertEqual(backend, "")
        self.assertIn("tools/get_xray.py", res.hint)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
