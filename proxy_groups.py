"""Proxy groups: named sections of the Overview page.

Groups live in settings.json under "proxy_groups" as an ordered list of
{"id": ..., "name": ...}. A proxy can be in several groups at once: the "groups"
list of its instances.json entry holds references (group ids), so adding a proxy
to another group never creates a second proxy or takes another port. A proxy with
no known group is "Unsorted". The older single "group" key is still read.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

import settings_manager

SETTINGS_KEY = "proxy_groups"
VIEW_MODE_KEY = "overview_view_mode"
VIEW_LIST = "list"
VIEW_GROUPS = "groups"
VIEW_GROUP_PREFIX = "group:"
TABS_KEY = "overview_group_tabs"
GROUPS_CFG_KEY = "groups"
LEGACY_GROUP_KEY = "group"
GROUPS_PAGE_SIZE = 10
MAX_NAME_LEN = 40

T = TypeVar("T")


class GroupNameError(ValueError):
    """Invalid group name; `code` is "empty" or "taken"."""

    def __init__(self, code: str, name: str = ""):
        super().__init__(code)
        self.code = code
        self.name = name


def load_groups() -> list[dict[str, str]]:
    """Groups in display order, skipping malformed and duplicate entries."""
    raw = settings_manager.get_setting(SETTINGS_KEY, [])
    groups: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("id") or "").strip()
        if not gid or gid in seen:
            continue
        seen.add(gid)
        groups.append({"id": gid, "name": str(item.get("name") or "").strip() or gid})
    return groups


def save_groups(groups: Sequence[dict[str, str]]) -> None:
    settings_manager.set_setting(SETTINGS_KEY, [{"id": g["id"], "name": g["name"]} for g in groups])


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME_LEN]


def _validate_name(groups: Sequence[dict[str, str]], name: str, ignore_id: Optional[str] = None) -> str:
    clean = normalize_name(name)
    if not clean:
        raise GroupNameError("empty")
    for g in groups:
        if g["id"] != ignore_id and g["name"].casefold() == clean.casefold():
            raise GroupNameError("taken", clean)
    return clean


def validate_new_name(name: str) -> str:
    """Check a name for a group that is about to be created; returns the normalized name."""
    return _validate_name(load_groups(), name)


def create_group(name: str) -> dict[str, str]:
    groups = load_groups()
    group = {"id": f"g_{uuid.uuid4().hex[:10]}", "name": _validate_name(groups, name)}
    groups.append(group)
    save_groups(groups)
    return group


def rename_group(group_id: str, name: str) -> dict[str, str]:
    groups = load_groups()
    for g in groups:
        if g["id"] == group_id:
            g["name"] = _validate_name(groups, name, ignore_id=group_id)
            save_groups(groups)
            return g
    raise KeyError(group_id)


def delete_group(group_id: str) -> bool:
    groups = load_groups()
    kept = [g for g in groups if g["id"] != group_id]
    if len(kept) == len(groups):
        return False
    save_groups(kept)
    return True


def get_group(group_id: Optional[str]) -> Optional[dict[str, str]]:
    if not group_id:
        return None
    return next((g for g in load_groups() if g["id"] == group_id), None)


def group_ids_of(cfg: dict[str, Any], known_ids: Optional[Iterable[str]] = None) -> list[str]:
    """Ids of the proxy's groups in membership order (legacy "group" included);
    with `known_ids`, ids of deleted groups are left out. Empty = Unsorted."""
    raw = cfg.get(GROUPS_CFG_KEY)
    ids = [str(g).strip() for g in raw] if isinstance(raw, list) else []
    legacy = str(cfg.get(LEGACY_GROUP_KEY) or "").strip()
    if legacy:
        ids.insert(0, legacy)
    known = set(known_ids) if known_ids is not None else None
    result: list[str] = []
    for gid in ids:
        if gid and gid not in result and (known is None or gid in known):
            result.append(gid)
    return result


def set_group_ids(cfg: dict[str, Any], group_ids: Iterable[str]) -> None:
    """Store memberships in the "groups" list (and drop the legacy single key)."""
    ids: list[str] = []
    for gid in group_ids:
        gid = str(gid or "").strip()
        if gid and gid not in ids:
            ids.append(gid)
    cfg.pop(LEGACY_GROUP_KEY, None)
    if ids:
        cfg[GROUPS_CFG_KEY] = ids
    else:
        cfg.pop(GROUPS_CFG_KEY, None)


def add_group_ref(cfg: dict[str, Any], group_id: str) -> bool:
    """Add the proxy to a group (a reference, not a copy). False if it was already there."""
    ids = group_ids_of(cfg)
    if group_id in ids:
        set_group_ids(cfg, ids)  # still normalizes a legacy entry
        return False
    set_group_ids(cfg, ids + [group_id])
    return True


def remove_group_ref(cfg: dict[str, Any], group_id: str) -> bool:
    ids = group_ids_of(cfg)
    if group_id not in ids:
        return False
    set_group_ids(cfg, [g for g in ids if g != group_id])
    return True


def partition(
    items: Sequence[T],
    groups: Sequence[dict[str, str]],
    groups_of: Callable[[T], Iterable[str]],
) -> list[tuple[Optional[dict[str, str]], list[T]]]:
    """Split items into sections: every group in order (empty ones included), then Unsorted.

    An item in several groups appears in each of their sections. Item order inside
    a section follows `items`. The Unsorted section (group None) holds items with
    no known group and is present only when non-empty or when there are no groups.
    """
    by_id: dict[str, list[T]] = {g["id"]: [] for g in groups}
    unsorted: list[T] = []
    for item in items:
        placed = False
        for gid in groups_of(item):
            if gid in by_id and item not in by_id[gid]:
                by_id[gid].append(item)
                placed = True
        if not placed:
            unsorted.append(item)
    sections: list[tuple[Optional[dict[str, str]], list[T]]] = [(g, by_id[g["id"]]) for g in groups]
    if unsorted or not groups:
        sections.append((None, unsorted))
    return sections


def member_counts(cfgs: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Number of proxies referencing each group id."""
    counts: dict[str, int] = {}
    for cfg in cfgs:
        for gid in group_ids_of(cfg):
            counts[gid] = counts.get(gid, 0) + 1
    return counts


def page_count(total: int, size: int = GROUPS_PAGE_SIZE) -> int:
    return max(1, (total + size - 1) // size)


def page_slice(items: Sequence[T], page: int, size: int = GROUPS_PAGE_SIZE) -> tuple[list[T], int]:
    """Items of `page` (clamped into range) and the clamped page index."""
    page = min(max(0, page), page_count(len(items), size) - 1)
    return list(items[page * size:(page + 1) * size]), page


def group_view_mode(group_id: str) -> str:
    """View mode of a single-group tab on the Overview."""
    return f"{VIEW_GROUP_PREFIX}{group_id}"


def group_id_of_view(mode: Optional[str]) -> Optional[str]:
    """Group id when `mode` is a single-group tab, else None."""
    if isinstance(mode, str) and mode.startswith(VIEW_GROUP_PREFIX):
        return mode[len(VIEW_GROUP_PREFIX):] or None
    return None


def _valid_view_mode(mode: Any) -> bool:
    if mode in (VIEW_LIST, VIEW_GROUPS):
        return True
    gid = group_id_of_view(mode)
    return bool(gid) and gid in get_group_tabs()


def get_view_mode() -> str:
    """Saved view; a tab whose group is gone (or was closed) falls back to the list."""
    mode = settings_manager.get_setting(VIEW_MODE_KEY, VIEW_LIST)
    return mode if _valid_view_mode(mode) else VIEW_LIST


def set_view_mode(mode: str) -> None:
    settings_manager.set_setting(VIEW_MODE_KEY, mode if _valid_view_mode(mode) else VIEW_LIST)


def get_group_tabs() -> list[str]:
    """Ids of groups open as Overview tabs, in tab order; deleted groups are dropped."""
    raw = settings_manager.get_setting(TABS_KEY, [])
    known = {g["id"] for g in load_groups()}
    tabs: list[str] = []
    for gid in raw if isinstance(raw, list) else []:
        gid = str(gid)
        if gid in known and gid not in tabs:
            tabs.append(gid)
    return tabs


def set_group_tabs(group_ids: Iterable[str]) -> list[str]:
    known = {g["id"] for g in load_groups()}
    tabs: list[str] = []
    for gid in group_ids:
        if gid in known and gid not in tabs:
            tabs.append(gid)
    settings_manager.set_setting(TABS_KEY, tabs)
    return tabs


def open_group_tabs(group_ids: Iterable[str]) -> list[str]:
    """Append tabs for groups that are not open yet; returns the full tab list."""
    return set_group_tabs(get_group_tabs() + [g for g in group_ids])


def close_group_tab(group_id: str) -> list[str]:
    return set_group_tabs(g for g in get_group_tabs() if g != group_id)
