# Step 6: Setup Vendor C / Claude Code CLI isolated profiles with Killswitch (Ports 1015 and 1016)
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Write-Host "=== [6/8] Configuring Vendor C Claude Code CLI Environments (Ports 1015 & 1016) ===" -ForegroundColor Cyan

# 1. Verify Node.js and claude CLI
$claudeBin = Join-Path $env:APPDATA "npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
if (-not (Test-Path $claudeBin)) {
    Write-Host "[+] Installing @anthropic-ai/claude-code globally via npm..." -ForegroundColor Yellow
    npm install -g @anthropic-ai/claude-code
}

$python = "C:\proj\vless2socks\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python.exe" }

$proxyDir = "C:\MyFiles\Proxy"
$desktopDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
New-Item -ItemType Directory -Path "$proxyDir\ink" -Force | Out-Null
New-Item -ItemType Directory -Path "$proxyDir\ico" -Force | Out-Null

$profiles = @(
    @{
        Port = 1015
        Name = "Claude Code 1015"
        CliAlias = "CLI_1015"
        ConfigDir = ".claude-1"
        Title = "Claude Code CLI (Vendor C - Socket 1015)"
        WorkDir = "Vendor_C_1015"
        Color = "gold"
    },
    @{
        Port = 1016
        Name = "Claude Code 1016"
        CliAlias = "CLI_1016"
        ConfigDir = ".claude-secondary"
        Title = "Claude Code CLI (Vendor C - Socket 1016 Secondary)"
        WorkDir = "Vendor_C_1016"
        Color = "gold"
    }
)

$wsh = New-Object -ComObject WScript.Shell

foreach ($p in $profiles) {
    $port = $p.Port
    $configPath = Join-Path $env:USERPROFILE $p.ConfigDir
    $workPath = Join-Path $env:USERPROFILE "Downloads\$($p.WorkDir)"
    New-Item -ItemType Directory -Path $configPath -Force | Out-Null
    New-Item -ItemType Directory -Path $workPath -Force | Out-Null

    $batPath = Join-Path $proxyDir "Vendor_C_Socket_$port.bat"
    $legacyBatPath = Join-Path $proxyDir "Claude_Socket_$port.bat"

    # Deploy launcher batch file with Killswitch
    $batContent = @"
@echo off
chcp 65001 >nul
title $($p.Title)

set "PY=python"
if exist "C:\proj\vless2socks\.venv\Scripts\python.exe" set "PY=C:\proj\vless2socks\.venv\Scripts\python.exe"

REM 1. Pre-launch IP Verification and Killswitch Protection
"%PY%" "C:\MyFiles\vless2socks\new_claude\verify_and_notify.py" --port $port --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] Launch aborted: Killswitch blocked traffic leak!
    timeout /t 5 >nul
    exit /b 1
)

REM 2. Routing strictly via socket 127.0.0.1:$port
set HTTP_PROXY=http://127.0.0.1:$port
set HTTPS_PROXY=http://127.0.0.1:$port
set ALL_PROXY=http://127.0.0.1:$port
set http_proxy=http://127.0.0.1:$port
set https_proxy=http://127.0.0.1:$port
set all_proxy=http://127.0.0.1:$port
set NO_PROXY=

REM 3. Isolated configuration directory
set CLAUDE_CONFIG_DIR=$configPath
set CLAUDE_SECURESTORAGE_CONFIG_DIR=$configPath
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 4. Sync profile status with vless2socks
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port $port --quiet

cd /d "$workPath"
if not exist "$workPath" mkdir "$workPath"

"$claudeBin" --dangerously-skip-permissions %*

REM 5. Update profile status on exit
"%PY%" "C:\MyFiles\vless2socks\vendor_c_sync.py" --port $port --quiet
"@

    [System.IO.File]::WriteAllText($batPath, $batContent, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($legacyBatPath, $batContent, [System.Text.Encoding]::UTF8)
    Copy-Item $batPath "C:\proj\vless2socks\proxys\" -Force -ErrorAction SilentlyContinue

    Write-Host "[+] Deployed batch launchers for Port $port." -ForegroundColor Yellow

    # Shortcuts
    $icoPath = Join-Path $proxyDir "ico\claude_$($p.Color).ico"
    $shortcutTargets = @(
        (Join-Path $desktopDir "$($p.Name).lnk"),
        (Join-Path $desktopDir "$($p.CliAlias).lnk"),
        (Join-Path $proxyDir "ink\$($p.Name).lnk"),
        (Join-Path $proxyDir "ink\$($p.CliAlias).lnk"),
        (Join-Path "C:\proj\vless2socks\proxys\ink" "$($p.Name).lnk"),
        (Join-Path "C:\proj\vless2socks\proxys\ink" "$($p.CliAlias).lnk")
    )

    foreach ($sc in $shortcutTargets) {
        $parent = Split-Path $sc
        if (!(Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $lnk = $wsh.CreateShortcut($sc)
        $lnk.TargetPath = "C:\Windows\System32\cmd.exe"
        $lnk.Arguments = "/k `"$batPath`""
        $lnk.WorkingDirectory = $proxyDir
        if (Test-Path $icoPath) { $lnk.IconLocation = "$icoPath,0" }
        $lnk.Description = "$($p.Title)"
        $lnk.Save()
    }

    # Sync profile status
    & $python "C:\proj\vless2socks\vendor_c_sync.py" --port $port --quiet
}

Write-Host "[SUCCESS] Vendor C Claude Code CLI profiles 1015 & 1016 successfully deployed and isolated!" -ForegroundColor Green
