"""WSL watchdog: notices a hung WSL and brings it back, without touching a healthy one.

Same idea as the sleep recovery (power_watcher + tunnel_probe): a cheap active probe instead of
"the process exists, so it works", and recovery only after the failure is confirmed.

Rules that keep running Linux apps safe:
  * a stopped WSL is "idle", never a failure - the watchdog does not start a distro on its own;
  * probes are read-only (`wsl --list`, a no-op command inside an already running distro);
  * recovery needs several failed probes in a row, is skipped right after wake-up from sleep,
    and is rate limited;
  * the mildest step goes first (terminate one distro), a full `wsl --shutdown` only if that did not help.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .logging_setup import get_logger

log = get_logger("vless2socks.wsl")

# Held by anything that restarts WSL on purpose (RestartServices), so the watchdog does not
# mistake a deliberate restart for a hang.
WSL_OPERATION_LOCK = threading.Lock()

STATUS_OK = "ok"
STATUS_IDLE = "idle"                # no distro running: nothing to watch, nothing to fix
STATUS_UNAVAILABLE = "unavailable"  # WSL not installed / wsl.exe answers with an error
STATUS_LOW_MEMORY = "low_memory"    # Windows is out of virtual memory: nothing starts, WSL is not the culprit
STATUS_SERVICE_HUNG = "service_hung"
STATUS_DISTRO_HUNG = "distro_hung"
STATUS_GUI_DOWN = "gui_down"        # distro answers, WSLg display socket is gone

FAILURE_STATUSES = (STATUS_SERVICE_HUNG, STATUS_DISTRO_HUNG, STATUS_GUI_DOWN)

_ALIVE_MARK = "V2S_ALIVE"
_GUI_OK_MARK = "V2S_GUI_OK"
_GUI_DOWN_MARK = "V2S_GUI_DOWN"
# One round trip: proves the distro executes commands and, where WSLg is present, that its X11 socket exists
_PROBE_SCRIPT = (
    f"echo {_ALIVE_MARK}; "
    f"if [ -d /mnt/wslg ]; then if [ -S /tmp/.X11-unix/X0 ]; then echo {_GUI_OK_MARK}; else echo {_GUI_DOWN_MARK}; fi; fi"
)


@dataclass
class CommandResult:
    returncode: Optional[int]  # None = timed out
    stdout: bytes = b""

    @property
    def timed_out(self) -> bool:
        return self.returncode is None


@dataclass
class WslHealth:
    status: str
    detail: str = ""
    running: list[str] = field(default_factory=list)
    distro: str = ""  # the distro the failure was seen in

    @property
    def failed(self) -> bool:
        return self.status in FAILURE_STATUSES


def run_command(args: list[str], timeout: float) -> CommandResult:
    """Run a console command hidden, never waiting longer than ``timeout`` (a hung wsl.exe is killed)."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return CommandResult(proc.returncode, out or b"")
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass
        return CommandResult(None)


def launch_failed(result: CommandResult) -> bool:
    """wsl.exe died in the Windows loader (NTSTATUS exit code such as 0xC0000142) - it never reached WSL."""
    return result.returncode is not None and (result.returncode & 0xFFFFFFFF) >= 0xC0000000


def free_commit_bytes() -> Optional[int]:
    """Virtual memory (RAM + page file) Windows can still hand out; None when unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                    "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPageFile)
    except Exception:
        return None


def suppress_child_error_dialogs() -> None:
    """Make a child that cannot start exit with an error code instead of showing a modal "application error" box.

    The error mode is inherited by the processes we spawn; without it a probe launched while Windows is
    out of memory leaves a 0xc0000142 dialog on screen and looks like a hung WSL.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        kernel32.SetErrorMode(kernel32.SetErrorMode(0) | 0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass


def parse_running_distros(list_output: str) -> list[str]:
    """Names of running distros from `wsl --list --verbose` text."""
    running = []
    for line in list_output.replace("\x00", "").splitlines()[1:]:
        parts = line.split()
        if parts and parts[0] == "*":
            parts = parts[1:]
        if len(parts) >= 2 and parts[1] == "Running":
            running.append(parts[0])
    return running


class WslWatchdog:
    POLL_INTERVAL = 30.0
    RECHECK_INTERVAL = 10.0        # between probes while a failure is being confirmed
    FAILURES_TO_RECOVER = 3
    RESUME_GRACE = 45.0            # WSL needs time to thaw after sleep
    EXTERNAL_RESTART_GRACE = 20.0
    RECOVERY_COOLDOWN = 300.0
    MAX_RECOVERIES_PER_HOUR = 3
    LIST_TIMEOUT = 6.0
    EXEC_TIMEOUT = 10.0
    WAKE_TIMEOUT = 40.0
    SHUTDOWN_TIMEOUT = 25.0
    MIN_FREE_COMMIT = 2 * 1024 ** 3  # below this new processes fail to start system-wide

    def __init__(
        self,
        enabled: bool = True,
        auto_recover: bool = True,
        recover_gui: bool = False,
        on_log: Optional[Callable[[str], None]] = None,
        on_alert: Optional[Callable[[str], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
        runner: Callable[[list[str], float], CommandResult] = run_command,
        clock: Callable[[], float] = time.monotonic,
        free_commit: Callable[[], Optional[int]] = free_commit_bytes,
    ) -> None:
        self.enabled = bool(enabled)
        self.auto_recover = bool(auto_recover)
        # A missing WSLg socket can only be cured by a full shutdown, which kills working terminals too
        self.recover_gui = bool(recover_gui)
        self.on_log = on_log
        self.on_alert = on_alert
        self.on_change = on_change
        self._run = runner
        self._clock = clock
        self._free_commit = free_commit

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state_lock = threading.Lock()

        self._failures = 0
        self._grace_until = 0.0
        self._recovery_times: list[float] = []
        self._known_running: list[str] = []
        self._alerted_for: Optional[str] = None
        self._status = STATUS_IDLE
        self._detail = ""
        self._recovering = False

    # ── lifecycle ─────────────────────────────────────────────
    def start(self) -> None:
        if sys.platform != "win32":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        suppress_child_error_dialogs()
        self._thread = threading.Thread(target=self._run_loop, name="WslWatchdogThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._failures = 0
        self._wake_event.set()

    def note_resume(self, grace: Optional[float] = None) -> None:
        """The machine just woke up (or WSL was restarted on purpose): forget failures, give WSL time."""
        self._failures = 0
        self._grace_until = max(self._grace_until, self._clock() + (self.RESUME_GRACE if grace is None else grace))
        self._wake_event.set()

    def get_state(self) -> dict:
        with self._state_lock:
            return {
                "enabled": self.enabled,
                "status": self._status,
                "detail": self._detail,
                "failures": self._failures,
                "recovering": self._recovering,
            }

    # ── probing ───────────────────────────────────────────────
    def probe(self) -> WslHealth:
        try:
            listing = self._run(["wsl.exe", "--list", "--verbose"], self.LIST_TIMEOUT)
        except OSError as exc:
            return WslHealth(STATUS_UNAVAILABLE, f"wsl.exe cannot be started: {exc}")
        if listing.timed_out:
            return WslHealth(STATUS_SERVICE_HUNG, "wsl --list did not answer", running=list(self._known_running))
        if listing.returncode != 0:
            return WslHealth(STATUS_UNAVAILABLE, f"wsl --list exit code {listing.returncode & 0xFFFFFFFF:#x}")

        running = [d for d in parse_running_distros(listing.stdout.decode("utf-16le", errors="replace"))
                   if not d.startswith("docker-desktop")]
        if not running:
            return WslHealth(STATUS_IDLE, "no distro is running")

        gui_down = ""
        for distro in running:
            res = self._run(["wsl.exe", "-d", distro, "-e", "/bin/sh", "-c", _PROBE_SCRIPT], self.EXEC_TIMEOUT)
            out = res.stdout.decode("utf-8", errors="replace")
            if launch_failed(res):
                # Windows could not start wsl.exe at all; restarting WSL would not help and may hurt
                return WslHealth(STATUS_UNAVAILABLE, f"wsl.exe failed to start ({res.returncode & 0xFFFFFFFF:#x})")
            if res.timed_out:
                return WslHealth(STATUS_DISTRO_HUNG, f"{distro} did not run a command in {self.EXEC_TIMEOUT:.0f}s", running, distro)
            if _ALIVE_MARK not in out:
                return WslHealth(STATUS_DISTRO_HUNG, f"{distro} failed to run a command (exit code {res.returncode})", running, distro)
            if _GUI_DOWN_MARK in out and not gui_down:
                gui_down = distro
        if gui_down:
            return WslHealth(STATUS_GUI_DOWN, f"WSLg display socket is missing in {gui_down}", running, gui_down)
        return WslHealth(STATUS_OK, ", ".join(running), running)

    # ── one watchdog step; returns seconds until the next one ──
    def tick(self) -> float:
        if not self.enabled:
            self._set_state(STATUS_IDLE, "watchdog disabled")
            return self.POLL_INTERVAL

        now = self._clock()
        if now < self._grace_until:
            return max(1.0, min(self.RECHECK_INTERVAL, self._grace_until - now))

        free = self._free_commit()
        if free is not None and free < self.MIN_FREE_COMMIT:
            # Not even probing: wsl.exe would fail to start and the failure would look like a hang
            self._failures = 0
            self._set_state(STATUS_LOW_MEMORY, f"{free / 1024 ** 3:.1f} GB of virtual memory left")
            if self._alerted_for != STATUS_LOW_MEMORY:
                self._alerted_for = STATUS_LOW_MEMORY
                self._emit("🛑 [WslWatchdog] Windows is almost out of virtual memory "
                           f"({free / 1024 ** 3:.1f} GB left). WSL and other apps cannot start until some program "
                           "is closed; WSL itself is not restarted.", alert=True)
            return self.POLL_INTERVAL

        if not WSL_OPERATION_LOCK.acquire(blocking=False):
            # RestartServices is restarting WSL right now
            self.note_resume(self.EXTERNAL_RESTART_GRACE)
            return self.RECHECK_INTERVAL
        try:
            health = self.probe()
            if not health.failed:
                if self._failures or self._alerted_for:
                    self._emit(f"✅ [WslWatchdog] WSL answers again ({health.detail}).")
                self._failures = 0
                self._alerted_for = None
                if health.status == STATUS_OK:
                    self._known_running = list(health.running)
                self._set_state(health.status, health.detail)
                return self.POLL_INTERVAL

            self._failures += 1
            self._set_state(health.status, health.detail)
            if self._failures < self.FAILURES_TO_RECOVER:
                self._emit(f"⚠️ [WslWatchdog] {health.detail} ({self._failures}/{self.FAILURES_TO_RECOVER}), rechecking...")
                return self.RECHECK_INTERVAL

            blocked = self._why_not_recover(health, now)
            if blocked:
                if self._alerted_for != health.status:
                    self._alerted_for = health.status
                    self._emit(f"🛑 [WslWatchdog] {health.detail}. {blocked}", alert=True)
                return self.POLL_INTERVAL

            self._recovery_times.append(now)
            self._recover(health)
            self._failures = 0
            return self.RECHECK_INTERVAL
        finally:
            WSL_OPERATION_LOCK.release()

    def _why_not_recover(self, health: WslHealth, now: float) -> str:
        if not self.auto_recover:
            return "Auto-recovery is off, use Restart WSL."
        if health.status == STATUS_GUI_DOWN and not self.recover_gui:
            return "Terminals still work, so WSL is left running; Restart WSL brings GUI apps back."
        self._recovery_times = [t for t in self._recovery_times if now - t < 3600]
        if len(self._recovery_times) >= self.MAX_RECOVERIES_PER_HOUR:
            return f"Already recovered {len(self._recovery_times)} times within an hour, not trying again."
        if self._recovery_times and now - self._recovery_times[-1] < self.RECOVERY_COOLDOWN:
            return "Last recovery was a few minutes ago, waiting before the next attempt."
        return ""

    # ── recovery ──────────────────────────────────────────────
    def _recover(self, health: WslHealth) -> bool:
        with self._state_lock:
            self._recovering = True
        try:
            to_wake = list(health.running)
            steps = []
            if health.status == STATUS_DISTRO_HUNG and health.distro:
                steps.append((f"terminating distro {health.distro}", lambda: self._terminate(health.distro)))
            steps.append(("full WSL shutdown", self._shutdown))

            for title, action in steps:
                self._emit(f"🔄 [WslWatchdog] {health.detail}. Recovery: {title}...", alert=True)
                action()
                for distro in to_wake:
                    self._run(["wsl.exe", "-d", distro, "-e", "/bin/true"], self.WAKE_TIMEOUT)
                after = self.probe()
                if not after.failed:
                    self._set_state(after.status, after.detail)
                    self._emit("✅ [WslWatchdog] WSL recovered. Linux apps that were open have to be started again.", alert=True)
                    return True
                health = after
            self._alerted_for = health.status
            self._emit(f"❌ [WslWatchdog] WSL still does not answer: {health.detail}. "
                       "Run `wsl --shutdown` from an administrator console or reboot.", alert=True)
            return False
        finally:
            with self._state_lock:
                self._recovering = False

    def _terminate(self, distro: str) -> None:
        self._run(["wsl.exe", "--terminate", distro], self.SHUTDOWN_TIMEOUT)

    def _shutdown(self) -> None:
        res = self._run(["wsl.exe", "--shutdown"], self.SHUTDOWN_TIMEOUT)
        if res.timed_out:
            # The service itself is stuck; killing it needs administrator rights and fails harmlessly without them
            kill = self._run(["taskkill.exe", "/F", "/IM", "wslservice.exe"], 10.0)
            if kill.returncode != 0:
                self._emit("⚠️ [WslWatchdog] wsl --shutdown hung and wslservice.exe cannot be killed without administrator rights.")

    # ── plumbing ──────────────────────────────────────────────
    def _set_state(self, status: str, detail: str) -> None:
        with self._state_lock:
            changed = status != self._status
            self._status = status
            self._detail = detail
        if changed and self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _emit(self, message: str, alert: bool = False) -> None:
        log.info(message)
        for handler in (self.on_log, self.on_alert if alert else None):
            if handler:
                try:
                    handler(message)
                except Exception:
                    pass

    def _run_loop(self) -> None:
        delay = 15.0  # let the app finish starting first
        while not self._stop_event.is_set():
            waited_from = time.time()
            self._wake_event.wait(timeout=delay)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            # The wall clock jumped: the machine slept and nobody told us
            if time.time() - waited_from > delay + 20.0:
                self.note_resume()
                self._wake_event.clear()
            try:
                delay = self.tick()
            except Exception as exc:
                log.warning(f"WslWatchdog tick failed: {exc}")
                delay = self.POLL_INTERVAL
