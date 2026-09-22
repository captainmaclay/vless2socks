"""Поднять тестовый VLESS-сервер + HTTP-цель и держать их, пока не убьют.

Печатает в stdout одну строку JSON с портами — используется для ручного
дымового теста связки с реальным CLI (main.py) и curl.
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, ".")

from tests.fake_vless_server import FakeVlessServer  # noqa: E402
from tests.helpers import TinyHttpServer, start_udp_echo  # noqa: E402


async def main() -> None:
    user_id = sys.argv[1]
    http = await TinyHttpServer().start()
    vless = await FakeVlessServer(user_id).start()
    udp_echo, _transport = await start_udp_echo()

    print(
        json.dumps(
            {"vless": vless.port, "http": http.port, "udp": udp_echo.port}
        ),
        flush=True,
    )
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
