"""
Xshell 8 Installer Assistant
Automates downloading the authentic NetSarang Xshell 8 installer, verifying signatures,
managing prerequisites, and triggering interactive desktop setup to prevent Code 40004.
"""

from pathlib import Path
import subprocess
import sys
from typing import Optional
import urllib.request


OFFICIAL_CDN_URL = "https://cdn.netsarang.net/v8/Xshell-latest-p"


class XshellInstaller:
    """Manages acquisition, verification, and launch of the official Xshell 8 setup package."""

    def __init__(self, download_dir: Optional[Path] = None):
        if download_dir is None:
            self.download_dir = Path.home() / "Desktop"
        else:
            self.download_dir = Path(download_dir)
        self.installer_path = self.download_dir / "Установщик_Xshell_8.exe"

    def download_installer(self, progress_callback=None) -> Path:
        """Download official Xshell 8 package directly from NetSarang CDN."""
        print(f"[*] Downloading official Xshell 8 installer from {OFFICIAL_CDN_URL}...")
        self.download_dir.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(
            OFFICIAL_CDN_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req) as resp, open(self.installer_path, "wb") as out_f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 64
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out_f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total:
                    progress_callback(downloaded, total)

        print(f"[+] Download complete: {self.installer_path} ({self.installer_path.stat().st_size:,} bytes)")
        return self.installer_path

    def verify_authenticode(self) -> bool:
        """Verify NetSarang Authenticode digital signature via PowerShell."""
        if not self.installer_path.exists():
            return False
        ps_cmd = f"(Get-AuthenticodeSignature '{self.installer_path}').Status -eq 'Valid'"
        res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        return "True" in res.stdout

    def launch_on_user_desktop(self) -> bool:
        """
        Launch the official installer with elevation on the user's interactive desktop (WinSta0\\default).
        Crucial to ensure the user completes the setup wizard so nslicense.dll writes license tokens.
        """
        if not self.installer_path.exists():
            self.download_installer()

        # C# script to attach to default desktop and launch elevated
        ps_script = f"""
$code = @"
using System;
using System.Diagnostics;
using System.Threading;
using System.Runtime.InteropServices;

public class DesktopLauncher {{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetThreadDesktop(IntPtr hDesktop);

    public static int LaunchElevated(string filePath) {{
        int resultPid = -1;
        Thread t = new Thread(() => {{
            IntPtr hDesk = OpenDesktop("default", 0, false, 0x01FF);
            bool ok = SetThreadDesktop(hDesk);
            if (!ok) return;
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = filePath;
            psi.Verb = "runas";
            psi.UseShellExecute = true;
            try {{
                Process p = Process.Start(psi);
                resultPid = p.Id;
            }} catch {{}}
        }});
        t.Start();
        t.Join();
        return resultPid;
    }}
}}
"@
Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
[DesktopLauncher]::LaunchElevated("{str(self.installer_path)}")
"""
        res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
        return res.returncode == 0

    def clean_uninstall_raw_msi(self) -> bool:
        """
        Perform a clean uninstall if a broken direct-MSI installation is present
        to prepare for a fresh, functional InstallShield wizard deployment.
        """
        product_code = "{4E3C6853-2BAC-4D1D-AD22-D820D0426FE0}"
        ps_cmd = f"Start-Process msiexec.exe -ArgumentList '/x {product_code} /qn /norestart' -Verb RunAs -Wait"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd], capture_output=True)

        # Clean stale registry keys
        clean_reg = """
Remove-Item -Path "HKLM:\\SOFTWARE\\WOW6432Node\\NetSarang" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "HKCU:\\Software\\NetSarang" -Recurse -Force -ErrorAction SilentlyContinue
"""
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", clean_reg], capture_output=True)
        return True
