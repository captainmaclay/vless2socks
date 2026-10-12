"""Windows Power Broadcast Watcher (Sleep / Resume Detection).

Detects system suspend (sleep/hibernate) and resume (wake up) events
via Windows WM_POWERBROADCAST to automatically heal dead sockets,
reconnect VLESS/SOCKS5 proxies, and recover the system tray icon.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional

from .logging_setup import get_logger

log = get_logger("vless2socks.power")

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007


class PowerBroadcastWatcher:
    """Listens for Windows power management events (Sleep / Wakeup)."""

    def __init__(
        self,
        on_resume: Optional[Callable[[], None]] = None,
        on_suspend: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_resume = on_resume
        self.on_suspend = on_suspend
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._hwnd = None

    def start(self) -> None:
        if sys.platform != "win32":
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="PowerWatcherThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if sys.platform == "win32" and self._hwnd:
            try:
                import ctypes
                ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass

    def _run_loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = wintypes.LPARAM

            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            class WNDCLASS(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_POWERBROADCAST:
                    if wparam == PBT_APMSUSPEND:
                        log.info("⚡ [PowerWatcher] System is entering sleep / standby...")
                        if self.on_suspend:
                            try:
                                self.on_suspend()
                            except Exception as e:
                                log.warning(f"Error in on_suspend handler: {e}")
                        return 1
                    elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                        log.info(f"⚡ [PowerWatcher] System resumed from sleep (wparam={hex(wparam)})! Triggering recovery...")
                        if self.on_resume:
                            try:
                                self.on_resume()
                            except Exception as e:
                                log.warning(f"Error in on_resume handler: {e}")
                        return 1
                elif msg == 0x0010:  # WM_CLOSE
                    user32.DestroyWindow(hwnd)
                    return 0
                elif msg == 0x0002:  # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            # Register Window Class
            class_name = f"Vless2SocksPowerWatcher_{int(time.time())}"
            wc = WNDCLASS()
            wnd_proc_ref = WNDPROC(wnd_proc)  # Prevent GC
            wc.lpfnWndProc = wnd_proc_ref
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = class_name

            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                log.warning("Failed to register PowerWatcher window class")
                return

            # Top-level popup window (hidden) to receive broadcast messages
            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "Vless2SocksPowerWatcher",
                0x80000000,  # WS_POPUP
                0, 0, 0, 0,
                0,
                0,
                wc.hInstance,
                None,
            )
            self._hwnd = hwnd

            log.info(f"⚡ PowerWatcher active (HWND {hex(hwnd) if hwnd else 'None'})")

            # Win32 Message Loop
            msg = wintypes.MSG()
            while self._running:
                r = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if r <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        except Exception as e:
            log.warning(f"PowerWatcher thread error: {e}")
        finally:
            log.info("PowerWatcher thread terminated")
