"""
Xshell 8 Proxy Configuration Manager
Configures global SOCKS5 / HTTP proxy profiles for NetSarang Xshell 8 and
binds them to the default session template (default.xshf) and existing sessions.
"""

from pathlib import Path
from typing import Optional, Dict
import winreg


class XshellProxyManager:
    """Manages NetSarang proxy profiles in Common/Proxy, Registry, and Sessions."""

    def __init__(self, common_dir: Optional[Path] = None, sessions_dir: Optional[Path] = None):
        user_docs = Path.home() / "Documents" / "NetSarang Computer" / "8"
        self.proxy_dir = (common_dir or (user_docs / "Common")) / "Proxy"
        self.sessions_dir = sessions_dir or (user_docs / "Xshell" / "Sessions")
        self.default_xshf = self.sessions_dir / "default.xshf"

    def set_socks5_proxy(
        self,
        port: int,
        host: str = "127.0.0.1",
        profile_name: Optional[str] = None,
        exclusive: bool = True
    ) -> str:
        """
        Configure Xshell 8 to route traffic through a SOCKS5 proxy on specified host and port.
        Updates INI profile, Windows Registry, and binds it to default.xshf.
        """
        if not profile_name:
            profile_name = f"SOCKS5_{port}"

        self.proxy_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write Profile INI file (UTF-16 LE with BOM)
        ini_content = f"""[SECTION]
TYPE=2
Host={host}
HOST={host}
Port={port}
PORT={port}
USERNAME=
PASSWORD=
JUMPHOST=
"""
        ini_path = self.proxy_dir / f"{profile_name}.ini"
        ini_path.write_text(ini_content, encoding="utf-16")

        # 2. Write Registry mirror for tool compatibility
        try:
            reg_key_path = f"Software\\NetSarang\\Proxy\\{profile_name}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_key_path) as key:
                winreg.SetValueEx(key, "TYPE", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "Host", 0, winreg.REG_SZ, host)
                winreg.SetValueEx(key, "HOST", 0, winreg.REG_SZ, host)
                winreg.SetValueEx(key, "Port", 0, winreg.REG_DWORD, port)
                winreg.SetValueEx(key, "PORT", 0, winreg.REG_DWORD, port)
                winreg.SetValueEx(key, "USERNAME", 0, winreg.REG_SZ, "")
                winreg.SetValueEx(key, "PASSWORD", 0, winreg.REG_SZ, "")
        except Exception as e:
            print(f"[Warning] Failed to set registry proxy key: {e}")

        # 3. Clean up other proxy files if exclusive mode requested
        if exclusive:
            for old_ini in self.proxy_dir.glob("*.ini"):
                if old_ini.name.lower() != f"{profile_name.lower()}.ini":
                    try:
                        old_ini.unlink()
                    except Exception:
                        pass

        # 4. Update default.xshf session template
        if self.default_xshf.exists():
            text = self.default_xshf.read_text(encoding="utf-16")
            import re
            text = re.sub(r'(?m)^Proxy=.*$', f'Proxy={profile_name}', text)
            if f"Proxy={profile_name}" not in text:
                text = text.replace("[CONNECTION:PROXY]\n", f"[CONNECTION:PROXY]\nProxy={profile_name}\n")
                text = text.replace("[CONNECTION:PROXY]\r\n", f"[CONNECTION:PROXY]\r\nProxy={profile_name}\r\n")
            self.default_xshf.write_text(text, encoding="utf-16")

        # 5. Update existing session (.xsh) files
        for session_file in self.sessions_dir.glob("*.xsh"):
            try:
                stext = session_file.read_text(encoding="utf-16")
                import re
                stext = re.sub(r'(?m)^Proxy=.*$', f'Proxy={profile_name}', stext)
                session_file.write_text(stext, encoding="utf-16")
            except Exception as ex:
                print(f"[Warning] Could not update {session_file.name}: {ex}")

        return profile_name

    def get_active_proxy_name(self) -> Optional[str]:
        """Read the currently configured proxy from default.xshf."""
        if not self.default_xshf.exists():
            return None
        text = self.default_xshf.read_text(encoding="utf-16")
        for line in text.splitlines():
            if line.startswith("Proxy="):
                val = line.split("=", 1)[1].strip()
                return val if val else None
        return None
