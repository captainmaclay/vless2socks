"""Test package guard: tests can never modify the user's real data.

Two layers:
1. Isolation by default: on import, the app modules' data paths (settings, instances,
   config, .env, backup folder) point into a temp folder for the whole test run.
   Tests that set their own temp paths keep working; restoring "the original" value
   restores the temp one.
2. A backstop audit hook (below).

The app's modules default to the real files next to gui.py (settings, instances, the
backup password in .env, encrypted backups) and to the real launcher folder. A test that
forgets to redirect one of them used to overwrite the user's data. This audit hook turns
any such write, delete or rename into a PermissionError, so the test fails instead.
"""

import os
import sys

_APP_DIR = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROTECTED_NAMES = {"settings.json", "instances.json", "config.json", ".env", ".vless2socks.lock"}
_PROTECTED_DIRS = [os.path.normcase(p) for p in (
    r"C:\MyFiles\Proxy", os.path.join(os.path.expanduser("~"), "Documents", "VlessBackups"))]
_WRITE_EVENTS = {"os.remove", "os.rename", "os.replace", "os.rmdir", "shutil.rmtree", "os.truncate",
                 "shutil.copyfile", "shutil.move"}


def is_protected(path) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError):
        return False
    folder, name = os.path.split(p)
    if folder == _APP_DIR and (name in _PROTECTED_NAMES or name.startswith(".instance_")
                               or name.endswith(".hbak")):
        return True
    return any(p == d or p.startswith(d + os.sep) for d in _PROTECTED_DIRS)


def _guard(event, args):
    if event == "open":
        if not args or isinstance(args[0], int):
            return
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
        if writing and is_protected(args[0]):
            raise PermissionError(f"test tried to write the user's real file: {args[0]}")
    elif event in _WRITE_EVENTS:
        targets = args[:2] if event in ("os.rename", "os.replace", "shutil.copyfile", "shutil.move") else args[:1]
        if event in ("shutil.copyfile", "shutil.move"):
            targets = args[1:2]                       # the source may be a real file; the target may not
        for target in targets:
            if target is not None and not isinstance(target, int) and is_protected(target):
                raise PermissionError(f"test tried to {event} the user's real file: {target}")


if not getattr(sys, "_vless2socks_test_guard", False):
    sys.addaudithook(_guard)
    sys._vless2socks_test_guard = True


def _isolate_data_files() -> None:
    import atexit
    import shutil
    import tempfile
    from pathlib import Path

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    tmp = Path(tempfile.mkdtemp(prefix="v2s_tests_data_"))
    atexit.register(shutil.rmtree, tmp, True)
    (tmp / "backups").mkdir()

    import settings_manager
    settings_manager.SETTINGS_FILE = tmp / "settings.json"
    settings_manager.DEFAULT_BACKUP_DIR = str(tmp / "backups")
    try:
        settings_manager.DEFAULT_SETTINGS["backup_dir"] = str(tmp / "backups")
    except Exception:
        pass
    import backup_manager
    backup_manager.ROOT_DIR = tmp
    backup_manager.ENV_FILE = tmp / ".env"
    backup_manager.SETTINGS_FILE = tmp / "settings.json"
    backup_manager.INSTANCES_FILE = tmp / "instances.json"
    backup_manager.CONFIG_FILE = tmp / "config.json"
    backup_manager.DEFAULT_BACKUP_DIR = tmp / "backups"
    try:
        import gui
    except Exception:
        return
    gui.SETTINGS_FILE = tmp / "settings.json"
    gui.INSTANCES_FILE = tmp / "instances.json"
    gui.CONFIG_FILE = tmp / "config.json"


if not getattr(sys, "_vless2socks_test_isolated", False):
    sys._vless2socks_test_isolated = True
    _isolate_data_files()
