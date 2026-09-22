"""Транспорт до VLESS-сервера: обычный TCP и TCP поверх TLS."""

from __future__ import annotations

import asyncio
import ssl
from typing import Optional

from .logging_setup import get_logger
from .url import VlessServer

__all__ = ["build_ssl_context", "open_upstream", "TransportError"]

log = get_logger("vless2socks.transport")

_WARNED_FINGERPRINT = False


class TransportError(OSError):
    """Не удалось установить соединение с VLESS-сервером."""


def build_ssl_context(server: VlessServer) -> Optional[ssl.SSLContext]:
    """Сконструировать SSLContext по настройкам сервера. ``None`` для security=none."""
    global _WARNED_FINGERPRINT

    if not server.uses_tls:
        return None

    ctx = ssl.create_default_context()
    ctx.check_hostname = not server.allow_insecure
    ctx.verify_mode = ssl.CERT_NONE if server.allow_insecure else ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    if server.alpn:
        try:
            ctx.set_alpn_protocols(list(server.alpn))
        except (NotImplementedError, ssl.SSLError) as exc:
            log.warning("не удалось выставить ALPN %s: %s", server.alpn, exc)

    if server.fingerprint and not _WARNED_FINGERPRINT:
        _WARNED_FINGERPRINT = True
        log.warning(
            "fp=%s игнорируется: подделка TLS-отпечатка (uTLS) недоступна "
            "в стандартном ssl-модуле Python",
            server.fingerprint,
        )

    if server.allow_insecure:
        log.warning(
            "allowInsecure=true — сертификат сервера НЕ проверяется, "
            "соединение уязвимо к MITM"
        )

    return ctx


async def open_upstream(
    server: VlessServer,
    connect_timeout: float = 10.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Открыть соединение до VLESS-сервера (TCP, при необходимости обёрнутое в TLS)."""
    ctx = build_ssl_context(server)
    server_hostname = (server.sni or server.address) if ctx is not None else None

    try:
        coro = asyncio.open_connection(
            host=server.address,
            port=server.port,
            ssl=ctx,
            server_hostname=server_hostname,
        )
        reader, writer = await asyncio.wait_for(coro, timeout=connect_timeout)
    except asyncio.TimeoutError:
        raise TransportError(
            f"таймаут подключения к {server.address}:{server.port} "
            f"({connect_timeout:.0f} c)"
        ) from None
    except ssl.SSLCertVerificationError as exc:
        raise TransportError(
            f"сертификат {server_hostname} не прошёл проверку: {exc.verify_message or exc}. "
            f"Проверьте sni= или включите allowInsecure только если понимаете риск"
        ) from exc
    except ssl.SSLError as exc:
        raise TransportError(f"ошибка TLS с {server.address}:{server.port}: {exc}") from exc
    except OSError as exc:
        raise TransportError(
            f"не удалось подключиться к {server.address}:{server.port}: {exc}"
        ) from exc

    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            import socket as _socket

            sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except OSError:
            pass

    return reader, writer
