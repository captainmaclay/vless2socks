"""vless2socks.ink — Launcher, Icon & Shortcut automation subsystem."""

from .colors import THEMES, recolor_chrome_image, recolor_monochrome_hue
from .icon_engine import (
    extract_pe_icon_frames,
    generate_app_icon,
    generate_chrome_icon,
    save_multires_ico,
)
from .launchers import build_chrome_bat, build_xshell_bat, configure_xshell_proxy
from .pipeline import generate_proxy_workspace
from .shortcuts import create_or_update_shortcut, ensure_folder_aliases, flush_icon_cache

__all__ = [
    "THEMES",
    "recolor_chrome_image",
    "recolor_monochrome_hue",
    "extract_pe_icon_frames",
    "save_multires_ico",
    "generate_chrome_icon",
    "generate_app_icon",
    "build_chrome_bat",
    "build_xshell_bat",
    "configure_xshell_proxy",
    "create_or_update_shortcut",
    "ensure_folder_aliases",
    "flush_icon_cache",
    "generate_proxy_workspace",
]
