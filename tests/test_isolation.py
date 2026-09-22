"""Главный вопрос: адрес подменяется только в туннеле, остальная система цела.

Здесь это не рассуждение, а измерение. Цель сообщает, с какого адреса к ней
пришли; VLESS-сервер ходит наружу с 127.0.0.2, а сама машина — с 127.0.0.1.
Если через туннель цель видит 127.0.0.2, а напрямую 127.0.0.1 — подмена есть
и она ограничена туннелем.
"""

from __future__ import annotations

import asyncio
import re
import socket
import sys
import unittest
import uuid as uuid_mod
from pathlib import Path

from tests.fake_vless_server import FakeVlessServer
from tests.helpers import socks5_connect
from vless2socks.config import AppConfig
from vless2socks.ipcheck import Service, compare_ip, fetch_ip_direct, fetch_ip_via_socks5
from vless2socks.socks5 import REP_SUCCESS, Socks5Server
from vless2socks.url import parse_vless_url

USER_ID = str(uuid_mod.uuid4())
#: Второй адрес петли играет роль «другого внешнего IP».
ALT_SOURCE = "127.0.0.2"


def alt_source_available() -> bool:
    try:
        sock = socket.socket()
        sock.bind((ALT_SOURCE, 0))
        sock.close()
        return True
    except OSError:
        return False


ALT_OK = alt_source_available()


class WhoamiServer:
    """Отвечает тем адресом, с которого к нему пришли, — как ifconfig.me."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.host = host
        self._server: asyncio.AbstractServer | None = None
        self.seen: list[str] = []

    @property
    def port(self) -> int:
        assert self._server and self._server.sockets
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> "WhoamiServer":
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        source = peer[0] if peer else "?"
        self.seen.append(source)
        try:
            await asyncio.wait_for(reader.readline(), timeout=5)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
            body = source.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


async def http_get_direct(host: str, port: int) -> str:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout=10)
        return raw.partition(b"\r\n\r\n")[2].decode().strip()
    finally:
        writer.close()
        await writer.wait_closed()


async def http_get_via_proxy(proxy_port: int, host: str, port: int) -> str:
    reader, writer, rep = await socks5_connect("127.0.0.1", proxy_port, host, port)
    assert rep == REP_SUCCESS, f"SOCKS5 отказал, код {rep}"
    try:
        writer.write(
            f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout=10)
        return raw.partition(b"\r\n\r\n")[2].decode().strip()
    finally:
        writer.close()


@unittest.skipUnless(
    ALT_OK, f"нужен второй адрес петли {ALT_SOURCE} (есть на Linux, не на Windows)"
)
class AddressSubstitutionTest(unittest.IsolatedAsyncioTestCase):
    """Подмена адреса измеряется, а не предполагается."""

    async def asyncSetUp(self):
        self.whoami = await WhoamiServer().start()
        self.vless = await FakeVlessServer(
            USER_ID, source_address=(ALT_SOURCE, 0)
        ).start()
        server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:{self.vless.port}?type=tcp")
        self.config = AppConfig(
            server=server, listen_host="127.0.0.1", listen_port=0
        )
        self.proxy = Socks5Server(self.config)
        await self.proxy.start()

    async def asyncTearDown(self):
        self.proxy.close()
        await self.vless.stop()
        await self.whoami.stop()

    async def test_tunnel_changes_source_address(self):
        through = await http_get_via_proxy(
            self.proxy.port, "127.0.0.1", self.whoami.port
        )
        self.assertEqual(
            through, ALT_SOURCE,
            "цель должна видеть адрес VLESS-сервера, а не наш",
        )

    async def test_direct_traffic_keeps_its_own_address(self):
        direct = await http_get_direct("127.0.0.1", self.whoami.port)
        self.assertEqual(
            direct, "127.0.0.1",
            "трафик мимо прокси не должен менять адрес",
        )

    async def test_both_at_once_and_they_differ(self):
        """Тот самый вопрос: подменяется в туннеле и не меняется вне его."""
        direct = await http_get_direct("127.0.0.1", self.whoami.port)
        through = await http_get_via_proxy(
            self.proxy.port, "127.0.0.1", self.whoami.port
        )
        self.assertNotEqual(direct, through, "подмены не произошло")
        self.assertEqual(direct, "127.0.0.1")
        self.assertEqual(through, ALT_SOURCE)

    async def test_direct_still_unchanged_after_tunnel_use(self):
        """Использование туннеля не должно ничего менять для остальной системы."""
        before = await http_get_direct("127.0.0.1", self.whoami.port)
        await http_get_via_proxy(self.proxy.port, "127.0.0.1", self.whoami.port)
        after = await http_get_direct("127.0.0.1", self.whoami.port)
        self.assertEqual(before, after, "прямой трафик изменился после туннеля")
        self.assertEqual(after, "127.0.0.1")


@unittest.skipUnless(ALT_OK, f"нужен второй адрес петли {ALT_SOURCE}")
class IpCheckTest(unittest.IsolatedAsyncioTestCase):
    """Режим --ip на подставном сервисе определения адреса."""

    async def asyncSetUp(self):
        self.whoami = await WhoamiServer().start()
        self.vless = await FakeVlessServer(
            USER_ID, source_address=(ALT_SOURCE, 0)
        ).start()
        server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:{self.vless.port}?type=tcp")
        self.config = AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)
        self.proxy = Socks5Server(self.config)
        await self.proxy.start()
        self.services = (Service("127.0.0.1", "/", self.whoami.port),)

    async def asyncTearDown(self):
        self.proxy.close()
        await self.vless.stop()
        await self.whoami.stop()

    async def test_direct_fetch_returns_own_address(self):
        ip, service, error = await fetch_ip_direct(self.services)
        self.assertEqual(error, "")
        self.assertEqual(ip, "127.0.0.1")
        self.assertIn("127.0.0.1", service)

    async def test_tunnel_fetch_returns_substituted_address(self):
        ip, _, error = await fetch_ip_via_socks5(
            "127.0.0.1", self.proxy.port, services=self.services
        )
        self.assertEqual(error, "")
        self.assertEqual(ip, ALT_SOURCE)

    async def test_report_says_substitution_works(self):
        report = await compare_ip(
            "127.0.0.1", self.proxy.port, services=self.services
        )
        self.assertEqual(report.direct, "127.0.0.1")
        self.assertEqual(report.tunnel, ALT_SOURCE)
        self.assertIs(report.substituted, True)
        self.assertTrue(report.ok)

    async def test_report_detects_absence_of_substitution(self):
        """Если сервер выпускает трафик с того же адреса — это надо назвать."""
        plain_vless = await FakeVlessServer(USER_ID).start()  # без подмены
        try:
            server = parse_vless_url(
                f"vless://{USER_ID}@127.0.0.1:{plain_vless.port}?type=tcp"
            )
            config = AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)
            proxy = Socks5Server(config)
            await proxy.start()
            try:
                report = await compare_ip(
                    "127.0.0.1", proxy.port, services=self.services
                )
            finally:
                proxy.close()
        finally:
            await plain_vless.stop()

        self.assertEqual(report.direct, report.tunnel)
        self.assertIs(report.substituted, False)
        self.assertFalse(report.ok)

    async def test_report_when_tunnel_is_down(self):
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()

        report = await compare_ip("127.0.0.1", dead_port, services=self.services)
        self.assertEqual(report.direct, "127.0.0.1")
        self.assertEqual(report.tunnel, "")
        self.assertIn("недоступен", report.tunnel_error)
        self.assertIsNone(report.substituted)


class ListeningSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """Программа не должна открывать ничего, кроме своего порта."""

    async def test_binds_only_the_configured_address(self):
        server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:443?type=tcp")
        config = AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)
        proxy = Socks5Server(config)
        await proxy.start()
        try:
            socks = proxy._server.sockets
            self.assertEqual(len(socks), 1, "слушающих сокетов должно быть ровно один")
            host, port = socks[0].getsockname()[:2]
            self.assertEqual(host, "127.0.0.1")
            self.assertEqual(port, proxy.port)
        finally:
            proxy.close()

    async def test_loopback_bind_is_not_reachable_from_outside(self):
        """127.0.0.1 значит «только эта машина» — снаружи порт не виден."""
        server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:443?type=tcp")
        config = AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)
        proxy = Socks5Server(config)
        await proxy.start()
        try:
            outside = _non_loopback_address()
            if not outside:
                self.skipTest("у машины нет внешнего адреса для проверки")
            with self.assertRaises((ConnectionRefusedError, OSError, asyncio.TimeoutError)):
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(outside, proxy.port), timeout=3
                )
                writer.close()
        finally:
            proxy.close()


class NoSystemChangesTest(unittest.TestCase):
    """Сторож: в коде не должно появиться ничего, что меняет настройки ОС."""

    FORBIDDEN = re.compile(
        r"\b(winreg|netsh|ProxyEnable|ProxyServer|ProxyOverride|"
        r"InternetSetOption|iptables|nftables|ip\s+route|route\s+add|"
        r"resolvconf|/etc/resolv\.conf|/etc/hosts|SystemConfiguration|"
        r"networksetup|pywin32|ctypes\.windll)\b"
    )
    #: Код, который реально может что-то поменять в системе.
    ROOTS = ("vless2socks", "tools")

    def test_no_system_wide_configuration_is_touched(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for package in self.ROOTS:
            for path in (root / package).rglob("*.py"):
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if line.lstrip().startswith("#"):
                        continue
                    match = self.FORBIDDEN.search(line)
                    if match:
                        offenders.append(f"{path.name}:{number}: {match.group(0)}")
        self.assertEqual(
            offenders, [],
            "появился код, меняющий настройки системы — "
            "прокси обязан оставаться изолированным:\n" + "\n".join(offenders),
        )

    def test_main_does_not_touch_system_either(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "main.py").read_text(encoding="utf-8")
        self.assertIsNone(
            self.FORBIDDEN.search(text),
            "main.py начал менять настройки системы",
        )


def _non_loopback_address() -> str:
    """Найти собственный не-петлевой адрес, если он есть."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 53))
        address = sock.getsockname()[0]
        sock.close()
    except OSError:
        return ""
    return "" if address.startswith("127.") else address


if __name__ == "__main__":
    unittest.main()
