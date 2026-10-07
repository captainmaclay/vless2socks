"""Windows shortcut management and Explorer icon cache operations."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union


def create_or_update_shortcut(
    lnk_path: Union[str, Path],
    target_path: Union[str, Path],
    working_dir: Optional[Union[str, Path]] = None,
    icon_path: Optional[Union[str, Path]] = None,
    icon_index: int = 0,
    arguments: Optional[str] = None,
) -> bool:
    """Creates or updates a Windows .lnk shortcut via PowerShell.

    Uses an external UTF-8-sig script file to avoid path encoding corruption
    with Cyrillic characters and variable expansion issues in PowerShell CLI.
    """
    lnk_path = Path(lnk_path).resolve()
    target_path = Path(target_path).resolve()
    lnk_path.parent.mkdir(parents=True, exist_ok=True)

    w_dir = str(Path(working_dir).resolve()) if working_dir else str(target_path.parent)
    i_path = str(Path(icon_path).resolve()) if icon_path else ""

    ps_lines = [
        "$wsh = New-Object -ComObject WScript.Shell",
        f"$sc = $wsh.CreateShortcut('{str(lnk_path)}')",
        f"$sc.TargetPath = '{str(target_path)}'",
        f"$sc.WorkingDirectory = '{w_dir}'",
    ]

    if i_path:
        ps_lines.append(f"$sc.IconLocation = '{i_path},{icon_index}'")
    if arguments:
        ps_lines.append(f"$sc.Arguments = '{arguments}'")

    ps_lines.append("$sc.Save()")

    temp_script = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".ps1", delete=False) as f:
            f.write("\r\n".join(ps_lines))
            temp_script = f.name

        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", temp_script],
            capture_output=True,
            text=True,
        )
        return res.returncode == 0
    except Exception:
        return False
    finally:
        if temp_script and os.path.exists(temp_script):
            try:
                os.remove(temp_script)
            except Exception:
                pass


def ensure_folder_aliases(base_dir: Union[str, Path], target_subfolder: str = "ink") -> None:
    """Ensures junction aliases 'link' and 'lnk' point to 'ink' inside base_dir."""
    base = Path(base_dir).resolve()
    target = base / target_subfolder
    target.mkdir(parents=True, exist_ok=True)

    for alias in ["link", "lnk"]:
        if alias == target_subfolder:
            continue
        alias_path = base / alias
        if not alias_path.exists():
            success = False
            try:
                cmd = f'cmd /c mklink /J "{str(alias_path)}" "{str(target)}"'
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
                success = alias_path.exists()
            except Exception:
                success = False

            if not success and not alias_path.exists():
                try:
                    # Fallback to directory creation if junctions are not permitted on volume
                    alias_path.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass


def flush_icon_cache() -> None:
    """Triggers Windows icon cache flush via ie4uinit.exe."""
    try:
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, check=False)
    except Exception:
        pass
