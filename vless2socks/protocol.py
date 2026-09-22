"""Протокол VLESS, версия 0.

Формат запроса (клиент -> сервер), сразу после установления транспорта::

    1 байт   версия протокола (0)
    16 байт  UUID пользователя
    1 байт   длина addons (M)
    M байт   addons (protobuf; для flow="" всегда пусто)
    1 байт   команда: 0x01 TCP, 0x02 UDP, 0x03 MUX
    2 байта  порт назначения, big-endian
    1 байт   тип адреса: 0x01 IPv4, 0x02 домен, 0x03 IPv6
    N байт   адрес (4 / 1+len / 16)
    ...      полезная нагрузка

Формат ответа (сервер -> клиент), один раз в начале потока::

    1 байт   версия протокола
    1 байт   длина addons (N)
    N байт   addons
    ...      полезная нагрузка

Для команды UDP поток несёт датаграммы с префиксом длины (2 байта big-endian).
"""

from __future__ import annotations

import ipaddress
import struct
import uuid as uuid_mod
from dataclasses import dataclass

__all__ = [
    "VERSION",
    "CMD_TCP",
    "CMD_UDP",
    "CMD_MUX",
    "ATYP_IPV4",
    "ATYP_DOMAIN",
    "ATYP_IPV6",
    "ProtocolError",
    "build_request_header",
    "encode_address",
    "decode_address",
    "parse_request_header",
    "build_response_header",
    "VlessRequest",
    "pack_udp",
]

VERSION = 0x00

CMD_TCP = 0x01
CMD_UDP = 0x02
CMD_MUX = 0x03

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x02
ATYP_IPV6 = 0x03

MAX_DOMAIN_LEN = 255
MAX_UDP_PAYLOAD = 65535


class ProtocolError(Exception):
    """Нарушение формата VLESS."""


def encode_address(host: str) -> bytes:
    """Закодировать адрес назначения: ``atyp + адрес``."""
    host = host.strip().strip("[]")
    if not host:
        raise ProtocolError("пустой адрес назначения")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            raw = host.encode("ascii")
        except UnicodeEncodeError:
            try:
                raw = host.encode("idna")  # IDN -> punycode
            except UnicodeError:
                raise ProtocolError(f"некорректное доменное имя: {host!r}") from None
        if not 1 <= len(raw) <= MAX_DOMAIN_LEN:
            raise ProtocolError(f"длина домена вне 1..255: {len(raw)}") from None
        return bytes([ATYP_DOMAIN, len(raw)]) + raw

    if ip.version == 4:
        return bytes([ATYP_IPV4]) + ip.packed
    return bytes([ATYP_IPV6]) + ip.packed


def decode_address(data: bytes, offset: int = 0) -> tuple[str, int]:
    """Раскодировать адрес. Возвращает ``(host, новый_offset)``."""
    if offset >= len(data):
        raise ProtocolError("нет данных для типа адреса")
    atyp = data[offset]
    offset += 1

    if atyp == ATYP_IPV4:
        if len(data) < offset + 4:
            raise ProtocolError("обрезанный IPv4-адрес")
        host = str(ipaddress.IPv4Address(data[offset : offset + 4]))
        return host, offset + 4

    if atyp == ATYP_DOMAIN:
        if offset >= len(data):
            raise ProtocolError("нет длины домена")
        length = data[offset]
        offset += 1
        if length == 0:
            raise ProtocolError("нулевая длина домена")
        if len(data) < offset + length:
            raise ProtocolError("обрезанный домен")
        host = data[offset : offset + length].decode("ascii", errors="replace")
        return host, offset + length

    if atyp == ATYP_IPV6:
        if len(data) < offset + 16:
            raise ProtocolError("обрезанный IPv6-адрес")
        host = str(ipaddress.IPv6Address(data[offset : offset + 16]))
        return host, offset + 16

    raise ProtocolError(f"неизвестный тип адреса: 0x{atyp:02x}")


def build_request_header(
    uuid_bytes: bytes,
    command: int,
    host: str,
    port: int,
    addons: bytes = b"",
) -> bytes:
    """Собрать заголовок VLESS-запроса."""
    if len(uuid_bytes) != 16:
        raise ProtocolError(f"UUID должен занимать 16 байт, получено {len(uuid_bytes)}")
    if command not in (CMD_TCP, CMD_UDP, CMD_MUX):
        raise ProtocolError(f"неизвестная команда: {command}")
    if not 1 <= port <= 65535:
        raise ProtocolError(f"порт вне диапазона 1..65535: {port}")
    if len(addons) > 255:
        raise ProtocolError("addons длиннее 255 байт")

    return b"".join(
        (
            bytes([VERSION]),
            uuid_bytes,
            bytes([len(addons)]),
            addons,
            bytes([command]),
            struct.pack("!H", port),
            encode_address(host),
        )
    )


def build_response_header(addons: bytes = b"") -> bytes:
    """Собрать заголовок ответа сервера (нужен тестовому серверу)."""
    if len(addons) > 255:
        raise ProtocolError("addons длиннее 255 байт")
    return bytes([VERSION, len(addons)]) + addons


@dataclass
class VlessRequest:
    """Разобранный заголовок запроса (используется тестовым сервером)."""

    version: int
    user_id: str
    command: int
    host: str
    port: int
    addons: bytes
    header_len: int


def parse_request_header(data: bytes) -> VlessRequest:
    """Разобрать заголовок запроса. Бросает :class:`ProtocolError` при нехватке байт."""
    if len(data) < 1:
        raise ProtocolError("нет байта версии")
    version = data[0]
    if version != VERSION:
        raise ProtocolError(f"неподдерживаемая версия VLESS: {version}")

    if len(data) < 18:
        raise ProtocolError("обрезанный заголовок (UUID/addons)")
    user_id = str(uuid_mod.UUID(bytes=data[1:17]))
    addons_len = data[17]

    offset = 18
    if len(data) < offset + addons_len:
        raise ProtocolError("обрезанные addons")
    addons = data[offset : offset + addons_len]
    offset += addons_len

    if len(data) < offset + 3:
        raise ProtocolError("обрезанный заголовок (команда/порт)")
    command = data[offset]
    offset += 1
    (port,) = struct.unpack("!H", data[offset : offset + 2])
    offset += 2

    host, offset = decode_address(data, offset)

    return VlessRequest(
        version=version,
        user_id=user_id,
        command=command,
        host=host,
        port=port,
        addons=addons,
        header_len=offset,
    )


def pack_udp(payload: bytes) -> bytes:
    """Обернуть UDP-датаграмму в кадр с префиксом длины."""
    if len(payload) > MAX_UDP_PAYLOAD:
        raise ProtocolError(f"UDP-датаграмма больше {MAX_UDP_PAYLOAD} байт")
    return struct.pack("!H", len(payload)) + payload
