"""Подставной «xray» для тестов жизненного цикла процесса.

Настоящий xray в песочнице недоступен, поэтому проверяем всё, что вокруг него:
разбор конфига, ожидание готовности порта, чтение лога, реакцию на падение,
остановку. Режим ``ok`` поднимает честный SOCKS5, который ходит напрямую, —
этого достаточно, чтобы прогнать проверку «HTTP через туннель» целиком.

Использование (как у xray):  stub_xray.py run -c config.json
Режим задаётся переменной окружения STUB_XRAY_MODE: ok | fail | silent | slow.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time

MODE = os.environ.get("STUB_XRAY_MODE", "ok")
VERSION_LINE = "Xray 25.3.6 (Xray, Penetrates Everything.) Custom (go1.24.1 linux/amd64)"


def parse_args(argv: list[str]) -> str | None:
    if argv and argv[0] == "version":
        print(VERSION_LINE)
        raise SystemExit(0)
    for i, arg in enumerate(argv):
        if arg in ("-c", "-config", "--config") and i + 1 < len(argv):
            return argv[i + 1]
    return None


class Socks5Handler(socketserver.BaseRequestHandler):
    """Минимальный прямой SOCKS5 CONNECT — без всякого VLESS."""

    def handle(self) -> None:
        sock = self.request
        try:
            head = _recv_exact(sock, 2)
            if not head or head[0] != 5:
                return
            methods = _recv_exact(sock, head[1])
            if methods is None:
                return
            sock.sendall(b"\x05\x00")

            request = _recv_exact(sock, 4)
            if not request or request[1] != 1:
                sock.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
                return

            atyp = request[3]
            if atyp == 1:
                raw = _recv_exact(sock, 4)
                host = str(ipaddress.IPv4Address(raw))
            elif atyp == 3:
                length = _recv_exact(sock, 1)[0]
                host = _recv_exact(sock, length).decode()
            elif atyp == 4:
                raw = _recv_exact(sock, 16)
                host = str(ipaddress.IPv6Address(raw))
            else:
                sock.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
                return
            (port,) = struct.unpack("!H", _recv_exact(sock, 2))

            try:
                upstream = socket.create_connection((host, port), timeout=10)
            except OSError:
                sock.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                return

            sock.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            _pipe(sock, upstream)
        except (OSError, TypeError, struct.error):
            return


def _recv_exact(sock: socket.socket, count: int) -> bytes | None:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _pipe(a: socket.socket, b: socket.socket) -> None:
    def copy(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t = threading.Thread(target=copy, args=(a, b), daemon=True)
    t.start()
    copy(b, a)
    t.join(timeout=5)


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    config_path = parse_args(sys.argv[1:])

    if MODE == "fail":
        print(VERSION_LINE, flush=True)
        print("Failed to start: infra/conf: invalid user id", flush=True)
        return 23

    if MODE in ("reject_vnext", "reject_flat") and not config_path:
        print("Failed to read config", flush=True)
        return 23

    if MODE == "silent":
        # Процесс жив, но порт не открывает — проверка таймаута готовности.
        time.sleep(300)
        return 0

    if not config_path or not os.path.exists(config_path):
        print(f"Failed to read config: {config_path}", flush=True)
        return 23

    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)

    # Режимы, изображающие разные поколения xray: одно понимает только
    # современную плоскую форму outbound, другое только старую (vnext).
    settings = config["outbounds"][0].get("settings", {})
    if MODE == "reject_vnext" and "vnext" in settings:
        print(VERSION_LINE, flush=True)
        print(
            "Failed to start: main: failed to load config files: [%s] > "
            "infra/conf: unknown field \"vnext\"" % config_path,
            flush=True,
        )
        return 23
    if MODE == "reject_flat" and "vnext" not in settings:
        print(VERSION_LINE, flush=True)
        print(
            "Failed to start: main: failed to load config files: [%s] > "
            "infra/conf: VLESS settings: address is not allowed here"
            % config_path,
            flush=True,
        )
        return 23

    inbound = config["inbounds"][0]
    host, port = inbound["listen"], int(inbound["port"])

    if MODE == "slow":
        time.sleep(3)

    print(VERSION_LINE, flush=True)
    server = ReusableServer((host, port), Socks5Handler)
    print(f"[Info] transport/internet/tcp: listening TCP on {host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
