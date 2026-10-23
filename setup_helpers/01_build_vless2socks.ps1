# Step 1: Build vless2socks standalone release from source
# Requires Git and Python 3.10+
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = "C:\proj\vless2socks"
Write-Host "=== [1/8] Building vless2socks Release from Source ===" -ForegroundColor Cyan

if (-not (Test-Path $root)) {
    Write-Host "[+] Cloning repository to $root..." -ForegroundColor Yellow
    git clone https://github.com/captainmaclay/vless2socks $root
}

Set-Location $root

Write-Host "[+] Ensuring repository is on release tag v1.2.4 or main..." -ForegroundColor Yellow
if ((git tag -l "v1.2.4")) {
    git checkout v1.2.4
} else {
    git checkout main
}

# Virtual environment setup
$venvDir = Join-Path $root ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[+] Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $venvDir
}

Write-Host "[+] Installing dependencies..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt pefile pillow pyinstaller

# Download Xray binary
Write-Host "[+] Fetching Xray core binary..." -ForegroundColor Yellow
& $python "tools\get_xray.py"

# Build executable
Write-Host "[+] Running PyInstaller build..." -ForegroundColor Yellow
& $python -m PyInstaller --clean vless2socks.spec

# Ensure C:\MyFiles\vless2socks junction exists
$myFiles = "C:\MyFiles"
$junction = "C:\MyFiles\vless2socks"
if (-not (Test-Path $myFiles)) {
    New-Item -ItemType Directory -Path $myFiles -Force | Out-Null
}
if (-not (Test-Path $junction)) {
    Write-Host "[+] Creating NTFS Junction C:\MyFiles\vless2socks -> $root" -ForegroundColor Green
    New-Item -ItemType Junction -Path $junction -Target $root | Out-Null
}

Write-Host "[SUCCESS] vless2socks successfully built in $root\dist\vless2socks and aliased to C:\MyFiles\vless2socks" -ForegroundColor Green
