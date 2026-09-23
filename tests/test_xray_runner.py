"""Жизненный цикл процесса xray на подставном бинарнике.

Настоящий xray в песочнице недоступен, поэтому проверяется всё вокруг него:
запись конфига, ожидание готовности порта, сбор лога, внятность сообщения при
падении, таймаут, остановка и сквозной проход трафика через его SOCKS5-вход.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
import uuid as uuid_mod
from pathlib import Path
from unittest import mock

from tests.helpers import TinyHttpServer
from tests.stub_launcher import make_stub_xray
from vless2socks.backend import PYTHON, XRAY, resolve_backend
from vless2socks.config import AppConfig
from vless2socks.selftest import check_through_socks5
from vless2socks.url import ConfigError, parse_vless_url
from vless2socks.xray import XrayNotFound, XrayProcess, XrayStartupError, find_xray
from vless2socks.xray.binary import xray_version
from vless2socks.xray.runner import write_config

UUID = str(uuid_mod.uuid4())
REALITY_URL = (
    f"vless://{UUID}@fi3.example.net:443?encryption=none&flow=xtls-rprx-vision"
    "&type=tcp&security=reality&sni=fi3.example.net&fp=edge&pbk=KEY&sid=abcd"
)
PLAIN_URL = f"vless://{UUID}@h.example:443?security=tls&type=tcp"


def stub_runs() -> tuple[bool, str]:
    """Проверить, что подставной «бинарник» вообще запускается.

    На Windows launcher — .cmd; если конкретная сборка Python откажется его
    исполнять, честнее пропустить тесты с понятной причиной, чем показать
    пользователю пачку красных падений, к его настройке отношения не имеющих.
    """
    import subprocess

    with tempfile.TemporaryDirectory() as d:
        stub = make_stub_xray(d)
        try:
            proc = subprocess.run(
                [str(stub), "version"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"подставной xray не запускается: {exc}"
    if "Xray" not in (proc.stdout + proc.stderr):
        return False, "подставной xray не ответил на 'version'"
    return True, ""


STUB_OK, STUB_REASON = stub_runs()


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def config_for(url: str, port: int | None = None, **kw) -> AppConfig:
    cfg = AppConfig(
        server=parse_vless_url(url, strict=False),
        listen_host="127.0.0.1",
        listen_port=port if port is not None else free_port(),
    )
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


class WriteConfigTest(unittest.TestCase):
    def test_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(config_for(REALITY_URL), d)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["outbounds"][0]["protocol"], "vless")

    @unittest.skipIf(os.name == "nt", "права файлов проверяются на POSIX")
    def test_config_is_not_world_readable(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(config_for(REALITY_URL), d)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o077, 0, f"права {oct(mode)} слишком широкие")

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as d:
            nested = Path(d) / "runtime" / "deep"
            path = write_config(config_for(REALITY_URL), nested)
            self.assertTrue(path.exists())


@unittest.skipUnless(STUB_OK, STUB_REASON)
class FindXrayTest(unittest.TestCase):
    def test_explicit_path_is_used(self):
        with tempfile.TemporaryDirectory() as d:
            stub = make_stub_xray(d)
            self.assertEqual(find_xray(stub), stub.resolve())

    def test_missing_explicit_path_raises(self):
        with self.assertRaises(XrayNotFound):
            find_xray("/definitely/not/here/xray")

    def test_error_mentions_installer(self):
        with mock.patch("vless2socks.xray.binary.search_paths", return_value=[]), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(XrayNotFound) as ctx:
                find_xray()
        self.assertIn("tools/get_xray.py", str(ctx.exception))

    def test_version_is_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            stub = make_stub_xray(d)
            self.assertTrue(xray_version(stub).startswith("25.3.6"))


@unittest.skipUnless(STUB_OK, STUB_REASON)
class BackendChoiceTest(unittest.TestCase):
    def test_supported_profile_uses_python(self):
        choice = resolve_backend(config_for(PLAIN_URL))
        self.assertEqual(choice.name, PYTHON)

    def test_reality_picks_xray_when_available(self):
        with tempfile.TemporaryDirectory() as d:
            stub = make_stub_xray(d)
            cfg = config_for(REALITY_URL, xray_path=str(stub))
            choice = resolve_backend(cfg)
        self.assertEqual(choice.name, XRAY)
        self.assertIn("security=reality", choice.reason)

    def test_reality_without_xray_explains_how_to_install(self):
        with mock.patch("vless2socks.xray.binary.search_paths", return_value=[]), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ConfigError) as ctx:
                resolve_backend(config_for(REALITY_URL))
        self.assertIn("tools/get_xray.py", str(ctx.exception))

    def test_forcing_python_on_reality_is_refused(self):
        cfg = config_for(REALITY_URL, backend="python")
        with self.assertRaises(ConfigError) as ctx:
            resolve_backend(cfg)
        self.assertIn("backend=python", str(ctx.exception))

    def test_forcing_xray_on_plain_profile_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            stub = make_stub_xray(d)
            cfg = config_for(PLAIN_URL, backend="xray", xray_path=str(stub))
            self.assertEqual(resolve_backend(cfg).name, XRAY)

    def test_unknown_backend_name(self):
        with self.assertRaises(ConfigError):
            resolve_backend(config_for(PLAIN_URL, backend="wireguard"))


@unittest.skipUnless(STUB_OK, STUB_REASON)
class ProcessLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.stub = make_stub_xray(self.tmp / "bin")
        self._saved_mode = os.environ.get("STUB_XRAY_MODE")
        self.http = await TinyHttpServer().start()
        self.http_port = self.http.port

    async def asyncTearDown(self):
        await self.http.stop()
        if self._saved_mode is None:
            os.environ.pop("STUB_XRAY_MODE", None)
        else:
            os.environ["STUB_XRAY_MODE"] = self._saved_mode
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def _process(self, mode: str = "ok", **kw) -> tuple[XrayProcess, AppConfig]:
        os.environ["STUB_XRAY_MODE"] = mode
        cfg = config_for(REALITY_URL, **kw)
        return (
            XrayProcess(
                cfg, xray_path=str(self.stub),
                runtime_dir=self.tmp / "runtime", restart=False,
            ),
            cfg,
        )

    async def test_starts_and_opens_port(self):
        process, cfg = self._process("ok")
        await process.start()
        try:
            self.assertIsNotNone(process.pid)
            reader, writer = await asyncio.open_connection(
                cfg.listen_host, cfg.listen_port
            )
            writer.close()
            await writer.wait_closed()
        finally:
            await process.stop()

    async def test_collects_log_output(self):
        process, _ = self._process("ok")
        await process.start()
        try:
            self.assertTrue(
                any("Xray" in line for line in process.log_tail),
                f"в логе нет строки запуска: {process.log_tail}",
            )
        finally:
            await process.stop()

    async def test_crash_reports_process_output(self):
        process, _ = self._process("fail")
        with self.assertRaises(XrayStartupError) as ctx:
            await process.start()
        message = str(ctx.exception)
        self.assertIn("invalid user id", message)
        self.assertIn("с кодом 23", message)
        self.assertIn("Конфиг лежит здесь", message)

    async def test_timeout_when_port_never_opens(self):
        process, _ = self._process("silent")
        with mock.patch("vless2socks.xray.runner.READY_TIMEOUT", 1.5):
            with self.assertRaises(XrayStartupError) as ctx:
                await process.start()
        self.assertIn("не открыл", str(ctx.exception))
        self.assertIsNone(_returncode_or_none(process), "процесс должен быть убит")

    async def test_slow_start_still_succeeds(self):
        process, _ = self._process("slow")
        await process.start()
        try:
            self.assertIsNotNone(process.pid)
        finally:
            await process.stop()

    async def test_stop_is_idempotent(self):
        process, _ = self._process("ok")
        await process.start()
        await process.stop()
        await process.stop()

    async def test_traffic_flows_through_socks_inbound(self):
        """Сквозная проверка: наш код -> SOCKS5 движка -> цель."""
        http = await TinyHttpServer().start()
        process, cfg = self._process("ok")
        await process.start()
        try:
            ok, message = await check_through_socks5(
                cfg, "127.0.0.1", http.port, "/from-xray"
            )
            self.assertTrue(ok, message)
            self.assertIn("200 OK", message)
            self.assertEqual(http.hits, ["/from-xray"])
        finally:
            await process.stop()
            await http.stop()

    async def test_socks_auth_is_enforced_by_generated_config(self):
        http = await TinyHttpServer().start()
        process, cfg = self._process("ok", username="bob", password="hunter2")
        await process.start()
        try:
            ok, message = await check_through_socks5(
                cfg, "127.0.0.1", http.port, "/auth"
            )
            self.assertTrue(ok, message)
        finally:
            await process.stop()
            await http.stop()

    async def test_falls_back_to_legacy_shape_when_xray_rejects_flat(self):
        """Старый бинарник не знает плоской формы — молча пробуем vnext."""
        process, cfg = self._process("reject_flat")
        await process.start()
        try:
            self.assertTrue(process.legacy_vnext, "откат на vnext не сработал")
            written = json.loads(process.config_path.read_text(encoding="utf-8"))
            self.assertIn("vnext", written["outbounds"][0]["settings"])
            ok, message = await check_through_socks5(
                cfg, "127.0.0.1", self.http_port, "/legacy"
            )
            self.assertTrue(ok, message)
        finally:
            await process.stop()

    async def test_modern_shape_is_used_when_xray_rejects_vnext(self):
        """Современный xray (PR #5101 убрал vnext) должен работать сразу."""
        process, cfg = self._process("reject_vnext")
        await process.start()
        try:
            self.assertFalse(process.legacy_vnext, "не должно было откатываться")
            written = json.loads(process.config_path.read_text(encoding="utf-8"))
            self.assertNotIn("vnext", written["outbounds"][0]["settings"])
            ok, message = await check_through_socks5(
                cfg, "127.0.0.1", self.http_port, "/modern"
            )
            self.assertTrue(ok, message)
        finally:
            await process.stop()

    async def test_no_fallback_on_unrelated_failure(self):
        """Ошибка не про конфиг — переписывать формат незачем."""
        process, _ = self._process("fail")
        with self.assertRaises(XrayStartupError):
            await process.start()
        self.assertFalse(process.legacy_vnext)

    async def test_busy_port_is_refused_before_launch(self):
        """Иначе ожидание готовности увидит ЧУЖОЙ сокет и решит, что всё хорошо."""
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        try:
            process, _ = self._process("ok", listen_port=port)
            with self.assertRaises(XrayStartupError) as ctx:
                await process.start()
            self.assertIn("уже занят", str(ctx.exception))
        finally:
            busy.close()

    async def test_ready_timeout_does_not_disable_supervision(self):
        """Неудачный перезапуск не должен глушить надзор навсегда."""
        process, _ = self._process("silent")
        with mock.patch("vless2socks.xray.runner.READY_TIMEOUT", 1.0):
            with self.assertRaises(XrayStartupError):
                await process.start()
        self.assertFalse(
            process._stopping,
            "таймаут готовности выставил _stopping — supervise() замолчит навсегда",
        )

    async def test_restarts_after_unexpected_exit(self):
        process, cfg = self._process("ok")
        process.restart = True
        await process.start()
        first_pid = process.pid
        supervisor = asyncio.ensure_future(process.supervise())
        try:
            if sys.platform == "win32":
                import subprocess
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process._proc.kill()
            with mock.patch("vless2socks.xray.runner.RESTART_BACKOFF", (0.2,)):
                for _ in range(100):
                    await asyncio.sleep(0.1)
                    if process.pid != first_pid and process.restarts:
                        break
            self.assertNotEqual(process.pid, first_pid, "процесс не перезапустился")
            self.assertGreaterEqual(process.restarts, 1)
        finally:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
            await process.stop()

    async def test_missing_binary_fails_before_start(self):
        os.environ["STUB_XRAY_MODE"] = "ok"
        with self.assertRaises(XrayNotFound):
            XrayProcess(
                config_for(REALITY_URL),
                xray_path=str(self.tmp / "nope" / "xray"),
                runtime_dir=self.tmp / "runtime",
            )


def _returncode_or_none(process: XrayProcess):
    proc = getattr(process, "_proc", None)
    return None if proc is None or proc.returncode is not None else proc.returncode


if __name__ == "__main__":
    unittest.main()
