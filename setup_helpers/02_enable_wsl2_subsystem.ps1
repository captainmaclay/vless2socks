# Step 2: Enable Windows Subsystem for Linux (WSL2) features and configure mirrored network
# Requires Administrator privileges
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Write-Host "=== [2/8] Enabling WSL2 Subsystem and Virtualization Features ===" -ForegroundColor Cyan

# 1. Enable Windows Features
Write-Host "[+] Enabling Microsoft-Windows-Subsystem-Linux..." -ForegroundColor Yellow
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null

Write-Host "[+] Enabling VirtualMachinePlatform..." -ForegroundColor Yellow
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null

# 2. Configure BCD hypervisor launch type
Write-Host "[+] Configuring BCD hypervisor launch type to Auto..." -ForegroundColor Yellow
bcdedit.exe /set hypervisorlaunchtype Auto | Out-Null

# 3. Create .wslconfig with mirrored networking mode
$userProfile = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)
$wslConfigPath = Join-Path $userProfile ".wslconfig"

# [general] instanceIdleTimeout=-1: never stop the distro on idle. Restarting it inside a running VM breaks
# WSLg shared memory ("[WARN:COPY MODE]", blank GUI windows), see skills/claude_wsl_gui/SKILL.md.
$wslConfigContent = @"
[wsl2]
networkingMode=mirrored
dnsTunneling=true
firewall=true
autoProxy=true

[general]
instanceIdleTimeout=-1
"@

Write-Host "[+] Deploying Mirrored Network config: $wslConfigPath..." -ForegroundColor Yellow
[System.IO.File]::WriteAllText($wslConfigPath, $wslConfigContent, [System.Text.Encoding]::UTF8)

# 4. Check if hypervisor is active or requires reboot
$stat = & wsl.exe --status 2>&1 | Out-String
if ($stat -match "0x80370114" -or $stat -match "error" -or $stat -match "включена виртуализация") {
    Write-Host "[WARNING] Windows Hypervisor driver requires a 1-time PC reboot before WSL2 can start!" -ForegroundColor Red
    Write-Host "          Please reboot your computer, then proceed with Step 3." -ForegroundColor Yellow
} else {
    Write-Host "[SUCCESS] WSL2 subsystem features are enabled and configured!" -ForegroundColor Green
}
