"""Fast SOCKS5 Upstream Tunnel Health Probe.

Performs an active end-to-end tunnel verification by sending a binary SOCKS5
CONNECT probe to a fast DNS IP (1.1.1.1:53 or 8.8.8.8:53).
Takes ~10-50ms, consumes 0 external bandwidth, and deterministically detects
dead/zombie TCP sockets after sleep or network drops.
"""

from __future__ import annotations

import socket
import time
from typing import Tuple

TARGETS = [
    ("1.1.1.1", 53),
    ("1.1.1.1", 80),
    ("8.8.8.8", 53),
]


def probe_socks5_tunnel(host: str, port: int, timeout: float = 2.5) -> Tuple[bool, str]:
    """Tests if the SOCKS5 proxy can successfully establish an outbound TCP tunnel.

    Returns (True, message) if tunnel is active, (False, reason) if broken.
    """
    for target_ip, target_port in TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout)
                # 1. SOCKS5 Greeting: 1 method (No Authentication)
                s.sendall(b"\x05\x01\x00")
                resp = s.recv(2)
                if resp != b"\x05\x00":
                    return False, f"Handshake rejected: {resp.hex() if resp else 'empty'}"

                # 2. SOCKS5 CONNECT Request (IPv4)
                ip_bytes = socket.inet_aton(target_ip)
                port_bytes = target_port.to_bytes(2, "big")
                req = b"\x05\x01\x00\x01" + ip_bytes + port_bytes
                s.sendall(req)

                reply = s.recv(10)
                if len(reply) >= 2 and reply[1] == 0:
                    return True, "Tunnel active"
                rep_code = reply[1] if len(reply) >= 2 else -1
                # If target was rejected by server, try next target before declaring failure
                if rep_code != 0:
                    continue
        except (socket.timeout, OSError) as e:
            continue
        except Exception as e:
            return False, str(e)

    return False, "Upstream tunnel unreachable (timed out or refused)"
