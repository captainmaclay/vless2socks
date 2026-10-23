"""
Command-Line Interface for Xshell 8 Helper Utilities
Usage:
  python -m setup_helpers.xshell.cli <command> [options]
"""

import argparse
import sys
from .installer import XshellInstaller
from .shortcuts import XshellShortcutManager
from .proxy import XshellProxyManager
from .session import XshellSessionManager


def main():
    parser = argparse.ArgumentParser(
        description="Xshell 8 Helper Tool (vless2socks integration)",
        prog="python -m setup_helpers.xshell.cli"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: download
    subparsers.add_parser("download", help="Download official NetSarang installer to Desktop")

    # Command: launch
    subparsers.add_parser("launch", help="Launch installer on active interactive desktop")

    # Command: fix-shortcuts
    subparsers.add_parser("fix-shortcuts", help="Repair broken Desktop and Start Menu shortcuts")

    # Command: set-proxy
    proxy_parser = subparsers.add_parser("set-proxy", help="Configure SOCKS5 proxy profile and bind to default template")
    proxy_parser.add_argument("--port", type=int, default=1030, help="SOCKS5 listening port (default: 1030)")
    proxy_parser.add_argument("--host", type=str, default="127.0.0.1", help="SOCKS5 host IP (default: 127.0.0.1)")
    proxy_parser.add_argument("--profile", type=str, default=None, help="Custom profile name (default: SOCKS5_<port>)")

    # Command: add-session
    session_parser = subparsers.add_parser("add-session", help="Create pre-configured .xsh session file")
    session_parser.add_argument("--name", type=str, required=True, help="Session name (e.g. 192.168.1.138)")
    session_parser.add_argument("--host", type=str, required=True, help="Remote host IP or domain")
    session_parser.add_argument("--user", type=str, required=True, help="SSH user name")
    session_parser.add_argument("--password", type=str, required=True, help="SSH password")
    session_parser.add_argument("--port", type=int, default=22, help="Remote SSH port (default: 22)")
    session_parser.add_argument("--proxy", type=str, default="SOCKS5_1030", help="Proxy profile name (default: SOCKS5_1030)")

    # Command: list-sessions
    subparsers.add_parser("list-sessions", help="List all .xsh session files")

    # Command: clean-msi
    subparsers.add_parser("clean-msi", help="Remove defective raw MSI installation and clean registry")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "download":
        inst = XshellInstaller()
        path = inst.download_installer()
        print(f"[+] Downloaded installer to: {path}")

    elif args.command == "launch":
        inst = XshellInstaller()
        if inst.launch_on_user_desktop():
            print("[+] Installer launched on user desktop.")
        else:
            print("[-] Failed to launch installer.")

    elif args.command == "fix-shortcuts":
        sm = XshellShortcutManager()
        if sm.repair_shortcuts():
            print("[+] Shortcuts repaired successfully on Desktop and Start Menu.")
        else:
            print("[-] Could not repair shortcuts.")

    elif args.command == "set-proxy":
        pm = XshellProxyManager()
        name = pm.set_socks5_proxy(port=args.port, host=args.host, profile_name=args.profile)
        print(f"[+] SOCKS5 proxy '{name}' configured ({args.host}:{args.port}) and bound to default template.")

    elif args.command == "add-session":
        sm = XshellSessionManager()
        path = sm.create_session(
            name=args.name,
            host=args.host,
            user=args.user,
            password=args.password,
            port=args.port,
            proxy=args.proxy
        )
        print(f"[+] Session '{args.name}' created at: {path}")

    elif args.command == "list-sessions":
        sm = XshellSessionManager()
        sessions = sm.list_sessions()
        print(f"Total sessions ({len(sessions)}):")
        for s in sessions:
            print(f" - {s.name}")

    elif args.command == "clean-msi":
        inst = XshellInstaller()
        inst.clean_uninstall_raw_msi()
        print("[+] Uninstalled raw MSI and cleaned stale registry keys.")


if __name__ == "__main__":
    main()
