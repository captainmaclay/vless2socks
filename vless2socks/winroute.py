"""Windows default-route table (`route print 0.0.0.0`), read cheaply and shared.

`route.exe` is started directly: no `cmd.exe` in between (``shell=True`` used to
add one per call) and no console window. The output is cached for a short time
and concurrent callers share one run, so dozens of proxies starting together do
not each spawn console processes. A burst of those exhausts the desktop heap,
and Windows then fails new console programs with 0xc0000142.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

CACHE_SECONDS = 30.0
_lock = threading.Lock()
_cache: tuple[float, str] | None = None


def route_print_default(max_age: float = CACHE_SECONDS) -> str:
    """Text of `route print 0.0.0.0` ("" off Windows or on failure)."""
    global _cache
    if sys.platform != "win32":
        return ""
    with _lock:  # one run at a time; others wait and reuse its result
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < max_age:
            return _cache[1]
        try:
            out = subprocess.run(
                ["route", "print", "0.0.0.0"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, errors="replace", timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout or ""
        except Exception:
            out = ""
        _cache = (now, out)
        return out


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None
