# Master Orchestrator: Complete Zero-Touch Installation and Hardening for vless2socks
# Coordinates Steps 1 to 8 in sequence
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SkipRebootPrompt
)

$ErrorActionPreference = "Stop"
$scriptsDir = $PSScriptRoot

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "  vless2socks Complete Perimeter Hardening & Setup Orchestrator   " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan

# 1. Build vless2socks
if (-not $SkipBuild) {
    & "$scriptsDir\01_build_vless2socks.ps1"
}

# 2. Enable WSL2 and Virtualization
& "$scriptsDir\02_enable_wsl2_subsystem.ps1"

# Check if hypervisor is ready
$wslStat = & wsl.exe --status 2>&1 | Out-String
if ($wslStat -match "0x80370114" -or $wslStat -match "error" -or $wslStat -match "включена виртуализация") {
    Write-Host "`n[STOPPING] A machine reboot is required to activate the Windows Hypervisor driver." -ForegroundColor Red
    Write-Host "After rebooting, rerun: powershell -File `"$PSCommandPath`" -SkipBuild" -ForegroundColor Yellow
    exit 0
}

# 3. Initialize Ubuntu & install packages
& "$scriptsDir\03_initialize_ubuntu_environment.ps1"

# 4. Apply Linux Kernel Network Prison
& "$scriptsDir\04_lockdown_wsl_kernel_prison.ps1" -Port 1015

# 5. Register Auto-Isolation Daemon
& "$scriptsDir\05_register_auto_isolation_daemon.ps1"

# 6. Setup Vendor C Claude Code CLI (1015 & 1016)
& "$scriptsDir\06_setup_vendor_c_claude_cli.ps1"

# 7. Generate Isolated Chrome Instances
$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python.exe" }
Write-Host "=== [7/8] Generating Isolated Google Chrome Proxy Instances ===" -ForegroundColor Cyan
& $python "$scriptsDir\07_generate_chrome_instances.py"

# 8. Run Full Audit
& "$scriptsDir\08_run_full_isolation_audit.ps1" -Port 1015

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host "  Setup Complete! 100% Hermetic Zero-Leaks Perimeter Established   " -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green
