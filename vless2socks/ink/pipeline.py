"""Full orchestration pipeline for generating proxy launchers, colored icons, and shortcuts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .colors import THEMES
from .icon_engine import (
    DEFAULT_CHROME_PATHS,
    DEFAULT_XSHELL_PATHS,
    extract_pe_icon_frames,
    find_binary,
    generate_app_icon,
    generate_chrome_icon,
)
from .launchers import build_chrome_bat, build_xshell_bat, configure_xshell_proxy
from .shortcuts import create_or_update_shortcut, ensure_folder_aliases, flush_icon_cache

DEFAULT_WORKSPACE_DIR = r"C:\MyFiles\Proxy"

# Default theme assignments based on socket / role
DEFAULT_SOCKET_THEMES: Dict[int, str] = {
    1015: "gold",    # System Proxy (Amber Gold)
    1030: "blue",    # WorProxy (Royal Blue)
    1020: "purple",  # Russia-0.1 (Violet Purple)
    1021: "orange",  # 02-Russia (Vivid Orange)
    1081: "black",   # FI FINLAND 3 (Charcoal Black)
    1082: "green",   # FI FINLAND 4 (Emerald Green)
    1083: "red",     # FR FRANCE 1 (Ruby Red)
    1084: "cyan",    # FI FINLAND 2 (Turquoise Cyan)
    1085: "pink",    # Latvia (Rose Pink)
}


def load_instances_ports(instances_path: Optional[Union[str, Path]] = None) -> List[int]:
    """Loads listen ports from instances.json or defaults."""
    if instances_path and os.path.isfile(instances_path):
        try:
            with open(instances_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ports = []
            for item in data:
                listen = item.get("listen", "")
                if ":" in listen:
                    try:
                        ports.append(int(listen.split(":")[-1]))
                    except ValueError:
                        pass
            if ports:
                return sorted(list(set(ports)))
        except Exception:
            pass

    return [1015, 1020, 1021, 1030, 1081, 1082, 1083, 1084, 1085]


def generate_proxy_workspace(
    target_dir: Union[str, Path] = DEFAULT_WORKSPACE_DIR,
    instances_path: Optional[Union[str, Path]] = None,
    include_xshell: bool = True,
    xshell_port: int = 1030,
    update_desktop: bool = True,
) -> Dict[str, Any]:
    """Orchestrates creation of all launchers, icons, and shortcuts in target_dir."""
    target_dir = Path(target_dir).resolve()
    ico_dir = target_dir / "ico"
    ink_dir = target_dir / "ink"

    target_dir.mkdir(parents=True, exist_ok=True)
    ico_dir.mkdir(parents=True, exist_ok=True)
    ink_dir.mkdir(parents=True, exist_ok=True)
    ensure_folder_aliases(target_dir, "ink")

    ports = load_instances_ports(instances_path)
    created_launchers: List[str] = []
    created_icons: List[str] = []
    created_shortcuts: List[str] = []

    # 1. Chrome Icons and Launchers
    chrome_exe = find_binary(DEFAULT_CHROME_PATHS)
    chrome_frames = None
    if chrome_exe:
        try:
            chrome_frames = extract_pe_icon_frames(chrome_exe)
        except Exception:
            chrome_frames = None

    theme_keys = list(THEMES.keys())

    for idx, port in enumerate(ports):
        theme = DEFAULT_SOCKET_THEMES.get(port, theme_keys[idx % len(theme_keys)])
        ico_name = f"chrome_{theme}.ico"
        ico_path = ico_dir / ico_name

        if chrome_frames:
            from .colors import recolor_chrome_image
            from .icon_engine import save_multires_ico

            recolored = [recolor_chrome_image(f.copy(), theme) for f in chrome_frames]
            save_multires_ico(recolored, ico_path)
            created_icons.append(str(ico_path))

        bat_name = f"Chrome_Socket_{port}.bat"
        bat_path = target_dir / bat_name
        build_chrome_bat(port=port, out_bat_path=bat_path)
        created_launchers.append(str(bat_path))

        lnk_name = f"Chrome Socket {port}.lnk"
        lnk_path = ink_dir / lnk_name
        create_or_update_shortcut(
            lnk_path=lnk_path,
            target_path=bat_path,
            working_dir=target_dir,
            icon_path=ico_path,
        )
        created_shortcuts.append(str(lnk_path))

    # 2. Xshell Integration (if requested and present)
    if include_xshell:
        xshell_exe = find_binary(DEFAULT_XSHELL_PATHS)
        xshell_ico = ico_dir / "xshell_cyan.ico"

        if xshell_exe:
            try:
                generate_app_icon(xshell_exe, xshell_ico, target_hue=0.51, group_id=128)
                created_icons.append(str(xshell_ico))
            except Exception:
                pass

        xshell_bat = target_dir / f"Xshell_Socket_{xshell_port}.bat"
        build_xshell_bat(port=xshell_port, out_bat_path=xshell_bat)
        created_launchers.append(str(xshell_bat))

        xshell_lnk = ink_dir / f"Xshell Socket {xshell_port}.lnk"
        create_or_update_shortcut(
            lnk_path=xshell_lnk,
            target_path=xshell_bat,
            working_dir=target_dir,
            icon_path=xshell_ico if xshell_ico.exists() else None,
        )
        created_shortcuts.append(str(xshell_lnk))
        configure_xshell_proxy(port=xshell_port)

    # 3. Optional Desktop Shortcuts
    if update_desktop:
        desktop = Path(os.path.expanduser(r"~\Desktop"))
        if desktop.exists():
            # Shortcut to the Proxy folder itself
            folder_ico = ico_dir / "chrome_cyan.ico"
            create_or_update_shortcut(
                lnk_path=desktop / "Proxy.lnk",
                target_path=target_dir,
                working_dir=target_dir,
                icon_path=folder_ico if folder_ico.exists() else None,
            )

    flush_icon_cache()

    return {
        "workspace": str(target_dir),
        "launchers": created_launchers,
        "icons": created_icons,
        "shortcuts": created_shortcuts,
        "ports": ports,
    }


if __name__ == "__main__":
    result = generate_proxy_workspace()
    print(f"Generated {len(result['launchers'])} launchers in {result['workspace']}")
