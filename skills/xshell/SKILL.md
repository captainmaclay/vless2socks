---
name: xshell
description: >-
  Automated deployment, troubleshooting, SOCKS5 proxy routing (for vless2socks/Xray),
  and session management with reverse-engineered credential encryption for NetSarang Xshell 8.
---

# NetSarang Xshell 8 Deployment & Integration Skill

## Overview
This skill provides complete operational knowledge, forensic troubleshooting, and automation tools for installing and configuring **NetSarang Xshell 8** on Windows systems, with specific integration for routing SSH/Telnet traffic exclusively through local **vless2socks / Xray SOCKS5** proxy endpoints (e.g., `127.0.0.1:1030` or `127.0.0.1:1015`), as well as creating programmatic `.xsh` session profiles with properly encrypted passwords.

> **Chat Trajectory Reference:**  
> A full XML transcript of real-world troubleshooting, error forensics, and user interaction sequences is maintained at:  
> [`chat_history.xml`](./chat_history.xml).

---

## Critical Pitfalls & Hard Lessons Learned

During unattended or script-assisted deployments of Xshell 8, several fatal pitfalls can occur if standard assumptions about MSI packages are made.

### 1. The Raw MSI vs. Bootstrapper Trap (`setup.inx` Bypass)
* **What Happened:** NetSarang packages `Xshell 8.msi` inside an InstallShield self-extracting executable (`Xshell-latest-p.exe`). Extracting the `.msi` and running `msiexec.exe /i "Xshell 8.msi" /qn` completes with exit code `0` and installs files to `C:\Program Files (x86)\NetSarang\Xshell 8`.
* **The Fatal Consequence:** The installation is defective. InstallShield's InstallScript engine (`setup.inx`) performs crucial post-MSI tasks:
  1. Resolves dynamic properties such as `SHELL_OBJECT_FOLDER`.
  2. Binds shortcut targets properly.
  3. Executes `nslicense.dll` registration hooks (`SetInstallDate`, `NSLICENSE_SetMagicCode`) to write license initialization tokens into `HKLM\SOFTWARE\WOW6432Node\NetSarang` and `HKCU\Software\NetSarang`.
* **Rule:** **Never** run `msiexec.exe /i "Xshell 8.msi"` alone without the InstallScript engine.

### 2. Error Code 40004: License Initialization Failure
* **Visual Symptom:** When the user attempts to launch Xshell, a message box appears:
  > *«Ошибка получения информации используемой для запуска программы. Пожалуйста, попробуйте переустановить Xshell. Code: 40004»*  
  > *(Failed to get information used to start the program. Please try reinstalling Xshell. Code: 40004)*
* **Reverse-Engineering Breakdown (`nslicense.dll`):**
  * Entry function `NSLICENSE_CheckLicense` at RVA `0x3450` calls `NSLICENSE_GetMagicCode` (RVA `0x4250`) and `NSLICENSE_MagicCodeToDate` (RVA `0x4870`).
  * If these calls return `0` (because `Setup.exe` did not create the license tokens in registry/storage), execution jumps to offset `0x2DD4`.
  * Offset `0x2DD4` executes: `mov [ebp-0x974], 40004` and displays the fatal dialog before calling `ExitProcess`.
* **Remedy:** Execute a clean uninstall of any partial MSI installations, wipe residual registry entries (`HKLM\SOFTWARE\WOW6432Node\NetSarang` and `HKCU\Software\NetSarang`), and run the full official installer `Xshell-latest-p.exe` with GUI on the user's desktop.

### 3. Broken Shortcuts (`TargetPath = ""` and `SHELL_OBJECT_FOLDER`)
* **What Happened:** Direct MSI installation generated a desktop shortcut (`C:\Users\Public\Desktop\Xmanager Power Suite 8\Xshell.lnk`) whose `TargetPath` was an empty string `""`, causing double-clicks to fail silently.
* **Remedy:** Ensure shortcuts are explicitly validated using `WScript.Shell` ComObject:
  * Target: `C:\Program Files (x86)\NetSarang\Xshell 8\Xshell.exe`
  * WorkingDirectory: `C:\Program Files (x86)\NetSarang\Xshell 8`
  * Destination: `Desktop\Xshell 8.lnk` and `Start Menu\Programs\NetSarang Xshell 8\Xshell 8.lnk`.

### 4. Background Desktop Station Isolation (`WinSta0\default`)
* **What Happened:** Background agent processes in automated runners often operate on isolated synthetic desktops (e.g. `exebox-GEE4VME2THXD6B5COWSINGG35T`) on `WinSta0`. Running `Start-Process` inside the agent session opens windows on that hidden desktop, making setup wizards completely invisible to the interactive user.
* **Remedy:** When launching interactive installers or GUI tools for the user, use a worker thread that attaches to the active console desktop:
  ```csharp
  IntPtr hDesk = OpenDesktop("default", 0, false, 0x01FF);
  SetThreadDesktop(hDesk);
  Process.Start(new ProcessStartInfo("Xshell-latest-p.exe") { UseShellExecute = true, Verb = "runas" });
  ```

---

## SOCKS5 Proxy Architecture in Xshell 8

Xshell 8 does not use standard Windows WinINet proxy settings; it uses its own multi-tiered proxy configuration model.

### 1. Global Proxy Configuration Files
Global proxy profiles are stored as individual INI files in:
`C:\Users\<Username>\Documents\NetSarang Computer\8\Common\Proxy\<ProfileName>.ini`

**Format (UTF-16 LE with BOM `FF FE`):**
```ini
[SECTION]
TYPE=2
Host=127.0.0.1
HOST=127.0.0.1
Port=1030
PORT=1030
USERNAME=
PASSWORD=
JUMPHOST=
```
* **Protocol Types (`TYPE`):**
  * `0` = SOCKS4
  * `1` = SOCKS4A
  * `2` = **SOCKS5**
  * `3` = HTTP 1.1
  * `4` = SSH_PASSTHROUGH

### 2. Registry Mirror
For backward compatibility and internal tool discovery, mirror the proxy configuration in the Windows Registry:
`HKCU\Software\NetSarang\Proxy\<ProfileName>`
* `TYPE` (DWORD) = `2`
* `Host` (String) = `"127.0.0.1"`
* `Port` (DWORD) = `1030`
* `USERNAME` (String) = `""`
* `PASSWORD` (String) = `""`

### 3. Enforcing Proxy on Default Session Template
To ensure **all** newly created sessions automatically and exclusively use the proxy:
File: `C:\Users\<Username>\Documents\NetSarang Computer\8\Xshell\Sessions\default.xshf`
Update section:
```ini
[CONNECTION:PROXY]
Proxy=SOCKS5_1030
StartUp=0
```

---

## Session File Management & Password Encryption

Xshell stores sessions as `.xsh` files in:
`C:\Users\<Username>\Documents\NetSarang Computer\8\Xshell\Sessions\<SessionName>.xsh`

### File Format:
* Encoded in **UTF-16 LE** with Byte Order Mark (`0xFF, 0xFE`).
* Standard INI format based on `default.xshf`.

### Password Encryption Algorithm (Xshell 7 / 8 Standard Mode)
If no user-defined Master Password is set, NetSarang derives the encryption key from the Windows user's context:

1. **Get Windows Username and SID:**
   * Username: e.g. `Admin` (case-sensitive as returned by `GetUserNameW`).
   * SID: e.g. `S-1-5-21-1216202834-3237055983-2975759330-1000`.
2. **Compute Context String:**
   In version 7.1 and 8, the layout is reversed:
   $$\text{ContextString} = (\text{Username}[::-1] + \text{SID})[::-1] \equiv \text{SID}[::-1] + \text{Username}$$
   *Example:* `0001-0339575792-3895507323-4382026121-12-5-1-SAdmin`
3. **Derive ARC4 Key:**
   $$\text{Key} = \text{SHA256}(\text{ContextString.encode('ascii')})$$
4. **Encrypt Payload:**
   * Compute SHA-256 checksum of plaintext password: $\text{Checksum} = \text{SHA256}(\text{Password})$
   * Encrypt plaintext password with ARC4: $\text{Ciphertext} = \text{ARC4}(\text{Key}, \text{Password})$
   * Combine: $\text{Payload} = \text{Ciphertext} + \text{Checksum}$ (length = length of password + 32 bytes)
5. **Base64 Encode:**
   $$\text{EncryptedPassword} = \text{Base64}(\text{Payload})$$
6. **Write to Session File:**
   ```ini
   [CONNECTION:AUTHENTICATION]
   UserName=mazitov
   Password=FRyumozENrxeiEiY2igEcVHQ5W+Nxiknc2A9DWqrvdYqEe9yHRVC2A==
   AuthMethodList=01
   ```

---

## Automation Package: `setup_helpers/xshell`

The repository includes a production-ready Python package in `setup_helpers/xshell`:

File                                 | Purpose
:----------------------------------- | :------------------------------------------------------------------------------------------
`setup_helpers/xshell/__init__.py`   | Package root exporting `XshellInstaller`, `XshellProxyManager`, and `XshellSessionManager`.
`setup_helpers/xshell/installer.py`  | Downloads official CDN binary, checks VC++ redistributable, handles desktop elevation.
`setup_helpers/xshell/shortcuts.py`  | Resolves shortcut targets, fixes desktop & start menu `.lnk` files.
`setup_helpers/xshell/proxy.py`      | Creates/switches SOCKS5 proxy profiles in `Common\Proxy` and updates `default.xshf`.
`setup_helpers/xshell/session.py`    | Generates `.xsh` files with the reverse SID+Username ARC4 password encryption algorithm.
`setup_helpers/xshell/cli.py`        | CLI interface for quick terminal operations.

### CLI Quick Reference:
```bash
# 1. Download official installer to Desktop
python -m setup_helpers.xshell.cli download

# 2. Fix broken desktop and start menu shortcuts
python -m setup_helpers.xshell.cli fix-shortcuts

# 3. Configure exclusive SOCKS5 proxy on port 1030
python -m setup_helpers.xshell.cli set-proxy --port 1030

# 4. Add a pre-authenticated SSH session
python -m setup_helpers.xshell.cli add-session --name "192.168.1.138" --host "192.168.1.138" --user "mazitov" --password "password" --proxy "SOCKS5_1030"
```
