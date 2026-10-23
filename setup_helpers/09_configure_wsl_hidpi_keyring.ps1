# Step 9: Configure WSLg High-DPI Resolution & Eliminate Keyring Popups
# Author: Antigravity Engineering Pair-Programming Team
[CmdletBinding()]
param(
    [int]$ScaleFactor = 2,
    [int]$Dpi = 192
)

$ErrorActionPreference = "Stop"
Write-Host "=== [9/9] Configuring WSLg High-DPI Scaling & Eliminating Keyring Popups ===" -ForegroundColor Cyan

$wslgConfigPath = Join-Path $env:USERPROFILE ".wslgconfig"
$wslgContent = @"
# WSLg High-DPI Scaling Configuration for 2K/3K/4K Displays
[system-distro-env]
WESTON_RDP_DISABLE_FRACTIONAL_HI_DPI_SCALING=false
WESTON_RDP_DEBUG_DESKTOP_SCALING_FACTOR=$($ScaleFactor * 100)
"@

Write-Host "[+] Writing WSLg HiDPI configuration to $wslgConfigPath..." -ForegroundColor Yellow
[System.IO.File]::WriteAllText($wslgConfigPath, $wslgContent, [System.Text.Encoding]::UTF8)

# Distro detection
$distro = "Ubuntu-24.04"
$distroList = & wsl.exe -l -q 2>$null
if ($distroList -match "Ubuntu-24.04") {
    $distro = "Ubuntu-24.04"
} elseif ($distroList -match "Ubuntu") {
    $distro = "Ubuntu"
}

Write-Host "[+] Applying Linux environment variables and cleaning keyrings in $distro..." -ForegroundColor Yellow
$shScript = "C:\proj\vless2socks\new_claude\configure_wsl_hidpi_keyring.sh"
& wsl.exe -d $distro -u root bash -c "tr -d '\r' < /mnt/c/proj/vless2socks/new_claude/configure_wsl_hidpi_keyring.sh | bash"

Write-Host "[SUCCESS] WSLg High-DPI vector scaling ($($ScaleFactor * 100)%) and passwordless Keyring successfully configured!" -ForegroundColor Green
