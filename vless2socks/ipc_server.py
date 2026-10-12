"""Inter-Process Communication (IPC) for Single-Instance GUI Management.

Ensures only one GUI instance runs, allows newly launched shortcuts/scripts
to bring the existing window to the foreground, and detects/cleans up hung instances.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from typing import Callable, Optional

from .logging_setup import get_logger

log = get_logger("vless2socks.ipc")

DEFAULT_IPC_PORT = 10199


def send_ipc_command(cmd: str, port: int = DEFAULT_IPC_PORT, timeout: float = 1.5) -> Optional[str]:
    """Sends an IPC command to the running instance. Returns response or None."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"{cmd.strip()}\n".encode("utf-8"))
            resp = s.recv(1024)
            return resp.decode("utf-8", errors="replace").strip()
    except (OSError, socket.timeout):
        return None


def request_show_running_instance(port: int = DEFAULT_IPC_PORT) -> bool:
    """Tells the running instance to restore and show its window. Returns True if handled."""
    resp = send_ipc_command("SHOW", port=port, timeout=1.5)
    return resp == "OK"


class SingleInstanceIpcServer:
    """Local TCP server for single instance management and remote GUI actions."""

    def __init__(
        self,
        port: int = DEFAULT_IPC_PORT,
        on_show: Optional[Callable[[], None]] = None,
        on_restart: Optional[Callable[[], None]] = None,
    ) -> None:
        self.port = port
        self.on_show = on_show
        self.on_restart = on_restart
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        """Binds to 127.0.0.1:port and starts the listener thread."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            sock.listen(5)
            self._server_sock = sock
            self._running = True
            self._thread = threading.Thread(target=self._serve, name="IpcServerThread", daemon=True)
            self._thread.start()
            log.info(f"IPC Server listening on 127.0.0.1:{self.port}")
            return True
        except Exception as e:
            log.warning(f"Failed to start IPC Server on port {self.port}: {e}")
            return False

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

    def _serve(self) -> None:
        while self._running and self._server_sock:
            try:
                conn, _ = self._server_sock.accept()
            except Exception:
                break

            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            with conn:
                conn.settimeout(2.0)
                data = conn.recv(1024).decode("utf-8", errors="replace").strip()
                if not data:
                    return

                cmd = data.upper()
                if cmd == "SHOW":
                    log.info("Received IPC command: SHOW")
                    if self.on_show:
                        try:
                            self.on_show()
                        except Exception as e:
                            log.warning(f"Error in on_show: {e}")
                    conn.sendall(b"OK\n")
                elif cmd == "RESTART":
                    log.info("Received IPC command: RESTART")
                    if self.on_restart:
                        try:
                            self.on_restart()
                        except Exception as e:
                            log.warning(f"Error in on_restart: {e}")
                    conn.sendall(b"OK\n")
                elif cmd == "PING":
                    conn.sendall(b"PONG\n")
                else:
                    conn.sendall(b"UNKNOWN\n")
        except Exception:
            pass
