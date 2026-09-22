"""Проверка ветки security=tls на самоподписанном сертификате.

Сертификат генерируется через openssl; если его нет в системе — тест пропускается.
"""

from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
import tempfile
import unittest
import uuid as uuid_mod
from pathlib import Path

from tests.fake_vless_server import FakeVlessServer
from tests.helpers import TinyHttpServer, socks5_connect
from vless2socks.config import AppConfig
from vless2socks.socks5 import REP_SUCCESS, Socks5Server
from vless2socks.transport import TransportError, build_ssl_context
from vless2socks.url import parse_vless_url
from vless2socks.vless import VlessConnection

USER_ID = str(uuid_mod.uuid4())
CERT_HOST = "vless.test"


def make_cert(directory: Path) -> tuple[Path, Path]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", f"/CN={CERT_HOST}",
            "-addext", f"subjectAltName=DNS:{CERT_HOST}",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@unittest.skipUnless(shutil.which("openssl"), "нужен openssl для генерации сертификата")
class TlsTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cert, cls.key = make_cert(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    async def asyncSetUp(self) -> None:
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(self.cert, self.key)

        self.http = await TinyHttpServer().start()
        self.vless = await FakeVlessServer(USER_ID, ssl_context=server_ctx).start()
        self.proxy: Socks5Server | None = None

    async def asyncTearDown(self) -> None:
        if self.proxy:
            self.proxy.close()
        await self.vless.stop()
        await self.http.stop()

    def _config(self, *, allow_insecure: bool, sni: str = CERT_HOST) -> AppConfig:
        insecure = "&allowInsecure=1" if allow_insecure else ""
        server = parse_vless_url(
            f"vless://{USER_ID}@127.0.0.1:{self.vless.port}"
            f"?security=tls&type=tcp&sni={sni}{insecure}"
        )
        return AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)

    async def test_tls_roundtrip_with_allow_insecure(self):
        self.proxy = Socks5Server(self._config(allow_insecure=True))
        await self.proxy.start()

        reader, writer, rep = await socks5_connect(
            "127.0.0.1", self.proxy.port, "127.0.0.1", self.http.port
        )
        self.assertEqual(rep, REP_SUCCESS)
        writer.write(b"GET /tls HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        await writer.drain()
        body = await asyncio.wait_for(reader.read(-1), timeout=10)
        writer.close()

        self.assertIn(b"echo:/tls", body)

    async def test_self_signed_cert_is_rejected_by_default(self):
        config = self._config(allow_insecure=False)
        with self.assertRaises(TransportError) as ctx:
            await VlessConnection.connect_tcp(config.server, "127.0.0.1", 80)
        self.assertIn("сертификат", str(ctx.exception).lower())

    async def test_ssl_context_settings(self):
        secure = build_ssl_context(self._config(allow_insecure=False).server)
        self.assertTrue(secure.check_hostname)
        self.assertEqual(secure.verify_mode, ssl.CERT_REQUIRED)

        insecure = build_ssl_context(self._config(allow_insecure=True).server)
        self.assertFalse(insecure.check_hostname)
        self.assertEqual(insecure.verify_mode, ssl.CERT_NONE)

    async def test_plain_security_has_no_context(self):
        server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:443?type=tcp")
        self.assertIsNone(build_ssl_context(server))


if __name__ == "__main__":
    unittest.main()
