"""A real VlessApp in an isolated sandbox, for layout tests and screenshots.

Temp ROOT_DIR / settings / instances with synthetic proxies (long names, groups, flags:
the worst cases for the layout); no proxy is started, tray / IPC / power watcher are
stubbed, watchers that would touch the system are disabled.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Stub:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def sample_instances(count: int = 14) -> list[dict]:
    names = ["System Proxy", "WorkProxy", "Russia-0.1", "02-Russia", "FINLAND 3 VLESS TCP REALITY",
             "Netherlands Amsterdam Premium 10Gbps", "AUSTRIA 1 VLESS TCP", "Германия Франкфурт резерв",
             "USA West Coast Los Angeles", "JP Tokyo", "France 1", "UK London", "Poland Warsaw",
             "Singapore Edge Node #2"]
    out = []
    for i in range(count):
        port = 31080 + i
        out.append({
            "name": names[i % len(names)],
            "url": f"vless://11111111-1111-1111-1111-11111111111{i % 10}@127.0.0.1:9?type=tcp&security=none#{names[i % len(names)]}",
            "listen": f"127.0.0.1:{port}",
            "order": i,
            "killswitch": i % 3 == 0,
            "system_proxy": i == 0, "System_Proxy": i == 0,
            "work_proxy": i == 1, "Work_Proxy": i == 1,
            "telegram_proxy": i in (4, 5, 6), "TelegramProxy": i in (4, 5, 6),
            "groups": (["g_all"] if i < 10 else []) + (["g_fin"] if i in (4, 5) else []),
        })
    return out


class Sandbox:
    def __init__(self, language: str = "ru", count: int = 14, settings: dict | None = None):
        import settings_manager
        self.tmp = Path(tempfile.mkdtemp(prefix="v2s_ui_"))
        (self.tmp / "runtime").mkdir()
        cfg = {
            "language": language, "autostart_proxies": False, "start_minimized_tray": False,
            "force_port_takeover": False, "wsl_watchdog_enabled": False, "zapret2_watcher_enabled": False,
            "auto_backup_enabled": False, "telegram_proxy_port_migrated": True,
            "proxy_groups": [{"id": "g_all", "name": "ALL"}, {"id": "g_fin", "name": "Finland"},
                             {"id": "g_world", "name": "World_vless"}],
            "overview_group_tabs": [], "overview_view_mode": "list",
        }
        cfg.update(settings or {})
        (self.tmp / "settings.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        (self.tmp / "config.json").write_text(json.dumps({"url": "", "listen": "127.0.0.1:31079"}), encoding="utf-8")
        (self.tmp / "instances.json").write_text(json.dumps(sample_instances(count), ensure_ascii=False), encoding="utf-8")
        self._saved_settings = settings_manager.SETTINGS_FILE
        settings_manager.SETTINGS_FILE = self.tmp / "settings.json"

        import gui
        import i18n
        self.gui = gui
        self._saved = {n: getattr(gui, n) for n in ("ROOT_DIR", "INSTANCES_FILE", "CONFIG_FILE", "HAS_TRAY",
                                                    "SingleInstanceIpcServer", "PowerBroadcastWatcher")}
        gui.ROOT_DIR = self.tmp
        gui.INSTANCES_FILE = self.tmp / "instances.json"
        gui.CONFIG_FILE = self.tmp / "config.json"
        gui.HAS_TRAY = False
        gui.SingleInstanceIpcServer = _Stub
        gui.PowerBroadcastWatcher = _Stub
        self._lang = i18n.get_current_language()
        i18n.load_language_preference()
        self.app = gui.VlessApp()
        self.app.after_cancel  # noqa: B018 - keep a strong ref in tests

    def resize(self, width: int, height: int):
        self.app.geometry(f"{width}x{height}+10+10")
        self.settle()

    def settle(self, rounds: int = 12):
        import time
        for _ in range(rounds):
            self.app.update()
            time.sleep(0.01)

    def close(self):
        import i18n
        import settings_manager
        try:
            th = self.app.shutdown_all_proxies()
            th.join(5)
        except Exception:
            pass
        try:
            self.app.destroy()
        except Exception:
            pass
        self.app = None
        import gc
        gc.collect()
        for n, v in self._saved.items():
            setattr(self.gui, n, v)
        settings_manager.SETTINGS_FILE = self._saved_settings
        i18n._CURRENT_LANGUAGE = self._lang
        shutil.rmtree(self.tmp, ignore_errors=True)
