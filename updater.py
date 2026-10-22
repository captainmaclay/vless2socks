"""Updates from GitHub Releases: is there a newer vless2socks.exe, download it, swap it in.

* Check: the latest release of REPO through the GitHub API (no token: 60 requests an hour
  per address, the app asks every 15 minutes). Its tag ("v1.2.3") is compared with the
  running version; a release without a vless2socks.exe asset is ignored.
* Download: the .exe asset, checked against its size and, when the release notes carry
  "SHA-256 exe: <hex>", against that hash.
* Swap: a running .exe cannot be overwritten on Windows, so a small batch file waits for
  this process to end, keeps the old file as vless2socks.exe.old, moves the new one in and
  starts it. Only for the built .exe; from the source tree the release page is opened.

GitHub may be blocked on a direct connection: every request is retried through the HTTP
inbounds of running proxies (like subscriptions). This module has no Tk code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

REPO = "captainmaclay/vless2socks"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
EXE_ASSET = "vless2socks.exe"
CHECK_INTERVAL_S = 15 * 60
# Routes tried per request: direct + at most this many proxies. A blocked GitHub with ~90
# running proxies used to mean ~90 timeouts in a row on one background worker.
MAX_PROXY_ROUTES = 3
USER_AGENT = "vless2socks-updater"


class UpdateError(Exception):
    pass


@dataclass
class ReleaseInfo:
    version: str                 # "1.2.3"
    tag: str                     # "v1.2.3"
    page_url: str
    exe_url: str
    exe_size: int
    sha256: str = ""             # lower-case hex, "" when the notes do not give it
    notes: str = ""


@dataclass
class CheckResult:
    current: str
    latest: Optional[ReleaseInfo] = None
    available: bool = False
    error: str = ""
    route: str = ""              # "direct" or the proxy used
    checked_at: float = field(default_factory=time.time)


def current_version() -> str:
    from vless2socks import __version__
    return __version__


def parse_version(text: str) -> tuple:
    """"v1.2.3" / "1.2.3" / "1.2" -> (1, 2, 3); anything else -> ()."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(text or ""))
    if not m:
        return ()
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


def parse_release(data: dict) -> Optional[ReleaseInfo]:
    """GitHub release JSON -> ReleaseInfo, or None for drafts, pre-releases and releases
    without the .exe asset."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    version = ".".join(str(p) for p in parse_version(tag))
    if not version:
        return None
    asset = next((a for a in data.get("assets") or [] if a.get("name") == EXE_ASSET), None)
    if not asset or not asset.get("browser_download_url"):
        return None
    notes = str(data.get("body") or "")
    m = re.search(r"SHA-?256\s+exe\s*:\s*([0-9a-fA-F]{64})", notes)
    return ReleaseInfo(
        version=version, tag=tag, page_url=str(data.get("html_url") or RELEASES_PAGE),
        exe_url=str(asset["browser_download_url"]), exe_size=int(asset.get("size") or 0),
        sha256=m.group(1).lower() if m else "", notes=notes,
    )


def _open(url: str, proxy: Optional[str], timeout: float, accept: str = "*/*"):
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return opener.open(req, timeout=timeout)


def _routes(proxies: Sequence[str]) -> list[Optional[str]]:
    return [None] + [p for p in proxies if p][:MAX_PROXY_ROUTES]


def fetch_latest(proxies: Sequence[str] = (), timeout: float = 10.0,
                 api_url: str = API_LATEST) -> tuple[Optional[ReleaseInfo], str]:
    """The latest release (None if it has no .exe) and the route that answered.
    Raises UpdateError when no route reaches GitHub."""
    errors = []
    for proxy in _routes(proxies):
        try:
            with _open(api_url, proxy, timeout, accept="application/vnd.github+json") as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return parse_release(data), (proxy or "direct")
        except urllib.error.HTTPError as err:
            if err.code == 404:                      # no release published yet
                return None, (proxy or "direct")
            errors.append(f"{proxy or 'direct'}: HTTP {err.code}")
        except Exception as err:
            errors.append(f"{proxy or 'direct'}: {type(err).__name__}: {err}")
    raise UpdateError("GitHub недоступен: " + "; ".join(errors[-3:]))


def check(proxies: Sequence[str] = (), current: Optional[str] = None,
          api_url: str = API_LATEST, timeout: float = 10.0) -> CheckResult:
    """One check; never raises (the error goes into the result)."""
    cur = current or current_version()
    try:
        latest, route = fetch_latest(proxies, timeout=timeout, api_url=api_url)
    except UpdateError as err:
        return CheckResult(current=cur, error=str(err))
    return CheckResult(current=cur, latest=latest, route=route,
                       available=bool(latest) and is_newer(latest.version, cur))


def download(info: ReleaseInfo, dest: str, proxies: Sequence[str] = (),
             progress: Optional[Callable[[int, int], None]] = None, timeout: float = 30.0) -> str:
    """Download the release .exe to `dest` (via a temp file); verify size and SHA-256."""
    errors = []
    for proxy in _routes(proxies):
        tmp = dest + ".part"
        try:
            sha = hashlib.sha256()
            done = 0
            with _open(info.exe_url, proxy, timeout) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or info.exe_size or 0)
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if info.exe_size and done != info.exe_size:
                raise UpdateError(f"размер {done} Б вместо {info.exe_size} Б")
            if info.sha256 and sha.hexdigest() != info.sha256:
                raise UpdateError("контрольная сумма SHA-256 не совпала")
            os.replace(tmp, dest)
            return dest
        except UpdateError:
            _remove(tmp)
            raise
        except Exception as err:
            _remove(tmp)
            errors.append(f"{proxy or 'direct'}: {type(err).__name__}: {err}")
    raise UpdateError("не удалось скачать: " + "; ".join(errors[-3:]))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def swap_script(new_exe: str, target_exe: str, pid: int) -> str:
    """Batch text: wait for `pid` to end, keep the old exe as .old, move the new one in,
    start it, delete itself. If the move fails the old exe is put back and started."""
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        f'tasklist /FI "PID eq {pid}" /NH 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'if exist "{target_exe}.old" del /f /q "{target_exe}.old"',
        f'move /y "{target_exe}" "{target_exe}.old" >nul',
        f'move /y "{new_exe}" "{target_exe}" >nul || move /y "{target_exe}.old" "{target_exe}" >nul',
        f'start "" "{target_exe}"',
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def launch_swap(new_exe: str, target_exe: str, pid: Optional[int] = None) -> str:
    """Write the swap script next to the downloaded exe and start it detached. The caller
    must exit right after. Returns the script path."""
    pid = os.getpid() if pid is None else int(pid)
    script = os.path.join(os.path.dirname(new_exe), "vless2socks-update.bat")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(swap_script(new_exe, target_exe, pid))
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True,
                     cwd=os.path.dirname(target_exe) or None)
    return script


def download_dir() -> str:
    """runtime/update next to the app (falls back to the temp folder)."""
    try:
        from vless2socks.paths import APP_DIR
        path = os.path.join(str(APP_DIR), "runtime", "update")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        return tempfile.mkdtemp(prefix="vless2socks-update-")
