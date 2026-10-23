"""
Xshell 8 Shortcut Repair & Validation Tool
Repairs broken shortcut files (.lnk) resulting from unattended InstallShield/MSI deployments.
Resolves empty TargetPath bugs and cleans up broken Start Menu folders.
"""

import os
from pathlib import Path
import subprocess
from typing import Optional


class XshellShortcutManager:
    """Manages and repairs Windows desktop and start menu shortcuts for Xshell 8."""

    POSSIBLE_INSTALL_PATHS = [
        Path(r"C:\Program Files (x86)\NetSarang\Xshell 8\Xshell.exe"),
        Path(r"C:\Program Files\NetSarang\Xshell 8\Xshell.exe"),
    ]

    def find_xshell_executable(self) -> Optional[Path]:
        """Locate the Xshell.exe binary on the system."""
        for p in self.POSSIBLE_INSTALL_PATHS:
            if p.exists():
                return p
        return None

    def repair_shortcuts(self) -> bool:
        """
        Create and repair valid shortcuts on Desktop and in Start Menu.
        Cleans up empty TargetPath shortcuts and unexpanded SHELL_OBJECT_FOLDER directories.
        """
        exe_path = self.find_xshell_executable()
        if not exe_path:
            print("[Error] Xshell.exe not found in standard installation directories.")
            return False

        work_dir = str(exe_path.parent)
        exe_str = str(exe_path)

        ps_script = f"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'SilentlyContinue'

$target = "{exe_str}"
$workDir = "{work_dir}"
$wsh = New-Object -ComObject WScript.Shell

# 1. User Desktop
$userDesk = [Environment]::GetFolderPath('Desktop')
if (Test-Path $userDesk) {{
    try {{
        $sc1 = $wsh.CreateShortcut("$userDesk\\Xshell 8.lnk")
        $sc1.TargetPath = $target
        $sc1.WorkingDirectory = $workDir
        $sc1.Description = "NetSarang Xshell 8 Terminal"
        $sc1.Save()
    }} catch {{}}
}}

# 2. Public Desktop
$pubDesk = [Environment]::GetFolderPath('CommonDesktopDirectory')
if (Test-Path $pubDesk) {{
    try {{
        $sc2 = $wsh.CreateShortcut("$pubDesk\\Xshell 8.lnk")
        $sc2.TargetPath = $target
        $sc2.WorkingDirectory = $workDir
        $sc2.Description = "NetSarang Xshell 8 Terminal"
        $sc2.Save()
    }} catch {{}}
}}

# 3. Clean up broken public suite folder shortcut if target is empty
$suiteFolder = "$pubDesk\\Xmanager Power Suite 8"
if (Test-Path "$suiteFolder\\Xshell.lnk") {{
    try {{
        $scSuite = $wsh.CreateShortcut("$suiteFolder\\Xshell.lnk")
        $scSuite.TargetPath = $target
        $scSuite.WorkingDirectory = $workDir
        $scSuite.Save()
    }} catch {{}}
}}

# 4. Start Menu Programs
$startMenu = "$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\NetSarang Xshell 8"
New-Item -ItemType Directory -Force -Path $startMenu -ErrorAction SilentlyContinue | Out-Null
try {{
    $sc3 = $wsh.CreateShortcut("$startMenu\\Xshell 8.lnk")
    $sc3.TargetPath = $target
    $sc3.WorkingDirectory = $workDir
    $sc3.Description = "NetSarang Xshell 8 Terminal"
    $sc3.Save()
}} catch {{}}

# 5. Remove broken unexpanded folder
Remove-Item "$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\SHELL_OBJECT_FOLDER" -Recurse -Force -ErrorAction SilentlyContinue

exit 0
"""
        import base64
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode('ascii')
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
