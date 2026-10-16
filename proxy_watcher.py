"""Watcher: the one place that decides which proxies are kept up.

It replaces the separate Zapret2 Watcher and the per-proxy "auto reconnect" switch:

* What is watched is a selection of targets: ``all``, the flags TelegramProxy and
  SystemProxy, and proxy groups. Picking a flag or a group drops ``all``; picking
  ``all`` drops the rest; an empty selection falls back to ``all``.
* SystemProxy and WorkProxy proxies are always watched, whatever the selection: they
  carry the system-wide and the work traffic, so a selection of a few groups must not
  leave them down after a restart.
* At program start only watched proxies are started (when the Watcher is on).
* While the Watcher is on, a watched proxy that is down (and was not stopped by the
  user) is brought back up, with pauses between attempts from the reconnect intervals.
* Status pills: TelegramProxy, SystemProxy and every selected group, as running/total.

This module has no Tk code; the GUI drives it (see VlessApp).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import proxy_groups
import settings_manager

ALL = "all"
FLAG_TELEGRAM = "flag:telegram"
FLAG_SYSTEM = "flag:system"
FLAG_WORK = "flag:work"
FLAGS = (FLAG_TELEGRAM, FLAG_SYSTEM, FLAG_WORK)
GROUP_PREFIX = "group:"

ENABLED_KEY = "watcher_enabled"
TARGETS_KEY = "watcher_targets"

PROXY_SCHEMES = ("vless://", "socks5://", "socks://", "wireguard://", "wg://")


def group_key(group_id: str) -> str:
    return GROUP_PREFIX + str(group_id)


def group_id_of(key: str) -> Optional[str]:
    return key[len(GROUP_PREFIX):] if key.startswith(GROUP_PREFIX) else None


# ── settings ──
def is_enabled() -> bool:
    value = settings_manager.get_setting(ENABLED_KEY, None)
    if value is None:
        # first run after the update: on if the old autostart or auto-reconnect was on,
        # so nothing changes until the user narrows the selection
        return bool(settings_manager.get_setting("autostart_proxies", True)
                    or settings_manager.get_setting("auto_reconnect", True))
    return bool(value)


def set_enabled(enabled: bool) -> None:
    settings_manager.set_setting(ENABLED_KEY, bool(enabled))


def get_targets(known_group_ids: Optional[Iterable[str]] = None) -> list[str]:
    raw = settings_manager.get_setting(TARGETS_KEY, None)
    return normalize(raw if isinstance(raw, list) else [ALL], known_group_ids)


def set_targets(targets: Sequence[str], known_group_ids: Optional[Iterable[str]] = None) -> list[str]:
    clean = normalize(targets, known_group_ids)
    settings_manager.set_setting(TARGETS_KEY, clean)
    return clean


def normalize(targets: Iterable[str], known_group_ids: Optional[Iterable[str]] = None) -> list[str]:
    """Keep known keys in a stable order; ``all`` excludes everything else; empty -> all."""
    known = None if known_group_ids is None else {str(g) for g in known_group_ids}
    out: list[str] = []
    for key in targets or ():
        key = str(key)
        if key == ALL:
            return [ALL]
        if key in FLAGS or (key.startswith(GROUP_PREFIX) and (known is None or group_id_of(key) in known)):
            if key not in out:
                out.append(key)
    return out or [ALL]


def toggle(targets: Sequence[str], key: str, known_group_ids: Optional[Iterable[str]] = None) -> list[str]:
    """Menu click on `key`. ``all`` selects only itself; anything else drops ``all``."""
    if key == ALL:
        return [ALL]
    current = [k for k in normalize(targets, known_group_ids) if k != ALL]
    if key in current:
        current.remove(key)
    else:
        current.append(key)
    return normalize(current, known_group_ids)


# ── membership ──
def has_url(cfg: dict[str, Any]) -> bool:
    return str(cfg.get("url", "")).strip().startswith(PROXY_SCHEMES)


def _is_work(inst: Any) -> bool:
    check = getattr(inst, "is_work_proxy", None)
    return bool(check()) if callable(check) else False


def always_watched(inst: Any) -> bool:
    """SystemProxy and WorkProxy are kept up whatever the selection."""
    return bool(inst.is_system_proxy()) or _is_work(inst)


def is_watched(inst: Any, targets: Sequence[str]) -> bool:
    if ALL in targets or always_watched(inst):
        return True
    if FLAG_TELEGRAM in targets and inst.is_telegram_proxy():
        return True
    groups = {group_key(g) for g in proxy_groups.group_ids_of(inst.cfg)}
    return any(k in groups for k in targets)


def watched(instances: Iterable[Any], targets: Sequence[str]) -> list[Any]:
    return [i for i in instances if is_watched(i, targets)]


def startup_set(instances: Iterable[Any], targets: Sequence[str]) -> list[Any]:
    """Proxies to start when the program starts: watched ones with a usable URL."""
    return [i for i in watched(instances, targets) if has_url(i.cfg) and not i.running]


# ── status pills ──
@dataclass(frozen=True)
class Pill:
    key: str
    label: str
    up: int
    total: int

    @property
    def state(self) -> str:
        if not self.total:
            return "none"
        if self.up == self.total:
            return "ok"
        return "partial" if self.up else "down"


def _count(instances: Sequence[Any], belongs) -> tuple[int, int]:
    members = [i for i in instances if belongs(i) and has_url(i.cfg)]
    return sum(1 for i in members if i.running), len(members)


def pills(instances: Sequence[Any], targets: Sequence[str], groups: Sequence[dict]) -> list[Pill]:
    """TelegramProxy, SystemProxy and WorkProxy always; then ``All`` or every selected group."""
    out = [Pill(FLAG_TELEGRAM, "TelegramProxy", *_count(instances, lambda i: i.is_telegram_proxy())),
           Pill(FLAG_SYSTEM, "SystemProxy", *_count(instances, lambda i: i.is_system_proxy())),
           Pill(FLAG_WORK, "WorkProxy", *_count(instances, _is_work))]
    if ALL in targets:
        out.append(Pill(ALL, "All", *_count(instances, lambda i: True)))
        return out
    names = {str(g["id"]): g.get("name", "") for g in groups}
    for key in targets:
        gid = group_id_of(key)
        if gid is not None and gid in names:
            out.append(Pill(key, names[gid], *_count(
                instances, lambda i, g=gid: g in proxy_groups.group_ids_of(i.cfg))))
    return out


# ── keeping proxies up ──
class Reviver:
    """Decides which watched proxies to bring back up, with per-proxy pauses between
    attempts (the reconnect intervals: 10, 15, 30, 60 ... seconds)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._next_try: dict[int, float] = {}
        self._attempts: dict[int, int] = {}

    @staticmethod
    def _intervals() -> list[int]:
        raw = settings_manager.get_setting("reconnect_intervals", settings_manager.DEFAULT_RECONNECT_INTERVALS)
        return settings_manager.parse_reconnect_intervals(raw) or [10, 15, 30, 60, 120, 180, 30]

    def needs_reviving(self, inst: Any) -> bool:
        return (has_url(inst.cfg) and not inst.running and not inst.is_transitioning
                and not getattr(inst, "_manual_stop", False))

    def due(self, instances: Iterable[Any], targets: Sequence[str], enabled: bool) -> list[Any]:
        """Watched proxies that are down and whose pause is over; schedules the next pause."""
        now = self._clock()
        out = []
        for inst in instances:
            key = id(inst)
            if inst.running:
                self._next_try.pop(key, None)       # up again: the next failure starts from the
                self._attempts.pop(key, None)       # shortest pause
                continue
            if not enabled or not is_watched(inst, targets) or not self.needs_reviving(inst):
                continue
            if key not in self._next_try:
                self._next_try[key] = now + self._intervals()[0]   # first sight of it down: wait one pause
                self._attempts[key] = 0
                continue
            if now < self._next_try[key]:
                continue
            n = self._attempts.get(key, 0)
            intervals = self._intervals()
            self._attempts[key] = n + 1
            self._next_try[key] = now + intervals[min(n + 1, len(intervals) - 1)]
            out.append(inst)
        return out

    def attempts(self, inst: Any) -> int:
        return self._attempts.get(id(inst), 0)

    def forget(self, inst: Any) -> None:
        self._next_try.pop(id(inst), None)
        self._attempts.pop(id(inst), None)
